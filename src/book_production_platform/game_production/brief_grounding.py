"""Deterministic brief facts for Game V1B layout proposals (REQ-GAM-01).

The model proposes a *layout*; the brief must state the facts that bound it:
the grid size (``W x H``) and the number of collectibles, plus any explicit
player or exit cell such as ``(1, 1)``. This module reads those facts from the
brief itself. A fact stated with hedging ("about", "หรือ") or with two
different values is ambiguous; an ambiguous or unstated critical fact can only
lead to ``needs_input``, never to a guess.

It also flags asks the registered template cannot honour (enemies, lava,
an exit sealed off by walls), so they are rejected before any spend instead of
being silently dropped from the layout.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from book_production_platform.brief_adapter_kit.grounding import normalize_text

_GRID = re.compile(r"(?<![\d.])(\d{1,4})\s*(?:x|×|by|\*)\s*(\d{1,4})(?![\d.])")
_COUNT = re.compile(
    r"(?<![\d.(,])(\d{1,4})\s*(?:collectibles?|coins?|gems?|stars?|items?|keys?|objectives?|pickups?"
    r"|เหรียญ|อัญมณี|ดาว|ไอเท็ม|ชิ้นของสะสม)"
    r"|(?:collectibles?|coins?|gems?|stars?|items?|keys?|objectives?|pickups?|เหรียญ|อัญมณี|ดาว|ไอเท็ม)"
    r"\s*(?:จำนวน\s*)?[:=]?\s*(\d{1,4})(?![\d.,)])(?!\s*(?:x|×|by|\*)\s*\d)"
)
_CELL = r"\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)"
_PLAYER = re.compile(rf"(?:player|spawn|start|ผู้เล่น|จุดเริ่ม)[^()\n]{{0,30}}?{_CELL}")
_EXIT = re.compile(rf"(?:exit|goal|ทางออก)[^()\n]{{0,30}}?{_CELL}")
_HEDGE = re.compile(
    r"\b(?:about|approx\w*|around|roughly|maybe|perhaps|or|tbd|tbc|unsure|guess|whatever|any)\b|~|±|\?"
    r"|ประมาณ|ราว|หรือ|น่าจะ|ไม่แน่ใจ|ไม่แน่|อะไรก็ได้|แล้วแต่|สัก"
)
_CLAUSE_BREAK = re.compile(r"[\n;]|\.(?!\d)")
UNSUPPORTED_ENTITIES = re.compile(
    r"\b(?:enem(?:y|ies)|monsters?|npcs?|lava|traps?|spikes?|boss(?:es)?|turrets?|weapons?|guns?|bullets?|teleporters?)\b"
    r"|ศัตรู|มอนสเตอร์|ลาวา|กับดัก|บอส|อาวุธ|หนาม|ปืน|วาร์ป"
)
UNREACHABLE_REQUEST = re.compile(
    r"\b(?:enclosed|surrounded|sealed|walled[\s-]+(?:in|off)|blocked[\s-]+off|unreachable|cannot\s+be\s+reached|no\s+path)\b"
    r"|ล้อม|ปิดตาย|ไปไม่ถึง|ไม่มีทางไป|ปิดทาง"
)


@dataclass
class GameFacts:
    """Critical facts a brief states; ``ambiguous`` names facts that must not be guessed."""

    grids: set[tuple[int, int]] = field(default_factory=set)
    counts: set[int] = field(default_factory=set)
    players: set[tuple[int, int]] = field(default_factory=set)
    exits: set[tuple[int, int]] = field(default_factory=set)
    ambiguous: set[str] = field(default_factory=set)

    @property
    def grid(self) -> tuple[int, int] | None:
        return next(iter(self.grids)) if len(self.grids) == 1 and "grid" not in self.ambiguous else None

    @property
    def collectibles(self) -> int | None:
        return next(iter(self.counts)) if len(self.counts) == 1 and "collectibles" not in self.ambiguous else None

    @property
    def player(self) -> tuple[int, int] | None:
        return next(iter(self.players)) if len(self.players) == 1 else None

    @property
    def exit(self) -> tuple[int, int] | None:
        return next(iter(self.exits)) if len(self.exits) == 1 else None

    def missing(self) -> list[str]:
        missing = [name for name, value in (("grid", self.grid), ("collectibles", self.collectibles)) if value is None]
        if len(self.players) > 1:
            missing.append("player")
        if len(self.exits) > 1:
            missing.append("exit")
        return missing


def analyze_brief(brief: str) -> GameFacts:
    facts = GameFacts()
    for clause in _CLAUSE_BREAK.split(normalize_text(brief)):
        hedged = bool(_HEDGE.search(clause))
        grids = {(int(width), int(height)) for width, height in _GRID.findall(clause)}
        counts = {int(first or second) for first, second in _COUNT.findall(clause)}
        if hedged and grids:
            facts.ambiguous.add("grid")
        if hedged and counts:
            facts.ambiguous.add("collectibles")
        facts.grids |= grids
        facts.counts |= counts
        facts.players |= {(int(x), int(y)) for x, y in _PLAYER.findall(clause)}
        facts.exits |= {(int(x), int(y)) for x, y in _EXIT.findall(clause)}
    return facts


def unsupported_request(brief: str) -> str | None:
    """Return a stable code when the brief asks for something the template cannot build."""

    text = normalize_text(brief)
    if UNSUPPORTED_ENTITIES.search(text):
        return "unsupported_entity_requested"
    if UNREACHABLE_REQUEST.search(text):
        return "unreachable_layout_requested"
    return None


__all__ = ["UNREACHABLE_REQUEST", "UNSUPPORTED_ENTITIES", "GameFacts", "analyze_brief", "unsupported_request"]
