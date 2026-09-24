from decimal import Decimal

import pytest

from book_production_platform.spreadsheet_ir import (
    CellIR,
    FormulaSpec,
    SheetIR,
    WorkbookIR,
)
from book_production_platform.spreadsheet_production.formulas import (
    FormulaError,
    evaluate_workbook,
)


def workbook(formulas: list[FormulaSpec]) -> WorkbookIR:
    return WorkbookIR(
        workbook_id="formula-001",
        title="Formula test",
        locale="en-US",
        sheets=[
            SheetIR(name="Detail", cells=[
                CellIR(address="Detail!A1", data_type="text", value="=SUM(A1:A2)"),
                CellIR(address="Detail!B1", data_type="decimal", value=Decimal("10.50")),
                CellIR(address="Detail!B2", data_type="decimal", value=Decimal("5.00")),
                CellIR(address="Detail!B3", data_type="null", value=None),
            ]),
            SheetIR(name="Summary", cells=[]),
        ],
        formulas=formulas,
        named_ranges={"detail_amounts": "Detail!B1:B3"},
    )


def test_evaluator_supports_sum_count_and_if_with_decimal_values() -> None:
    result = evaluate_workbook(workbook([
        FormulaSpec(target="Summary!B2", expression="=SUM(detail_amounts)", result_type="decimal"),
        FormulaSpec(target="Summary!B3", expression="=COUNT(Detail!B1:B3)", result_type="integer"),
        FormulaSpec(target="Summary!B4", expression="=IF(Summary!B2>10,1,0)", result_type="integer"),
    ]))

    assert result.values["Summary!B2"] == Decimal("15.50")
    assert result.values["Summary!B3"] == 2
    assert result.values["Summary!B4"] == 1


@pytest.mark.parametrize("expression", ["=AVERAGE(Detail!B1:B2)", "=SUM([external.xlsx]Sheet1!A1:A2)"])
def test_evaluator_rejects_unsupported_or_external_formula(expression: str) -> None:
    with pytest.raises(FormulaError, match="unsupported_function|external_reference_forbidden"):
        evaluate_workbook(workbook([FormulaSpec(target="Summary!B2", expression=expression)]))


def test_evaluator_rejects_broken_reference_and_cycle() -> None:
    with pytest.raises(FormulaError, match="broken_reference"):
        evaluate_workbook(workbook([FormulaSpec(target="Summary!B2", expression="=SUM(Detail!Z99:Z100)")]))
    with pytest.raises(FormulaError, match="formula_cycle"):
        evaluate_workbook(workbook([
            FormulaSpec(target="Summary!B2", expression="=SUM(Summary!B3:B3)"),
            FormulaSpec(target="Summary!B3", expression="=SUM(Summary!B2:B2)"),
        ]))
