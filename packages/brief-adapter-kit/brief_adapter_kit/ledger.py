"""Persistent, fail-closed call, token, and cost reservations for brief adapters.

One SQLite file holds two tables: per-job call/token reservations (at most two
model calls per job, bounded input and output tokens) and a cost ledger that
enforces per-job, per-day, and per-month USD caps atomically. Reservations are
never silently released; an unknown actual cost keeps the full reservation
charged.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

MAX_CALLS_PER_JOB = 2
MAX_INPUT_TOKENS = 8_000
MAX_OUTPUT_TOKENS = 2_000


class BudgetExceeded(RuntimeError):
    """Raised when a model step would exceed its bounded budget."""


MICRO_USD = Decimal(1000000)


def usd_to_micros(value: Decimal) -> int:
    """Convert a USD amount to whole micro-dollars, rounding up so caps stay conservative."""

    return int((value * MICRO_USD).to_integral_value(rounding=ROUND_CEILING))


def micros_to_usd(value: int) -> float:
    return float(Decimal(value) / MICRO_USD)


@dataclass(frozen=True)
class CostCaps:
    """Owner-approved spending caps in USD; all three are enforced atomically."""

    per_job_usd: Decimal
    per_day_usd: Decimal
    per_month_usd: Decimal


@dataclass(frozen=True)
class CostReservation:
    job_id: str
    reservation_id: int
    reserved_micros: int
    day: str
    month: str


@dataclass(frozen=True)
class BudgetReservation:
    job_id: str
    calls: int
    input_tokens: int
    output_tokens: int
    total_calls: int


class BudgetLedger:
    """SQLite-backed reservation ledger that serializes concurrent claims."""

    def __init__(
        self,
        root: str | Path,
        *,
        filename: str = "ai_budget.sqlite3",
        max_calls: int = MAX_CALLS_PER_JOB,
        max_input_tokens: int = MAX_INPUT_TOKENS,
        max_output_tokens: int = MAX_OUTPUT_TOKENS,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / filename
        self.max_calls = min(max_calls, MAX_CALLS_PER_JOB)
        self.max_input_tokens = min(max_input_tokens, MAX_INPUT_TOKENS)
        self.max_output_tokens = min(max_output_tokens, MAX_OUTPUT_TOKENS)
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_cost_ledger (
                    reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    day TEXT NOT NULL,
                    month TEXT NOT NULL,
                    rate_card_version TEXT NOT NULL,
                    reserved_micros INTEGER NOT NULL,
                    actual_micros INTEGER,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    settled INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS ai_cost_ledger_day ON ai_cost_ledger(day)")
            connection.execute("CREATE INDEX IF NOT EXISTS ai_cost_ledger_month ON ai_cost_ledger(month)")
        finally:
            connection.close()
    def reserve(self, job_id: str, *, calls: int, input_tokens: int, output_tokens: int) -> BudgetReservation:
        """Reserve attempts before an adapter call; reservations are never silently released."""

        if calls <= 0:
            raise BudgetExceeded("at least one model call must be reserved")
        if calls > self.max_calls:
            raise BudgetExceeded(f"a job may use at most {self.max_calls} model calls")
        if input_tokens > self.max_input_tokens:
            raise BudgetExceeded(f"input cannot exceed {self.max_input_tokens} tokens")
        if output_tokens > self.max_output_tokens:
            raise BudgetExceeded(f"output cannot exceed {self.max_output_tokens} tokens")
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
            if total_calls > self.max_calls:
                connection.rollback()
                raise BudgetExceeded(f"a job may use at most {self.max_calls} model calls")
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

    def reserve_cost(
        self,
        job_id: str,
        *,
        amount_usd: Decimal,
        caps: CostCaps,
        rate_card_version: str,
        now: datetime | None = None,
    ) -> CostReservation:
        """Atomically reserve a worst-case cost against per-job, per-day, and per-month caps.

        Unsettled reservations count at their reserved amount and settled ones at
        their actual amount, so concurrent dispatches can never overspend a cap.
        """

        if amount_usd <= 0:
            raise BudgetExceeded("a paid step must reserve a positive cost")
        moment = (now or datetime.now(UTC)).astimezone(UTC)
        day, month = moment.strftime("%Y-%m-%d"), moment.strftime("%Y-%m")
        amount = usd_to_micros(amount_usd)
        spent_sql = "SELECT COALESCE(SUM(CASE WHEN settled = 1 THEN actual_micros ELSE reserved_micros END), 0) FROM ai_cost_ledger WHERE "
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            for scope, column, value, cap in (
                ("job", "job_id", job_id, caps.per_job_usd),
                ("day", "day", day, caps.per_day_usd),
                ("month", "month", month, caps.per_month_usd),
            ):
                spent = int(connection.execute(spent_sql + f"{column} = ?", (value,)).fetchone()[0])
                if spent + amount > usd_to_micros(cap):
                    connection.rollback()
                    raise BudgetExceeded(f"the {scope} cost cap would be exceeded")
            cursor = connection.execute(
                """
                INSERT INTO ai_cost_ledger (job_id, day, month, rate_card_version, reserved_micros)
                VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, day, month, rate_card_version, amount),
            )
            connection.commit()
            return CostReservation(job_id, int(cursor.lastrowid or 0), amount, day, month)
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def settle_cost(
        self,
        reservation: CostReservation,
        *,
        actual_usd: Decimal | None,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> None:
        """Record the measured cost; an unknown cost keeps the full reservation charged."""

        actual = reservation.reserved_micros if actual_usd is None else usd_to_micros(actual_usd)
        connection = self._connect()
        try:
            connection.execute(
                """
                UPDATE ai_cost_ledger
                SET actual_micros = ?, input_tokens = ?, output_tokens = ?, settled = 1
                WHERE reservation_id = ? AND settled = 0
                """,
                (actual, input_tokens, output_tokens, reservation.reservation_id),
            )
        finally:
            connection.close()

    def spent_usd(self, *, day: str | None = None, month: str | None = None, job_id: str | None = None) -> float:
        """Return charged spend (actual when settled, reserved otherwise) for one scope."""

        column, value = ("job_id", job_id) if job_id is not None else ("day", day) if day is not None else ("month", month)
        if value is None:
            raise ValueError("one of day, month, or job_id is required")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT COALESCE(SUM(CASE WHEN settled = 1 THEN actual_micros ELSE reserved_micros END), 0) "
                f"FROM ai_cost_ledger WHERE {column} = ?",
                (value,),
            ).fetchone()
            return micros_to_usd(int(row[0]))
        finally:
            connection.close()
