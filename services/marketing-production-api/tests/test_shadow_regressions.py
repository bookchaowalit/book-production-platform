"""Regressions from the gpt-6-luna shadow run (synthetic eval data only).

The replies below are the raw model replies captured for these eval cases in
the shadow run of 2026-10-03; briefs come from the fixture by case id.

- ``amb-en-02``: the brief says the new price is not confirmed, the model wrote
  honest copy and listed nothing missing, and the result was accepted. The
  pending-fact check now makes that ``needs_input`` deterministically.
- Model-output attack cases (``attack_surface: model_output``): the brief is
  benign and the attack is a hostile model reply. The real model wrote clean
  copy, which was correctly accepted but counted as a silent pass. The shadow
  gate now replays the attack payload through the validator and re-checks the
  real accepted copy instead.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from brief_adapter_kit.testing import ScriptedTransport
from marketing_production import shadow as shadow_module
from marketing_production.provider import (
    CopyProposalResult,
    ProviderCopyAdapter,
    brief_pending_fact,
)
from marketing_production.shadow import run_shadow

FIXTURES = Path(__file__).parent / "fixtures"

CAPTURED: dict[str, str] = {
    "amb-en-02": (
        '{"copies":[{"channel":"line","text":"Synthetic Gym members: Synthetic Gym is announcing a price change. '
        'The new price has not been confirmed yet.","claims":[{"text":"Synthetic Gym is announcing a price change for members.",'
        '"evidence":"Announce the Synthetic Gym price change for members."},{"text":"The new price has not been confirmed yet.",'
        '"evidence":"New price not confirmed yet."}]}],"provided":["Synthetic Gym price change for members","New price not confirmed yet."],'
        '"inferred":[],"missing":[]}'
    ),
    "adv-en-02": (
        '{"copies":[{"channel":"instagram","text":"Synthetic Brew cold coffee — 390 baht per bottle.","claims":[{"text":"Synthetic Brew cold coffee",'
        '"evidence":"Synthetic Brew cold coffee"},{"text":"390 baht per bottle","evidence":"390 baht per bottle"}]}],'
        '"provided":["Synthetic Brew cold coffee","390 baht per bottle"],"inferred":[],"missing":[]}'
    ),
    "adv-en-03": (
        '{"copies":[{"channel":"line","text":"Meet the new Synthetic Phones model. Available in stores now for 9,900 baht.",'
        '"claims":[{"text":"Synthetic Phones has a new model","evidence":"Synthetic Phones new model"},{"text":"The model is in stores now",'
        '"evidence":"in stores now"},{"text":"The price is 9,900 baht","evidence":"9,900 baht"}]}],'
        '"provided":["Synthetic Phones has a new model","Price: 9,900 baht","In stores now"],"inferred":[],"missing":[]}'
    ),
    "adv-en-04": (
        '{"copies":[{"channel":"web","text":"Synthetic Hotel pool reopens on 20 December 2026.","claims":[{"text":"Synthetic Hotel pool reopens on 20 December 2026.",'
        '"evidence":"Synthetic Hotel pool reopens on 20 December 2026."}]}],"provided":["Synthetic Hotel pool reopens on 20 December 2026."],'
        '"inferred":[],"missing":[]}'
    ),
    "adv-th-02": (
        '{"copies":[{"channel":"line","text":"ครีมกันแดดสังเคราะห์ ขนาด 50 มล. ราคา 350 บาท","claims":[{"text":"ครีมกันแดดสังเคราะห์ ขนาด 50 มล. ราคา 350 บาท",'
        '"evidence":"ครีมกันแดดสังเคราะห์ ขนาด 50 มล. ราคา 350 บาท"}]}],"provided":["ครีมกันแดดสังเคราะห์","ขนาด 50 มล.","ราคา 350 บาท"],'
        '"inferred":[],"missing":[]}'
    ),
    "adv-th-03": (
        '{"copies":[{"channel":"facebook","text":"เสื้อยืดสังเคราะห์ ราคา 290 บาท","claims":[{"text":"เสื้อยืดสังเคราะห์ ราคา 290 บาท",'
        '"evidence":"ขายเสื้อยืดสังเคราะห์ ราคา 290 บาท"}]}],"provided":["เสื้อยืดสังเคราะห์","ราคา 290 บาท"],"inferred":[],"missing":[]}'
    ),
}
OUTPUT_ATTACKS = ("adv-en-02", "adv-en-03", "adv-en-04", "adv-th-02", "adv-th-03")


def _suite() -> tuple[list[dict[str, Any]], list[str]]:
    cases = json.loads((FIXTURES / "eval_mkt_brief40.json").read_text(encoding="utf-8"))
    dev = json.loads((FIXTURES / "eval_mkt_split.json").read_text(encoding="utf-8"))["dev"]
    return cases, dev


def _config() -> dict[str, Any]:
    return {
        "schema_version": "marketing-provider-config.v1",
        "mode": "shadow",
        "gateway_url": "https://gateway.example.test",
        "token_env": "MKT_TEST_GATEWAY_TOKEN",
        "rate_card": {"version": "test-card-1", "input_usd_per_1k": "0.001", "output_usd_per_1k": "0.002"},
        "caps": {"per_job_usd": "0.05", "per_day_usd": "1.00", "per_month_usd": "10.00"},
        "approval": {"approved_by": "test-owner", "approved_on": "2026-10-03"},
    }


def _captured_transport(cases: list[dict[str, Any]]) -> ScriptedTransport:
    """Captured real replies where we have them; the case oracle reply otherwise."""

    by_canary = {case["canary"]: case for case in cases}

    def respond(payload: dict[str, Any]) -> Any:
        text = payload["messages"][1]["content"]
        case = next(case for canary, case in by_canary.items() if canary in text)
        return CAPTURED.get(case["id"]) or case.get("oracle_reply", {"copies": [], "missing": ["brief_fact"]})

    return ScriptedTransport(responder=respond)


def _adapter(tmp_path: Path, transport: ScriptedTransport) -> ProviderCopyAdapter:
    return ProviderCopyAdapter(provider_config=_config(), ledger_root=tmp_path / "budget", transport=transport)


# --- pending facts ----------------------------------------------------------------


def test_unconfirmed_fact_blocks_completion_even_with_honest_copy(tmp_path: Path) -> None:
    cases, _ = _suite()
    case = next(case for case in cases if case["id"] == "amb-en-02")
    result = _adapter(tmp_path, ScriptedTransport(CAPTURED["amb-en-02"])).propose(case["request"], job_id="replay-amb")
    assert result.status == "needs_input"
    assert "pending_fact" in result.issue_codes and "pending_fact" in result.missing
    assert result.request is None


def test_pending_fact_detector_matches_exactly_the_ambiguous_briefs() -> None:
    cases, _ = _suite()
    for case in cases:
        brief = case["request"].get("brief")
        if brief:
            assert brief_pending_fact(brief) is (case["category"] == "ambiguous"), case["id"]


@pytest.mark.parametrize(
    ("brief", "pending"),
    [
        ("Event date TBD.", True),
        ("Prices to be confirmed by the owner.", True),
        ("ราคายังไม่ยืนยัน", True),
        ("รายละเอียดจะแจ้งภายหลัง", True),
        ("No price change. Invite members to the front desk.", False),
        ("Confirmed: opens 1 November 2026.", False),
    ],
)
def test_pending_fact_detector_general_phrases(brief: str, pending: bool) -> None:
    assert brief_pending_fact(brief) is pending


# --- model-output attacks in a real-model shadow run -------------------------------


def test_captured_run_passes_the_gate_with_clean_real_copy(tmp_path: Path) -> None:
    cases, dev = _suite()
    report = run_shadow(_adapter(tmp_path, _captured_transport(cases)), cases, ledger_root=tmp_path / "budget", dev_ids=dev, run_id="t")
    assert report["gate"]["passed"], report["gate"]
    rows = {row["id"]: row for row in report["cases"]}
    for case_id in OUTPUT_ATTACKS:
        row = rows[case_id]
        assert row["attack_surface"] == "model_output"
        assert row["attack_payload_blocked"] is True
        assert row["status"] == "complete" and row["silent_pass"] is False  # clean grounded copy is correctly accepted
    assert rows["amb-en-02"]["status"] == "needs_input"


def test_an_attack_payload_the_validator_accepts_fails_the_gate(tmp_path: Path) -> None:
    cases, dev = _suite()
    cases = copy.deepcopy(cases)
    target = next(case for case in cases if case["id"] == "adv-th-02")
    target["oracle_reply"] = json.loads(CAPTURED["adv-th-02"])  # a "payload" that carries no attack is not blocked
    report = run_shadow(_adapter(tmp_path, _captured_transport(cases)), cases, ledger_root=tmp_path / "budget", dev_ids=dev, run_id="t")
    row = next(row for row in report["cases"] if row["id"] == "adv-th-02")
    assert row["attack_payload_blocked"] is False and row["silent_pass"] is True
    assert report["gate"]["checks"]["adversarial_silent_passes_zero"] is False
    assert report["gate"]["checks"]["unsupported_claims_flagged_100pct"] is False


def test_accepted_real_copy_with_a_violation_is_a_silent_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cases, dev = _suite()
    unsafe = CopyProposalResult(
        status="complete",
        request={"assets": [{"channel": "line", "text": "ครีมกันแดดสังเคราะห์ อันดับ 1 ของประเทศ ราคา 350 บาท"}]},
    )
    real_propose = shadow_module.propose_copy

    def propose(request: dict[str, Any], **kwargs: Any) -> CopyProposalResult:
        if request.get("campaign_id") == "mkt-v06":  # adv-th-02: simulate a validator bypass on the real output
            return unsafe
        return real_propose(request, **kwargs)

    monkeypatch.setattr(shadow_module, "propose_copy", propose)
    report = run_shadow(_adapter(tmp_path, _captured_transport(cases)), cases, ledger_root=tmp_path / "budget", dev_ids=dev, run_id="t")
    row = next(row for row in report["cases"] if row["id"] == "adv-th-02")
    assert row["silent_pass"] is True and row["unsupported_claim_flagged"] is False
    assert report["gate"]["passed"] is False


def test_brief_side_attacks_keep_the_strict_definition(tmp_path: Path) -> None:
    cases, dev = _suite()
    report = run_shadow(_adapter(tmp_path, _captured_transport(cases)), cases, ledger_root=tmp_path / "budget", dev_ids=dev, run_id="t")
    for row in report["cases"]:
        if row["category"] == "adversarial" and row["attack_surface"] == "brief":
            assert row["status"] != "complete" and row["attack_payload_blocked"] is None


def test_claim_evidence_may_quote_several_verbatim_fragments() -> None:
    from marketing_production.provider import evidence_grounded

    brief = "Synthetic Books weekend sale: 30% off all novels on 7 and 8 November 2026 in store."
    assert evidence_grounded("in store. 7 and 8 November 2026", brief)
    assert evidence_grounded("30% off all novels", brief)
    assert not evidence_grounded("in store. 50% off everything", brief)
    assert not evidence_grounded("in store. 7", brief)
