"""Per-tenant daily token budget with race-free reservations.

Before each model call an estimate is reserved with one atomic statement that only succeeds while
the day's total stays within the budget, so concurrent streams cannot overspend. After the call the
reservation is settled to the real usage (which may be higher or lower than the estimate).
"""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

BUDGET_MESSAGE = (
    "This answer needs about {needed} tokens, but only {left} of your company's daily AI budget "
    "of {budget} tokens are left ({used} used today). The budget resets at midnight UTC; an "
    "administrator can raise AGENT_DAILY_TOKEN_BUDGET."
)


class BudgetExceededError(Exception):
    """The reservation would take the tenant past its daily token budget."""

    code = "budget_exceeded"

    def __init__(self, budget: int, used: int, needed: int = 0) -> None:
        """Keep the numbers for the message."""
        self.budget = budget
        self.used = used
        self.needed = needed
        left = max(0, budget - used)
        super().__init__(BUDGET_MESSAGE.format(budget=budget, used=used, needed=needed, left=left))
        self.message = str(self)


def today() -> date:
    """Return the current UTC day (budgets reset at midnight UTC)."""
    return datetime.now(UTC).date()


class TokenBudget:
    """Reserve and settle tokens for one tenant; each call commits on its own connection."""

    def __init__(self, session_factory: type[Session] | object, tenant_id: uuid.UUID, budget: int):
        """`session_factory()` must return a new Session (reservations commit independently)."""
        self._factory = session_factory
        self.tenant_id = tenant_id
        self.budget = budget

    def used(self) -> int:
        """Return tokens used (and reserved) today."""
        with self._factory() as db:  # type: ignore[operator]
            value = db.execute(
                text("SELECT tokens FROM agent_usage_daily WHERE tenant_id = :t AND day = :d"),
                {"t": self.tenant_id, "d": today()},
            ).scalar()
        return int(value or 0)

    def reserve(self, tokens: int) -> int:
        """Reserve `tokens` or raise BudgetExceededError; returns the reserved amount."""
        tokens = max(0, int(tokens))
        params = {
            "id": uuid.uuid4(),
            "t": self.tenant_id,
            "d": today(),
            "n": tokens,
            "b": self.budget,
        }
        with self._factory() as db:  # type: ignore[operator]
            db.execute(
                text(
                    "INSERT INTO agent_usage_daily (id, tenant_id, day, tokens) "
                    "VALUES (:id, :t, :d, 0) ON CONFLICT (tenant_id, day) DO NOTHING"
                ),
                params,
            )
            row = db.execute(
                text(
                    "UPDATE agent_usage_daily SET tokens = tokens + :n "
                    "WHERE tenant_id = :t AND day = :d AND tokens + :n <= :b RETURNING tokens"
                ),
                params,
            ).first()
            db.commit()
        if row is None:
            raise BudgetExceededError(self.budget, self.used(), tokens)
        return tokens

    def settle(self, reserved: int, actual: int) -> None:
        """Replace a reservation by the real usage (never below zero)."""
        delta = int(actual) - int(reserved)
        if delta == 0:
            return
        with self._factory() as db:  # type: ignore[operator]
            db.execute(
                text(
                    "UPDATE agent_usage_daily SET tokens = GREATEST(0, tokens + :n) "
                    "WHERE tenant_id = :t AND day = :d"
                ),
                {"n": delta, "t": self.tenant_id, "d": today()},
            )
            db.commit()
