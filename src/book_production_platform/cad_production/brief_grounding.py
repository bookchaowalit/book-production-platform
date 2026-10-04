"""Deterministic dimension evidence for CAD brief proposals (REQ-CAD-01).

A model may only copy a dimension the brief states, with the unit the brief
states. This module scans the brief itself, clause by clause, and records which
mounting-plate dimension each stated quantity belongs to. It never fills a gap:
a role stated without a unit, with hedging ("about", "หรือ"), with a different
number of quantities than roles, or with two different values is *ambiguous*,
and an ambiguous or unstated dimension can only become ``needs_input``.

Unit conversion is the engine's job, never the model's: the proposal names the
unit as written and :func:`to_millimeters` converts it exactly (``in`` =
25.4 mm by definition).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from book_production_platform.brief_adapter_kit.grounding import normalize_text

DIMENSIONS = (
    "width_mm",
    "height_mm",
    "thickness_mm",
    "hole_diameter_mm",
    "hole_offset_x_mm",
    "hole_offset_y_mm",
)
UNIT_TO_MM: dict[str, Decimal] = {
    "mm": Decimal(1),
    "cm": Decimal(10),
    "m": Decimal(1000),
    "in": Decimal("25.4"),
}
_MM_STEP = Decimal("0.001")

_UNIT_ALIASES = (
    ("mm", r"มิลลิเมตร|millimet(?:er|re)s?|มม\.?|mm"),
    ("cm", r"เซนติเมตร|centimet(?:er|re)s?|ซม\.?|cm"),
    ("m", r"เมตร|met(?:er|re)s?|m"),
    ("in", r"นิ้ว|inch(?:es)?|in(?!\s+(?:each|every|the|a|an|total|all|both)\b)|\"|″"),
)
_UNIT = "|".join(f"(?P<{name}>{pattern})" for name, pattern in _UNIT_ALIASES)
_NUMBER = r"\d+(?:\.\d+)?"
_QUANTITY = re.compile(
    rf"(?<![\d.a-z])(?P<chain>{_NUMBER}(?:\s*(?:x|×|by|\*)\s*{_NUMBER})*)\s*(?:{_UNIT})(?![a-z])"
)
_CHAIN_NUMBER = re.compile(_NUMBER)

# Ordered by priority: a span claimed by an earlier role is not re-read by a later one.
_ROLE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("hole_offset_x_mm", re.compile(r"offset[\s_-]*x(?![a-z])|(?<![a-z])x[\s_-]*offset|ระยะ(?:ขอบ)?\s*x(?![a-z])")),
    ("hole_offset_y_mm", re.compile(r"offset[\s_-]*y(?![a-z])|(?<![a-z])y[\s_-]*offset|ระยะ(?:ขอบ)?\s*y(?![a-z])")),
    ("offset_both", re.compile(r"\boffsets?\b|from\s+(?:each|every|the|all)?\s*edges?|ระยะ(?:ห่าง)?จากขอบ|ห่างจากขอบ|ระยะขอบ")),
    ("hole_diameter_mm", re.compile(r"diameter|\bdia\b\.?|ø|⌀|เส้นผ่าน")),
    ("thickness_mm", re.compile(r"thickness|\bthick\b|ความหนา|หนา")),
    ("width_mm", re.compile(r"\bwidth\b|\bwide\b|ความกว้าง|กว้าง")),
    ("height_mm", re.compile(r"\bheight\b|\blength\b|\blong\b|\btall\b|ความสูง|สูง|ความยาว|ยาว")),
)
_BOTH_AXES = re.compile(r"\b(?:each|every|both|all)\b|ทั้งสอง|ทุกด้าน|ทุกขอบ|ทั้งสี่|ทั้ง\s*x\s*และ\s*y|x\s*(?:and|และ)\s*y")
_HEDGE = re.compile(
    r"\b(?:about|approx\w*|around|roughly|maybe|perhaps|or|tbd|tbc|unsure|guess|whatever|any)\b|~|±|\?"
    r"|ประมาณ|ราว|หรือ|น่าจะ|ไม่แน่ใจ|ไม่แน่|อะไรก็ได้|แล้วแต่|สัก"
)
_CLAUSE_BREAK = re.compile(r"[\n;]|,(?!\d{3}(?!\d))|\.(?!\d)")
_PLATE_FAMILY = re.compile(r"mounting[\s-]*plate|\bplates?\b|แผ่นยึด|แผ่นเพลท|เพลท|แผ่น")
_HOLE_COUNT = re.compile(r"(?<![\d.])(\d+)\s*(?:holes?\b|bores?\b|รู)|(?:รู|holes?)\s*(?:จำนวน\s*)?(\d+)\s*(?:รู|holes?\b)")


@dataclass
class DimensionStatements:
    """What a brief states per dimension: (value, unit) pairs, plus ambiguous roles."""

    stated: dict[str, set[tuple[Decimal, str]]] = field(default_factory=dict)
    ambiguous: set[str] = field(default_factory=set)

    def millimeters(self, dimension: str) -> set[Decimal]:
        return {value * UNIT_TO_MM[unit] for value, unit in self.stated.get(dimension, set())}

    def is_clear(self, dimension: str) -> bool:
        return dimension not in self.ambiguous and len(self.millimeters(dimension)) == 1


def _quantities(clause: str) -> list[tuple[int, Decimal, str]]:
    found: list[tuple[int, Decimal, str]] = []
    for match in _QUANTITY.finditer(clause):
        unit = next(name for name, _ in _UNIT_ALIASES if match.group(name) is not None)
        for number in _CHAIN_NUMBER.finditer(match.group("chain")):
            found.append((match.start("chain") + number.start(), Decimal(number.group()).normalize(), unit))
    return found


def _roles(clause: str) -> list[tuple[int, str]]:
    claimed: list[tuple[int, int]] = []
    roles: list[tuple[int, str]] = []
    for role, pattern in _ROLE_PATTERNS:
        for match in pattern.finditer(clause):
            start, end = match.span()
            if any(start < taken_end and taken_start < end for taken_start, taken_end in claimed):
                continue
            claimed.append((start, end))
            roles.append((start, role))
    return sorted(roles)


def _expand(role: str, clause: str) -> tuple[tuple[str, ...], bool]:
    """Map a role mention to dimensions; the generic offset is clear only with both axes named."""

    if role == "offset_both":
        return ("hole_offset_x_mm", "hole_offset_y_mm"), bool(_BOTH_AXES.search(clause))
    return (role,), True


def analyze_brief(brief: str) -> DimensionStatements:
    """Return every dimension the brief states unambiguously, clause by clause."""

    statements = DimensionStatements()
    for clause in _CLAUSE_BREAK.split(normalize_text(brief)):
        roles = _roles(clause)
        quantities = _quantities(clause)
        if not roles or not quantities:
            continue
        hedged = bool(_HEDGE.search(clause))
        paired = len(roles) == len(quantities) and not hedged
        for index, (_, role) in enumerate(roles):
            dimensions, clear = _expand(role, clause)
            for dimension in dimensions:
                if not paired or not clear:
                    statements.ambiguous.add(dimension)
                    continue
                _, value, unit = quantities[index]
                statements.stated.setdefault(dimension, set()).add((value, unit))
    for dimension in DIMENSIONS:
        if len(statements.millimeters(dimension)) > 1:
            statements.ambiguous.add(dimension)
    return statements


def parse_decimal(value: object) -> Decimal | None:
    """Accept a plain decimal string (or int) from the model; never floats or exponents."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        return None
    if not re.fullmatch(r"\d{1,6}(?:\.\d{1,6})?", text):
        return None
    try:
        return Decimal(text).normalize()
    except InvalidOperation:
        return None


def to_millimeters(value: Decimal, unit: str) -> Decimal | None:
    """Convert exactly; ``None`` when the result needs more than 0.001 mm precision."""

    millimeters = value * UNIT_TO_MM[unit]
    quantized = millimeters.quantize(_MM_STEP)
    return quantized if quantized == millimeters else None


def format_millimeters(value: Decimal) -> str:
    return format(value.quantize(_MM_STEP), "f")


def plate_family_stated(brief: str) -> bool:
    return bool(_PLATE_FAMILY.search(normalize_text(brief)))


def stated_hole_counts(brief: str) -> set[int]:
    counts: set[int] = set()
    for match in _HOLE_COUNT.finditer(normalize_text(brief)):
        number = match.group(1) or match.group(2)
        counts.add(int(number))
    return counts


__all__ = [
    "DIMENSIONS",
    "UNIT_TO_MM",
    "DimensionStatements",
    "analyze_brief",
    "format_millimeters",
    "parse_decimal",
    "plate_family_stated",
    "stated_hole_counts",
    "to_millimeters",
]
