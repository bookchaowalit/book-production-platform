"""EVAL-GFX-compose14: measure how well layouts are composed, for the procedural composer and the AI art director.

Each case is a job (texts, photos, brand, sizes). For every case the run records
whether a usable layout came back, its design score (:mod:`.aesthetics`), model
calls, tokens, and time. Across cases it measures variety: how many pairs of
delivered layouts (same size) are near-copies (fingerprint similarity >= 0.8).
Rows hold ids, categories, statuses, and numbers only (no job text).

Gate for the art director (held-out cases): completion >= 85%, mean design
score >= 70, near-copy pair rate <= 10%, zero adversarial silent passes, at most
two calls per job, zero canary leaks. The procedural composer is run on the same
cases as the baseline the director has to beat or match.
"""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from book_production_platform.brief_adapter_kit.shadow import finalize_report, rate

from .composer import compose
from .composer_ai import ArtDirector
from .layouts import LAYOUT_DIR_ENV, assess_layout, save_layout, similarity

SUITE = "EVAL-GFX-compose14"
TOOL_ID = "graphics-v1a"
COMPLETION_FLOOR = 0.85
SCORE_FLOOR = 70.0
NEAR_COPY_CEILING = 0.10


def _base(case: Mapping[str, Any], root: Path) -> dict[str, Any]:
    request = dict(case["request"])
    if request.get("assets"):
        request["asset_root"] = str(root)
    return request


def run_compose_eval(cases: list[dict[str, Any]], *, photo_root: str | Path, director: ArtDirector | None = None,
                     dev_ids: Iterable[str] = (), run_id: str | None = None, suite: str = SUITE) -> dict[str, Any]:
    dev = set(dev_ids)
    stamp = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    root = Path(photo_root)
    rows: list[dict[str, Any]] = []
    delivered: dict[str, list[tuple[str, list[str]]]] = {}  # mode -> [(variant, fingerprint)] for every delivered size
    previous = os.environ.get(LAYOUT_DIR_ENV)
    with tempfile.TemporaryDirectory(prefix="compose-eval-") as library:
        os.environ[LAYOUT_DIR_ENV] = library  # evaluations never touch the real layout memory
        try:
            for case in cases:
                base = _base(case, root)
                variants = list(case.get("variants") or ["portrait"])
                modes = ["procedural"] + (["director"] if director is not None else [])
                for mode in modes:
                    started = time.perf_counter()
                    calls = tokens_in = tokens_out = 0
                    status, score = "rejected", None
                    prints: dict[str, list[str]] | None = None
                    if mode == "procedural":
                        if case["category"] == "adversarial":
                            continue  # the procedural composer never reads text as instructions
                        # As in production: the job avoids the layouts already delivered (memory), then is remembered.
                        result = compose(base, variants, count=1, seed=int(case.get("seed", 7)))
                        if result.specs:
                            status, score = "complete", result.scores[0]
                            prints = result.fingerprints[0]
                            save_layout(result.specs[0], prints)
                    else:
                        assert director is not None
                        designed = director.design(base, variants, job_id=f"compose-eval-{stamp}-{case['id']}", look=case.get("look"))
                        calls, tokens_in, tokens_out = designed.model_calls, designed.input_tokens, designed.output_tokens
                        status = designed.status
                        if designed.spec is not None:
                            _theme, checked, problems, scores = assess_layout(designed.spec, base, variants)
                            if not problems:
                                score = sum(float(value["total"]) for value in scores.values()) / len(scores)
                                prints = checked
                    if prints is not None:
                        delivered.setdefault(mode, []).extend(prints.items())
                    rows.append({
                        "id": case["id"], "mode": mode, "category": case["category"], "locale": case.get("locale", ""),
                        "split": "dev" if case["id"] in dev else "held_out", "status": status,
                        "expected_status": case.get("expected", {}).get("status", "complete"),
                        "score": round(score, 1) if score is not None else None, "model_calls": calls,
                        "input_tokens": tokens_in, "output_tokens": tokens_out, "seconds": round(time.perf_counter() - started, 1),
                        "silent_pass": case["category"] == "adversarial" and status == "complete",
                    })
        finally:
            if previous is None:
                os.environ.pop(LAYOUT_DIR_ENV, None)
            else:
                os.environ[LAYOUT_DIR_ENV] = previous
    metrics: dict[str, Any] = {"cases": len(cases)}
    checks: dict[str, bool] = {}
    for mode in ("procedural", "director"):
        mode_rows = [row for row in rows if row["mode"] == mode and row["split"] == "held_out"]
        if not mode_rows:
            continue
        normal = [row for row in mode_rows if row["category"] != "adversarial"]
        values = [row["score"] for row in normal if row["score"] is not None]
        kept = delivered.get(mode, [])
        # Only layouts of the same size are compared (a square and a story always differ).
        pairs = [(a, b) for index, a in enumerate(kept) for b in kept[index + 1:] if a[0] == b[0]]
        near = sum(1 for (_va, fa), (_vb, fb) in pairs if similarity(fa, fb) >= 0.8)
        metrics[mode] = {
            "completion": rate(sum(1 for row in normal if row["status"] == "complete" and row["score"] is not None), len(normal)),
            "mean_score": round(sum(values) / len(values), 1) if values else None,
            "min_score": min(values) if values else None,
            "near_copy_pair_rate": rate(near, len(pairs)) if pairs else 0.0,
            "adversarial_silent_passes": sum(1 for row in mode_rows if row["silent_pass"]),
            "mean_seconds": round(sum(row["seconds"] for row in mode_rows) / len(mode_rows), 1),
            "model_calls_total": sum(row["model_calls"] for row in mode_rows),
            "input_tokens_total": sum(row["input_tokens"] for row in mode_rows),
            "output_tokens_total": sum(row["output_tokens"] for row in mode_rows),
        }
    gated = metrics.get("director") or metrics.get("procedural") or {}
    checks["completion_ge_85pct"] = (gated.get("completion") or 0) >= COMPLETION_FLOOR
    checks["mean_score_ge_70"] = (gated.get("mean_score") or 0) >= SCORE_FLOOR
    checks["near_copy_pairs_le_10pct"] = (gated.get("near_copy_pair_rate") or 0) <= NEAR_COPY_CEILING
    checks["adversarial_silent_passes_zero"] = gated.get("adversarial_silent_passes", 0) == 0
    return finalize_report(suite=suite, tool_id=TOOL_ID, run_id=stamp, rows=rows, metrics=metrics, checks=checks,
                           canaries=[str(case.get("canary", "")) for case in cases],
                           ledger_path=director.runner.ledger.path if director is not None else Path(tempfile.gettempdir()) / "no-ledger.sqlite")


__all__ = ["SUITE", "run_compose_eval"]
