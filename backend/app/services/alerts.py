"""Alert rules: compare a finished analysis run with the previous one of the same dataset.

Every rule reads two stored run summaries (see `run_summary`), states the numbers and the periods
it compared, and never claims a cause. The dedupe key makes a rule fire at most once per subject
(a month, a day, a version, a data snapshot), so re-running the same analysis repeats nothing.

Revenue drops are seasonality-aware: when the previous run stored a monthly forecast for the new
month, the actual is compared with that forecast's 80% and 95% ranges. Without one, the month is
compared with the month before it and the text says seasonality is not accounted for.
"""

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.repository import (
    FINISHED_RUN_STATES,
    AlertRepository,
    AlertSettingsRepository,
    AnalysisRunRepository,
    DatasetRepository,
    DatasetVersionRepository,
)
from app.models.report import AlertSettings
from app.services.run_summary import SUMMARY_SCHEMA, from_profile_last_date

logger = structlog.get_logger()

Severity = Literal["info", "warning", "critical"]
MAX_EVENT_ALERTS = 5
MAX_NAMES = 10
NO_SEASONALITY = "Seasonality is not accounted for."
METRIC_LABEL = {"net_revenue": "Net revenue", "orders": "Orders"}


@dataclass(frozen=True)
class Thresholds:
    """A company's alert thresholds (saved values or the defaults from the settings)."""

    revenue_drop_pct: float
    orders_drop_pct: float
    churn_increase_pct: float
    quality_drop_points: int
    freshness_enabled: bool
    freshness_days: int

    @classmethod
    def defaults(cls, settings: Settings) -> "Thresholds":
        """Return the defaults from the environment."""
        return cls(
            revenue_drop_pct=settings.alert_revenue_drop_pct,
            orders_drop_pct=settings.alert_orders_drop_pct,
            churn_increase_pct=settings.alert_churn_increase_pct,
            quality_drop_points=settings.alert_quality_drop_points,
            freshness_enabled=False,
            freshness_days=settings.alert_freshness_days,
        )

    @classmethod
    def of(cls, row: AlertSettings) -> "Thresholds":
        """Return the thresholds a company saved."""
        return cls(
            revenue_drop_pct=row.revenue_drop_pct,
            orders_drop_pct=row.orders_drop_pct,
            churn_increase_pct=row.churn_increase_pct,
            quality_drop_points=row.quality_drop_points,
            freshness_enabled=row.freshness_enabled,
            freshness_days=row.freshness_days,
        )


@dataclass(frozen=True)
class Draft:
    """An alert a rule wants to raise (stored only if its dedupe key is new)."""

    rule: str
    severity: Severity
    title: str
    body: str
    dedupe_key: str
    payload: dict[str, Any] = field(default_factory=dict)


def load_thresholds(db: Session, tenant_id: uuid.UUID) -> Thresholds:
    """Return the tenant's saved thresholds, or the defaults."""
    row = AlertSettingsRepository(db, tenant_id).current()
    return Thresholds.of(row) if row else Thresholds.defaults(get_settings())


# ---- formatting -----------------------------------------------------------------------------


def _num(value: float) -> str:
    return f"{value:,.0f}" if abs(value) >= 100 else f"{value:,.2f}"


def _money(value: float, currency: str | None) -> str:
    return f"{currency} {_num(value)}" if currency else _num(value)


def _month(period: str) -> str:
    return date.fromisoformat(period[:10]).strftime("%b %Y")


def _pct(new: float, old: float) -> float | None:
    return None if not old else 100.0 * (new - old) / abs(old)


def _by_drop(change: float, threshold: float) -> Severity | None:
    """Warning past the threshold, critical past twice the threshold."""
    if change <= -2 * threshold:
        return "critical"
    if change <= -threshold:
        return "warning"
    return None


def _by_rise(change: float, threshold: float) -> Severity | None:
    if change >= 2 * threshold:
        return "critical"
    if change >= threshold:
        return "warning"
    return None


# ---- rules ----------------------------------------------------------------------------------


def _previous_month(monthly: list[dict[str, Any]], period: str) -> dict[str, Any] | None:
    earlier = [m for m in monthly if m["period"] < period and not m.get("partial")]
    return earlier[-1] if earlier else None


def _new_month(prev: dict[str, Any], cur: dict[str, Any]) -> dict[str, Any] | None:
    """Return the new run's latest complete month if it is later than the previous run's."""
    new, old = cur.get("latest_month"), prev.get("latest_month")
    if not new or (old and new["period"] <= old["period"]):
        return None
    return dict(new)


def _currency(cur: dict[str, Any]) -> str | None:
    return ((cur.get("kpis") or {}).get("currency")) or None


def revenue_drop(
    dataset_id: str, prev: dict[str, Any], cur: dict[str, Any], t: Thresholds
) -> list[Draft]:
    """Net revenue of the new month below the previous run's forecast (or the month before)."""
    month = _new_month(prev, cur)
    if month is None or month.get("net_revenue") is None:
        return []
    forecast = (prev.get("forecast") or {}).get("periods", {}).get(month["period"])
    if forecast is None:
        return _month_over_month("net_revenue", dataset_id, month, cur, t.revenue_drop_pct)
    actual, currency = float(month["net_revenue"]), _currency(cur)
    severity: Severity | None = None
    if actual < forecast["lo95"]:
        severity, level, lo, hi = "critical", 95, forecast["lo95"], forecast["hi95"]
    elif actual < forecast["lo80"]:
        severity, level, lo, hi = "warning", 80, forecast["lo80"], forecast["hi80"]
    if severity is None:
        return []
    made = (prev.get("forecast") or {}).get("made_through")
    label = _month(month["period"])
    body = (
        f"Net revenue in {label} was {_money(actual, currency)}, below the {level}% range of the "
        f"forecast ({_money(lo, currency)} to {_money(hi, currency)}; expected "
        f"{_money(forecast['point'], currency)})"
        + (f" made from data up to {_month(made)}." if made else ".")
    )
    return [
        Draft(
            rule="revenue_drop",
            severity=severity,
            title=f"Net revenue in {label} was below the forecast range",
            body=body,
            dedupe_key=f"revenue_drop:{dataset_id}:{month['period'][:7]}",
            payload={
                "metric": "net_revenue",
                "method": "forecast",
                "period": month["period"],
                "current": actual,
                "expected": forecast["point"],
                "range_level": level,
                "range_low": lo,
                "range_high": hi,
                "forecast_made_through": made,
                "currency": currency,
                "link": {"page": "dashboard", "dataset_id": dataset_id},
            },
        )
    ]


def _month_over_month(
    metric: str, dataset_id: str, month: dict[str, Any], cur: dict[str, Any], threshold: float
) -> list[Draft]:
    before = _previous_month(cur.get("monthly") or [], month["period"])
    if before is None or before.get(metric) is None or month.get(metric) is None:
        return []
    actual, prior = float(month[metric]), float(before[metric])
    change = _pct(actual, prior)
    if change is None or prior <= 0:
        return []
    severity = _by_drop(change, threshold)
    if severity is None:
        return []
    currency = _currency(cur) if metric == "net_revenue" else None
    fmt = (lambda v: _money(v, currency)) if metric == "net_revenue" else _num
    label, prior_label, name = (
        _month(month["period"]),
        _month(before["period"]),
        METRIC_LABEL[metric],
    )
    rule = "revenue_drop" if metric == "net_revenue" else "orders_drop"
    return [
        Draft(
            rule=rule,
            severity=severity,
            title=f"{name} in {label} fell {abs(change):.1f}% from {prior_label}",
            body=(
                f"{name} in {label} was {fmt(actual)}, {abs(change):.1f}% lower than "
                f"{prior_label} ({fmt(prior)}). {NO_SEASONALITY}"
            ),
            dedupe_key=f"{rule}:{dataset_id}:{month['period'][:7]}",
            payload={
                "metric": metric,
                "method": "month_over_month",
                "period": month["period"],
                "previous_period": before["period"],
                "current": actual,
                "previous": prior,
                "change_pct": round(change, 2),
                "threshold_pct": threshold,
                "seasonality_accounted": False,
                "currency": currency,
                "link": {"page": "dashboard", "dataset_id": dataset_id},
            },
        )
    ]


def orders_drop(
    dataset_id: str, prev: dict[str, Any], cur: dict[str, Any], t: Thresholds
) -> list[Draft]:
    """Orders of the new month vs the month before (no orders forecast is stored)."""
    month = _new_month(prev, cur)
    if month is None:
        return []
    return _month_over_month("orders", dataset_id, month, cur, t.orders_drop_pct)


def unusual_days(
    dataset_id: str, prev: dict[str, Any], cur: dict[str, Any], t: Thresholds
) -> list[Draft]:
    """Unusual days that fall after the previous run's data and were not reported before."""
    last = prev.get("last_data_date")
    if not last:
        return []
    seen = {(e.get("start"), e.get("kind")) for e in prev.get("unusual_days") or []}
    fresh = [
        e
        for e in cur.get("unusual_days") or []
        if e.get("start")
        and str(e["start"])[:10] > last[:10]
        and (e["start"], e.get("kind")) not in seen
    ]
    fresh.sort(key=lambda e: abs(float(e.get("change") or 0)), reverse=True)
    currency = _currency(cur)
    out = []
    for e in fresh[:MAX_EVENT_ALERTS]:
        start, end = str(e["start"])[:10], str(e.get("end") or e["start"])[:10]
        when = start if start == end else f"{start} to {end}"
        kind = "drop" if e.get("kind") == "drop" else "spike"
        actual, expected = float(e.get("actual") or 0), float(e.get("expected") or 0)
        out.append(
            Draft(
                rule="new_unusual_days",
                severity="warning" if kind == "drop" else "info",
                title=f"Unusual {kind} in net revenue on {when}",
                body=(
                    f"Net revenue on {when} was {_money(actual, currency)}, against a usual level "
                    f"of {_money(expected, currency)} for that period. The data after "
                    f"{last[:10]} is new since the previous analysis."
                ),
                dedupe_key=f"new_unusual_days:{dataset_id}:{start}:{kind}",
                payload={
                    "metric": "net_revenue",
                    "kind": kind,
                    "start": start,
                    "end": end,
                    "unit": e.get("unit"),
                    "current": actual,
                    "expected": expected,
                    "change": e.get("change"),
                    "previous_last_date": last[:10],
                    "currency": currency,
                    "link": {"page": "dashboard", "dataset_id": dataset_id},
                },
            )
        )
    return out


def churn_up(
    dataset_id: str, prev: dict[str, Any], cur: dict[str, Any], t: Thresholds
) -> list[Draft]:
    """More customers at high churn risk, or more yearly spend held by them."""
    old, new = prev.get("churn"), cur.get("churn")
    if not old or not new:
        return []
    band = "high" if old.get("basis") == new.get("basis") == "probability" else "top_10pct"
    a, b = old.get(band) or {}, new.get(band) or {}
    if not a or not b:
        return []
    customers = _pct(float(b.get("customers") or 0), float(a.get("customers") or 0))
    spend = _pct(float(b.get("yearly_spend") or 0), float(a.get("yearly_spend") or 0))
    levels = [
        s
        for s in (
            _by_rise(customers or 0, t.churn_increase_pct),
            _by_rise(spend or 0, t.churn_increase_pct),
        )
        if s
    ]
    if not levels:
        return []
    severity: Severity = "critical" if "critical" in levels else "warning"
    who = "High-risk customers" if band == "high" else "Customers in the top 10% of churn risk"
    currency = _currency(cur)
    body = (
        f"{who}: {int(a.get('customers') or 0):,} in version {prev.get('version')} and "
        f"{int(b.get('customers') or 0):,} in version {cur.get('version')}; the yearly spend they "
        f"represent went from {_money(float(a.get('yearly_spend') or 0), currency)} to "
        f"{_money(float(b.get('yearly_spend') or 0), currency)}."
    )
    return [
        Draft(
            rule="churn_up",
            severity=severity,
            title="More revenue is at risk from customers likely to stop buying",
            body=body,
            dedupe_key=f"churn_up:{dataset_id}:v{cur.get('version')}:{cur.get('last_data_date')}",
            payload={
                "metric": "churn_risk",
                "band": band,
                "previous_customers": a.get("customers"),
                "current_customers": b.get("customers"),
                "customers_change_pct": None if customers is None else round(customers, 2),
                "previous_yearly_spend": a.get("yearly_spend"),
                "current_yearly_spend": b.get("yearly_spend"),
                "yearly_spend_change_pct": None if spend is None else round(spend, 2),
                "previous_version": prev.get("version"),
                "current_version": cur.get("version"),
                "threshold_pct": t.churn_increase_pct,
                "currency": currency,
                "link": {"page": "churn", "dataset_id": dataset_id},
            },
        )
    ]


def new_dead_stock(
    dataset_id: str, prev: dict[str, Any], cur: dict[str, Any], t: Thresholds
) -> list[Draft]:
    """Products that newly have no recent sales."""
    old, new = prev.get("dead_stock"), cur.get("dead_stock")
    if not old or not new:
        return []
    added = sorted(set(new.get("names") or []) - set(old.get("names") or []))
    if not added:
        return []
    digest = hashlib.sha256("\n".join(added).encode()).hexdigest()[:16]
    shown = ", ".join(added[:MAX_NAMES]) + (
        f" and {len(added) - MAX_NAMES} more" if len(added) > MAX_NAMES else ""
    )
    return [
        Draft(
            rule="new_dead_stock",
            severity="info",
            title=f"{len(added)} product(s) newly without sales",
            body=(
                f"{len(added)} product(s) had no sales in the {new.get('days')} days up to "
                f"{str(new.get('as_of') or '')[:10]} that still sold in the previous analysis: "
                f"{shown}."
            ),
            dedupe_key=f"new_dead_stock:{dataset_id}:{digest}",
            payload={
                "metric": "dead_stock",
                "count": len(added),
                "names": added[:MAX_NAMES],
                "days": new.get("days"),
                "as_of": str(new.get("as_of") or "")[:10] or None,
                "link": {"page": "insights", "dataset_id": dataset_id},
            },
        )
    ]


def quality_down(
    dataset_id: str, prev: dict[str, Any], cur: dict[str, Any], t: Thresholds
) -> list[Draft]:
    """Alert when the data-quality score of a new version fell by the threshold or more."""
    old, new = prev.get("quality_score"), cur.get("quality_score")
    if old is None or new is None or prev.get("version") == cur.get("version"):
        return []
    fall = int(old) - int(new)
    if fall < t.quality_drop_points:
        return []
    return [
        Draft(
            rule="quality_down",
            severity="critical" if fall >= 2 * t.quality_drop_points else "warning",
            title=f"Data quality fell {fall} points in version {cur.get('version')}",
            body=(
                f"The data-quality score was {int(old)} in version {prev.get('version')} and "
                f"{int(new)} in version {cur.get('version')}, {fall} points lower."
            ),
            dedupe_key=f"quality_down:{dataset_id}:v{cur.get('version')}",
            payload={
                "metric": "quality_score",
                "previous": int(old),
                "current": int(new),
                "change_points": -fall,
                "previous_version": prev.get("version"),
                "current_version": cur.get("version"),
                "threshold_points": t.quality_drop_points,
                "link": {"page": "dataset", "dataset_id": dataset_id},
            },
        )
    ]


RULES = (revenue_drop, orders_drop, unusual_days, churn_up, new_dead_stock, quality_down)


def compare(
    dataset_id: str, prev: dict[str, Any], cur: dict[str, Any], t: Thresholds
) -> list[Draft]:
    """Run every rule on two run summaries; summaries of another schema are not compared."""
    if prev.get("schema") != SUMMARY_SCHEMA or cur.get("schema") != SUMMARY_SCHEMA:
        return []
    out: list[Draft] = []
    for rule in RULES:
        out.extend(rule(dataset_id, prev, cur, t))
    return out


# ---- storing --------------------------------------------------------------------------------


def _store(
    db: Session,
    tenant_id: uuid.UUID,
    dataset_id: uuid.UUID,
    run_id: uuid.UUID | None,
    drafts: list[Draft],
) -> int:
    repo = AlertRepository(db, tenant_id)
    added = 0
    for d in drafts:
        added += repo.insert_if_new(
            dataset_id=dataset_id,
            run_id=run_id,
            rule=d.rule,
            severity=d.severity,
            title=d.title[:300],
            body=d.body,
            payload=d.payload,
            dedupe_key=d.dedupe_key[:200],
        )
    db.commit()
    return added


def evaluate_run(db: Session, tenant_id: uuid.UUID, run_id: uuid.UUID) -> int:
    """Raise the alerts of a finished run against the previous one; return how many are new."""
    runs = AnalysisRunRepository(db, tenant_id)
    run = runs.get(run_id)
    if run is None or run.summary is None or run.status not in FINISHED_RUN_STATES:
        return 0
    version = DatasetVersionRepository(db, tenant_id).get(run.dataset_version_id)
    if version is None:
        return 0
    previous = runs.previous_with_summary(version.dataset_id, run)
    if previous is None or previous.summary is None:
        return 0
    drafts = compare(
        str(version.dataset_id), previous.summary, run.summary, load_thresholds(db, tenant_id)
    )
    added = _store(db, tenant_id, version.dataset_id, run.id, drafts)
    logger.info("alerts_evaluated", run_id=str(run_id), candidates=len(drafts), added=added)
    return added


def stale_data(dataset_id: str, name: str, last: date, today: date, t: Thresholds) -> list[Draft]:
    """No new dated rows for more than the company's freshness window."""
    days = (today - last).days
    if days <= t.freshness_days:
        return []
    return [
        Draft(
            rule="stale_data",
            severity="warning" if days > 2 * t.freshness_days else "info",
            title=f"No new data for {name} in {days} days",
            body=(
                f"The latest date in {name} is {last.isoformat()}, {days} days before "
                f"{today.isoformat()}. The alert threshold is {t.freshness_days} days."
            ),
            dedupe_key=f"stale_data:{dataset_id}:{last.isoformat()}",
            payload={
                "metric": "freshness",
                "dataset_name": name,
                "last_date": last.isoformat(),
                "days": days,
                "threshold_days": t.freshness_days,
                "link": {"page": "dataset", "dataset_id": dataset_id},
            },
        )
    ]


def check_freshness(db: Session, today: date | None = None) -> int:
    """Daily beat check: datasets of companies that turned the check on and have stale data."""
    today = today or datetime.now(UTC).date()
    tenants = db.scalars(
        select(AlertSettings.tenant_id).where(AlertSettings.freshness_enabled.is_(True))
    ).all()
    added = 0
    for tenant_id in tenants:
        t = load_thresholds(db, tenant_id)
        versions = DatasetVersionRepository(db, tenant_id)
        for dataset in DatasetRepository(db, tenant_id).list_recent(limit=1_000):
            version = (
                versions.get(dataset.current_version_id) if dataset.current_version_id else None
            )
            last = from_profile_last_date(version) if version else None
            if last is None:
                continue
            drafts = stale_data(str(dataset.id), dataset.name, last, today, t)
            added += _store(db, tenant_id, dataset.id, None, drafts)
    logger.info("freshness_checked", tenants=len(tenants), added=added)
    return added
