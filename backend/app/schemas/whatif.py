"""What-if request and response: up to 3 lever changes, each answered with a range."""

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.ml.whatif.scenarios import MAX_CHANGE_PCT

Lever = Literal["price", "discount", "volume", "cost"]
Scope = Literal["all", "category", "product"]


class ScenarioIn(BaseModel):
    """One lever change."""

    model_config = ConfigDict(extra="forbid")

    lever: Lever = Field(
        description="price (list price), discount (relative change of the average discount "
        "rate), volume (units at today's prices) or cost (unit cost)"
    )
    change_pct: float = Field(
        ge=-MAX_CHANGE_PCT,
        le=MAX_CHANGE_PCT,
        description="Change in percent, from -30 to +30 (larger changes are refused)",
    )
    scope: Scope = "all"
    target: str | None = Field(
        default=None, max_length=200, description="Category or product name for those scopes"
    )


class WhatIfRequest(BaseModel):
    """One to three scenarios, compared side by side."""

    model_config = ConfigDict(extra="forbid")

    scenarios: list[ScenarioIn] = Field(min_length=1, max_length=3)


class WhatIfOut(BaseModel):
    """Each scenario's yearly revenue and profit change as a range, with its assumptions."""

    dataset_id: uuid.UUID
    dataset_version_id: uuid.UUID
    version: int
    scenarios: list[dict[str, Any]] = Field(
        description="Per scenario: status, revenue_change and profit_change {low, expected, "
        "high} (null when there is not enough evidence), assumptions, warnings (including "
        "extrapolation risk), elasticity used, confidence, evidence SQL and a summary"
    )
    note: str
