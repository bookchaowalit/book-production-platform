"""Deterministic scripted fake adapter for the T8.1 brief-adapter test suite.

The adapter maps an exact brief text to a pre-decided ``ExtractionResult``.
It contains no intelligence, makes no model call, and never computes money:
for any brief it was not scripted for, it conservatively abstains with the
full required-field list instead of guessing. This is the reference behavior
the 60-case suite asserts against for ``needs_input`` and adversarial cases.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from document_production.extraction import (
    REQUIRED_FIELDS,
    ExtractionResult,
)


class ScriptedBriefAdapter:
    """Scripted ``brief-adapter.v1`` double: exact brief text to scripted outcome."""

    name = "scripted-fake"

    def __init__(self, outcomes: Mapping[str, ExtractionResult] | None = None) -> None:
        self._outcomes: dict[str, ExtractionResult] = dict(outcomes or {})
        self.calls: list[str] = []

    def register(self, brief: str, result: ExtractionResult) -> None:
        self._outcomes[brief] = result

    def extract(
        self,
        brief: str,
        *,
        locale: str,
        template_id: str = "invoice",
        template_version: str = "1",
        budget: Any | None = None,
    ) -> ExtractionResult:
        del locale, template_id, template_version, budget
        self.calls.append(brief)
        scripted = self._outcomes.get(brief)
        if scripted is not None:
            return scripted.model_copy(deep=True)
        return ExtractionResult(
            status="needs_input",
            adapter=self.name,
            missing=list(REQUIRED_FIELDS),
            model_calls=1,
            input_tokens=min(len(brief.split()), 8_000),
        )
