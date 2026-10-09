"""Persistent, fail-closed budget reservations for Documents V1B."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .extraction import MAX_INPUT_TOKENS, MAX_OUTPUT_TOKENS

MAX_CALLS_PER_JOB = 2


class BudgetExceeded(RuntimeError):
    """Raised when a V1B extraction would exceed its bounded budget."""


@dataclass(frozen=True)
class BudgetReservation:
    job_id: str
    calls: int
    input_tokens: int
    output_tokens: int
    total_calls: int


class BudgetLedger:
    """SQLite-backed reservation ledger that serializes concurrent claims."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "ai_budget.sqlite3"
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_budget_usage (
                    job_id TEXT PRIMARY KEY,
                    reserved_calls INTEGER NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL
                )
                """
            )
        finally:
            connection.close()
    def reserve(self, job_id: str, *, calls: int, input_tokens: int, output_tokens: int) -> BudgetReservation:
        """Reserve attempts before an adapter call; reservations are never silently released."""

        if calls <= 0:
            raise BudgetExceeded("at least one model call must be reserved")
        if calls > MAX_CALLS_PER_JOB:
            raise BudgetExceeded("a V1B job may use at most 2 model calls")
        if input_tokens > MAX_INPUT_TOKENS:
            raise BudgetExceeded("input extraction cannot exceed 8000 tokens")
        if output_tokens > MAX_OUTPUT_TOKENS:
            raise BudgetExceeded("output extraction cannot exceed 2000 tokens")
        if input_tokens < 0 or output_tokens < 0:
            raise BudgetExceeded("token counts cannot be negative")

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT reserved_calls, input_tokens, output_tokens FROM ai_budget_usage WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            existing_calls = int(row["reserved_calls"]) if row is not None else 0
            total_calls = existing_calls + calls
            if total_calls > MAX_CALLS_PER_JOB:
                connection.rollback()
                raise BudgetExceeded("a V1B job may use at most 2 model calls")
            existing_input = int(row["input_tokens"]) if row is not None else 0
            existing_output = int(row["output_tokens"]) if row is not None else 0
            if row is None:
                connection.execute(
                    "INSERT INTO ai_budget_usage (job_id, reserved_calls, input_tokens, output_tokens) VALUES (?, ?, ?, ?)",
                    (job_id, calls, input_tokens, output_tokens),
                )
            else:
                connection.execute(
                    """
                    UPDATE ai_budget_usage
                    SET reserved_calls = ?, input_tokens = ?, output_tokens = ?
                    WHERE job_id = ?
                    """,
                    (total_calls, existing_input + input_tokens, existing_output + output_tokens, job_id),
                )
            connection.commit()
            return BudgetReservation(job_id, calls, input_tokens, output_tokens, total_calls)
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
