from concurrent.futures import ThreadPoolExecutor

import pytest
from document_production.budget import BudgetExceeded, BudgetLedger


def test_budget_ledger_enforces_two_call_and_token_caps(tmp_path):
    ledger = BudgetLedger(tmp_path)

    reservation = ledger.reserve("job-1", calls=1, input_tokens=100, output_tokens=50)

    assert reservation.job_id == "job-1"
    assert reservation.calls == 1
    with pytest.raises(BudgetExceeded, match="2 model calls"):
        ledger.reserve("job-1", calls=2, input_tokens=100, output_tokens=50)
    with pytest.raises(BudgetExceeded, match="8000 tokens"):
        ledger.reserve("job-2", calls=1, input_tokens=8001, output_tokens=10)
    with pytest.raises(BudgetExceeded, match="2000 tokens"):
        ledger.reserve("job-3", calls=1, input_tokens=10, output_tokens=2001)


def test_budget_ledger_serializes_concurrent_reservations(tmp_path):
    def reserve(index: int) -> str:
        try:
            BudgetLedger(tmp_path).reserve("same-job", calls=1, input_tokens=10, output_tokens=10)
            return f"ok-{index}"
        except BudgetExceeded:
            return f"blocked-{index}"

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(reserve, range(3)))

    assert len([result for result in results if result.startswith("ok-")]) == 2
    assert len([result for result in results if result.startswith("blocked-")]) == 1
