"""Allowlisted, deterministic workbook formula evaluator."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from book_production_platform.spreadsheet_ir import WorkbookIR

Scalar = str | int | Decimal | date | bool | None
_CALL_RE = re.compile(r"^(SUM|COUNT|IF)\((.*)\)$", re.IGNORECASE | re.DOTALL)
_REF_RE = re.compile(r"^(?:(?P<sheet>[A-Za-z_][A-Za-z0-9_]*)!)?(?P<coord>\$?[A-Z]{1,3}\$?[1-9][0-9]*)(?::(?:(?P<end_sheet>[A-Za-z_][A-Za-z0-9_]*)!)?(?P<end_coord>\$?[A-Z]{1,3}\$?[1-9][0-9]*))?$")


class FormulaError(ValueError):
    """Raised when a formula is outside the V1A safe language."""


@dataclass(frozen=True)
class EvaluationResult:
    values: dict[str, Scalar]
    dependencies: dict[str, tuple[str, ...]]


def _split_args(body: str) -> list[str]:
    args: list[str] = []
    start = 0
    depth = 0
    quoted = False
    for index, char in enumerate(body):
        if char == '"':
            quoted = not quoted
        elif not quoted and char == "(":
            depth += 1
        elif not quoted and char == ")":
            depth -= 1
        elif not quoted and char == "," and depth == 0:
            args.append(body[start:index].strip())
            start = index + 1
    args.append(body[start:].strip())
    return args


def _parse_call(expression: str) -> tuple[str, list[str]]:
    value = expression.strip()
    if not value.startswith("="):
        raise FormulaError("formula_must_start_with_equals")
    match = _CALL_RE.fullmatch(value[1:].strip())
    if match is None:
        function_match = re.match(r"^([A-Za-z]+)", value[1:].strip())
        if function_match:
            raise FormulaError("unsupported_function")
        raise FormulaError("invalid_formula")
    return match.group(1).upper(), _split_args(match.group(2))


def _column_number(value: str) -> int:
    number = 0
    for char in value.replace("$", ""):
        number = number * 26 + ord(char) - ord("A") + 1
    return number


def _column_name(number: int) -> str:
    output = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        output = chr(ord("A") + remainder) + output
    return output


class _Evaluator:
    def __init__(self, workbook: WorkbookIR) -> None:
        self.workbook = workbook
        self.base: dict[str, Scalar] = {}
        self.formulas = {formula.target: formula for formula in workbook.formulas}
        self.cache: dict[str, Scalar] = {}
        self.state: dict[str, str] = {}
        self.dependencies: dict[str, tuple[str, ...]] = {}
        self.sheet_names = {sheet.name for sheet in workbook.sheets}
        for sheet in workbook.sheets:
            for cell in sheet.cells:
                if cell.address in self.formulas:
                    if cell.value is not None or cell.data_type not in {"null", "formula"}:
                        raise FormulaError("formula_target_conflict")
                    continue
                if cell.address in self.base or cell.address in self.formulas:
                    raise FormulaError("duplicate_cell_address")
                if cell.data_type == "formula":
                    raise FormulaError("formula_cell_requires_formula_spec")
                self.base[cell.address] = cell.value

    def evaluate(self) -> EvaluationResult:
        for target in self.formulas:
            self._resolve_cell(target)
        return EvaluationResult(values=dict(self.cache), dependencies=dict(self.dependencies))

    def _resolve_cell(self, reference: str, *, current_sheet: str | None = None) -> Scalar:
        key = self._resolve_ref(reference, current_sheet=current_sheet)
        if key in self.cache:
            return self.cache[key]
        if key in self.formulas:
            if self.state.get(key) == "visiting":
                raise FormulaError("formula_cycle")
            self.state[key] = "visiting"
            formula = self.formulas[key]
            self._validate_expression(formula.expression, key.split("!", 1)[0])
            value, dependencies = self._eval_expression(formula.expression, key.split("!", 1)[0])
            value = self._coerce_result(value, formula.result_type)
            self.dependencies[key] = tuple(dict.fromkeys(dependencies))
            self.cache[key] = value
            self.state[key] = "done"
            return value
        if key in self.base:
            return self.base[key]
        raise FormulaError("broken_reference")

    def _resolve_ref(self, reference: str, *, current_sheet: str | None = None) -> str:
        token = reference.strip().replace("$", "")
        if any(value in token for value in ("[", "]", "://")):
            raise FormulaError("external_reference_forbidden")
        if "!" in token:
            sheet, coord = token.split("!", 1)
        else:
            if current_sheet is None:
                raise FormulaError("sheet_required")
            sheet, coord = current_sheet, token
        if sheet not in self.sheet_names or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", sheet) or not re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]*", coord):
            raise FormulaError("broken_reference")
        return f"{sheet}!{coord}"

    def _range_keys(self, token: str, current_sheet: str) -> list[str]:
        if any(value in token for value in ("[", "]", "://")):
            raise FormulaError("external_reference_forbidden")
        named = self.workbook.named_ranges.get(token.strip())
        if named is not None:
            return self._range_keys(named, current_sheet)
        match = _REF_RE.fullmatch(token.strip())
        if match is None:
            raise FormulaError("broken_reference")
        sheet = match.group("sheet") or current_sheet
        end_sheet = match.group("end_sheet") or sheet
        if sheet != end_sheet:
            raise FormulaError("cross_sheet_range_forbidden")
        start_coord = match.group("coord").replace("$", "")
        end_coord = (match.group("end_coord") or start_coord).replace("$", "")
        start_match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)", start_coord)
        end_match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)", end_coord)
        if start_match is None or end_match is None:
            raise FormulaError("broken_reference")
        min_col, max_col = sorted((_column_number(start_match.group(1)), _column_number(end_match.group(1))))
        min_row, max_row = sorted((int(start_match.group(2)), int(end_match.group(2))))
        keys = [f"{sheet}!{_column_name(column)}{row}" for row in range(min_row, max_row + 1) for column in range(min_col, max_col + 1)]
        if any(key not in self.base and key not in self.formulas for key in keys):
            raise FormulaError("broken_reference")
        return keys

    def _validate_expression(self, expression: str, current_sheet: str) -> None:
        function, args = _parse_call(expression)
        if function in {"SUM", "COUNT"}:
            if len(args) != 1:
                raise FormulaError("invalid_formula_arity")
            self._range_keys(args[0], current_sheet)
            return
        if len(args) != 3:
            raise FormulaError("invalid_formula_arity")
        condition = args[0]
        comparator = re.match(r"^(.+?)\s*(>=|<=|<>|=|>|<)\s*(.+)$", condition)
        if comparator is None:
            self._validate_scalar(condition, current_sheet)
        else:
            self._validate_scalar(comparator.group(1), current_sheet)
            self._validate_scalar(comparator.group(3), current_sheet)
        for branch in args[1:]:
            if branch.startswith("="):
                self._validate_expression(branch, current_sheet)
            else:
                self._validate_scalar(branch, current_sheet)

    def _validate_scalar(self, token: str, current_sheet: str) -> None:
        value = token.strip()
        if not value or value.startswith('"'):
            return
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*![A-Z]{1,3}[1-9][0-9]*|[A-Z]{1,3}[1-9][0-9]*", value):
            self._resolve_cell(value, current_sheet=current_sheet)
            return
        if value in self.workbook.named_ranges:
            self._range_keys(value, current_sheet)
            return
        try:
            Decimal(value)
        except InvalidOperation:
            if value.upper() not in {"TRUE", "FALSE"}:
                raise FormulaError("invalid_scalar")

    def _eval_expression(self, expression: str, current_sheet: str) -> tuple[Scalar, list[str]]:
        function, args = _parse_call(expression)
        dependencies: list[str] = []
        if function in {"SUM", "COUNT"}:
            keys = self._range_keys(args[0], current_sheet)
            values = [self._resolve_cell(key, current_sheet=current_sheet) for key in keys]
            dependencies.extend(keys)
            if function == "COUNT":
                return sum(isinstance(value, (int, Decimal)) and not isinstance(value, bool) for value in values), dependencies
            total = Decimal(0)
            for value in values:
                if value is not None and isinstance(value, (int, Decimal)) and not isinstance(value, bool):
                    total += Decimal(str(value))
            return total, dependencies
        condition = args[0]
        comparator = re.match(r"^(.+?)\s*(>=|<=|<>|=|>|<)\s*(.+)$", condition)
        if comparator is None:
            condition_value, condition_dependencies = self._eval_scalar(condition, current_sheet)
            condition_result = bool(condition_value)
            dependencies.extend(condition_dependencies)
        else:
            left, left_dependencies = self._eval_scalar(comparator.group(1), current_sheet)
            right, right_dependencies = self._eval_scalar(comparator.group(3), current_sheet)
            dependencies.extend(left_dependencies + right_dependencies)
            condition_result = self._compare(left, right, comparator.group(2))
        branch = args[1] if condition_result else args[2]
        if branch.startswith("="):
            value, branch_dependencies = self._eval_expression(branch, current_sheet)
        else:
            value, branch_dependencies = self._eval_scalar(branch, current_sheet)
        dependencies.extend(branch_dependencies)
        return value, dependencies

    def _eval_scalar(self, token: str, current_sheet: str) -> tuple[Scalar, list[str]]:
        value = token.strip()
        if value.startswith('"') and value.endswith('"'):
            return value[1:-1].replace('""', '"'), []
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*![A-Z]{1,3}[1-9][0-9]*|[A-Z]{1,3}[1-9][0-9]*", value):
            key = self._resolve_ref(value, current_sheet=current_sheet)
            return self._resolve_cell(key), [key]
        if value.upper() in {"TRUE", "FALSE"}:
            return value.upper() == "TRUE", []
        try:
            number = Decimal(value)
        except InvalidOperation as exc:
            raise FormulaError("invalid_scalar") from exc
        return number, []

    @staticmethod
    def _compare(left: Scalar, right: Scalar, operator: str) -> bool:
        if operator == "=":
            return left == right
        if operator == "<>":
            return left != right
        try:
            if operator == ">":
                return bool(left is not None and right is not None and left > right)  # type: ignore[operator]
            if operator == "<":
                return bool(left is not None and right is not None and left < right)  # type: ignore[operator]
            if operator == ">=":
                return bool(left is not None and right is not None and left >= right)  # type: ignore[operator]
            if operator == "<=":
                return bool(left is not None and right is not None and left <= right)  # type: ignore[operator]
        except TypeError as exc:
            raise FormulaError("incomparable_values") from exc
        raise FormulaError("invalid_comparator")

    @staticmethod
    def _coerce_result(value: Scalar, result_type: str) -> Scalar:
        if result_type == "integer":
            if isinstance(value, (bool, date)) or value is None:
                raise FormulaError("formula_result_type_mismatch")
            try:
                return int(value)
            except (TypeError, ValueError) as exc:
                raise FormulaError("formula_result_type_mismatch") from exc
        if result_type == "decimal":
            if value is None or isinstance(value, bool):
                raise FormulaError("formula_result_type_mismatch")
            try:
                return Decimal(str(value))
            except (InvalidOperation, ValueError) as exc:
                raise FormulaError("formula_result_type_mismatch") from exc
        if result_type == "boolean" and not isinstance(value, bool):
            raise FormulaError("formula_result_type_mismatch")
        if result_type == "text" and not isinstance(value, str):
            raise FormulaError("formula_result_type_mismatch")
        return value


def evaluate_workbook(workbook: WorkbookIR) -> EvaluationResult:
    """Evaluate every formula through the bounded V1A language."""

    return _Evaluator(workbook).evaluate()


__all__ = ["EvaluationResult", "FormulaError", "evaluate_workbook"]
