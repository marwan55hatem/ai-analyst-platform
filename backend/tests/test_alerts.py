"""Alert rules (before/after run summaries), dedupe, thresholds, endpoints and the beat check."""

import uuid
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.repository import AlertRepository
from app.models.report import Alert
from app.services import alerts
from app.services.alerts import Thresholds, check_freshness, compare
from tests.dataset_helpers import csv_bytes, upload, upload_ready
from tests.ground_truth_fixtures import analyze, make
from tests.helpers import API, add_member, register

T = Thresholds(
    revenue_drop_pct=15,
    orders_drop_pct=15,
    churn_increase_pct=20,
    quality_drop_points=5,
    freshness_enabled=True,
    freshness_days=14,
)
DS = "dataset-1"


def _summary(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema": 1,
        "version": 1,
        "quality_score": 90,
        "kpis": {"currency": None},
        "monthly": [],
        "latest_month": None,
        "last_data_date": None,
        "forecast": None,
        "unusual_days": [],
        "churn": None,
        "dead_stock": None,
    }
    return {**base, **over}


def _month(period: str, revenue: float, orders: float = 100, partial: bool = False) -> dict:
    return {"period": period, "net_revenue": revenue, "orders": orders, "partial": partial}


DEC = _month("2024-12-01", 200_000, 2_000)
JAN = _month("2025-01-01", 120_000, 1_200)  # 40% below December: a seasonal low, not a problem


def _forecast(point: float, lo80: float, lo95: float) -> dict[str, Any]:
    return {
        "metric": "revenue",
        "made_through": "2024-12-01",
        "no_better_than_baseline": False,
        "periods": {
            "2025-01-01": {
                "point": point,
                "lo80": lo80,
                "hi80": 2 * point - lo80,
                "lo95": lo95,
                "hi95": 2 * point - lo95,
            }
        },
    }


def _pair(forecast: dict[str, Any] | None, jan: dict[str, Any] = JAN) -> tuple[dict, dict]:
    prev = _summary(monthly=[DEC], latest_month=DEC, forecast=forecast, last_data_date="2024-12-31")
    cur = _summary(version=2, monthly=[DEC, jan], latest_month=jan, last_data_date="2025-01-31")
    return prev, cur


def _rules(drafts: list[alerts.Draft]) -> dict[str, alerts.Draft]:
    return {d.rule: d for d in drafts}


# ---- revenue and orders ---------------------------------------------------------------------


def test_december_peak_then_january_drop_is_quiet_when_the_forecast_expected_it() -> None:
    prev, cur = _pair(_forecast(point=125_000, lo80=110_000, lo95=100_000))
    assert "revenue_drop" not in _rules(alerts.revenue_drop(DS, prev, cur, T))


def test_january_below_the_80_percent_range_warns_and_below_95_is_critical() -> None:
    prev, cur = _pair(_forecast(point=150_000, lo80=130_000, lo95=110_000))
    (warning,) = alerts.revenue_drop(DS, prev, cur, T)
    assert warning.severity == "warning" and warning.payload["method"] == "forecast"
    assert warning.payload["range_level"] == 80 and warning.payload["expected"] == 150_000
    assert "Jan 2025" in warning.body and "80% range" in warning.body
    assert "Dec 2024" in warning.body  # the data the forecast was made from
    assert "because" not in warning.body.lower()  # numbers only, never a cause
    prev, cur = _pair(_forecast(point=180_000, lo80=160_000, lo95=140_000))
    (critical,) = alerts.revenue_drop(DS, prev, cur, T)
    assert critical.severity == "critical" and critical.payload["range_level"] == 95
    assert critical.dedupe_key == f"revenue_drop:{DS}:2025-01"


def test_without_a_forecast_the_month_over_month_fallback_says_seasonality_is_ignored() -> None:
    prev, cur = _pair(None)
    (draft,) = alerts.revenue_drop(DS, prev, cur, T)
    assert draft.payload["method"] == "month_over_month"
    assert draft.payload["change_pct"] == -40.0 and draft.severity == "critical"  # 2 x 15%
    assert draft.payload["previous_period"] == "2024-12-01"
    assert "Seasonality is not accounted for." in draft.body
    assert draft.payload["seasonality_accounted"] is False
    small = _month("2025-01-01", 180_000, 1_900)  # -10%: under the 15% threshold
    assert alerts.revenue_drop(DS, *_pair(None, small), T) == []
    mid = _month("2025-01-01", 165_000, 1_900)  # -17.5%: warning
    assert alerts.revenue_drop(DS, *_pair(None, mid), T)[0].severity == "warning"


def test_a_forecast_for_another_month_falls_back_to_month_over_month() -> None:
    other = _forecast(point=1, lo80=1, lo95=1)
    other["periods"] = {"2025-03-01": other["periods"]["2025-01-01"]}
    (draft,) = alerts.revenue_drop(DS, *_pair(other), T)
    assert draft.payload["method"] == "month_over_month"


def test_orders_use_month_over_month_with_the_seasonality_caveat() -> None:
    prev, cur = _pair(_forecast(point=125_000, lo80=110_000, lo95=100_000))
    (draft,) = alerts.orders_drop(DS, prev, cur, T)
    assert draft.rule == "orders_drop" and draft.payload["metric"] == "orders"
    assert draft.payload["current"] == 1_200 and draft.payload["previous"] == 2_000
    assert "Seasonality is not accounted for." in draft.body


def test_no_new_complete_month_means_no_sales_alert() -> None:
    same = _summary(monthly=[DEC], latest_month=DEC)
    assert alerts.revenue_drop(DS, same, same, T) == []
    assert alerts.orders_drop(DS, same, same, T) == []
    partial_jan = _month("2025-01-01", 1, 1, partial=True)
    cur = _summary(monthly=[DEC, partial_jan], latest_month=DEC)
    assert alerts.revenue_drop(DS, same, cur, T) == []
    assert alerts.revenue_drop(DS, same, _summary(latest_month=None), T) == []


def test_month_over_month_needs_a_positive_earlier_month() -> None:
    zero = _month("2024-12-01", 0, 0)
    prev = _summary(latest_month=zero, monthly=[zero])
    cur = _summary(monthly=[zero, JAN], latest_month=JAN)
    assert alerts.revenue_drop(DS, prev, cur, T) == []
    lonely = _summary(monthly=[JAN], latest_month=JAN)
    assert alerts.revenue_drop(DS, _summary(), lonely, T) == []


# ---- other rules ----------------------------------------------------------------------------


def _event(start: str, kind: str, change: float) -> dict[str, Any]:
    return {
        "start": start,
        "end": start,
        "unit": "day",
        "kind": kind,
        "actual": 100 + change,
        "expected": 100,
        "change": change,
    }


def test_unusual_days_only_after_the_previous_data_and_not_seen_before() -> None:
    old = _event("2024-12-10", "spike", 50)
    prev = _summary(last_data_date="2024-12-31", unusual_days=[old])
    cur = _summary(
        last_data_date="2025-01-31",
        unusual_days=[old, _event("2025-01-05", "drop", -60), _event("2025-01-20", "spike", 80)],
    )
    found = alerts.unusual_days(DS, prev, cur, T)
    assert [(d.payload["start"], d.severity) for d in found] == [
        ("2025-01-20", "info"),
        ("2025-01-05", "warning"),
    ]
    assert found[1].dedupe_key == f"new_unusual_days:{DS}:2025-01-05:drop"
    many = _summary(
        last_data_date="2025-01-31",
        unusual_days=[_event(f"2025-01-{d:02d}", "drop", -d) for d in range(1, 9)],
    )
    assert len(alerts.unusual_days(DS, prev, many, T)) == alerts.MAX_EVENT_ALERTS
    assert alerts.unusual_days(DS, _summary(), cur, T) == []


def _churn(customers: int, spend: float, basis: str = "probability") -> dict[str, Any]:
    band = {"customers": customers, "yearly_spend": spend, "expected_loss": spend / 2}
    return {"basis": basis, "scored_customers": 1000, "high": band, "top_10pct": band}


def test_churn_rise_beyond_the_threshold() -> None:
    prev = _summary(churn=_churn(100, 50_000))
    assert alerts.churn_up(DS, prev, _summary(version=2, churn=_churn(110, 52_000)), T) == []
    (warning,) = alerts.churn_up(DS, prev, _summary(version=2, churn=_churn(125, 50_000)), T)
    assert warning.severity == "warning" and warning.payload["customers_change_pct"] == 25.0
    assert "100 in version 1" in warning.body and "125 in version 2" in warning.body
    (critical,) = alerts.churn_up(DS, prev, _summary(version=2, churn=_churn(100, 80_000)), T)
    assert critical.severity == "critical" and critical.payload["band"] == "high"
    ranked = _summary(churn=_churn(100, 50_000, basis="rank"))
    (top,) = alerts.churn_up(DS, ranked, _summary(churn=_churn(100, 70_000, basis="rank")), T)
    assert top.payload["band"] == "top_10pct"
    assert alerts.churn_up(DS, _summary(), prev, T) == []


def test_new_dead_stock_lists_only_new_products() -> None:
    prev = _summary(dead_stock={"names": ["A"], "count": 1, "days": 90, "as_of": "2024-12-31"})
    same = alerts.new_dead_stock(DS, prev, prev, T)
    assert same == []
    names = ["A", "B", "C"] + [f"P{i:02d}" for i in range(12)]
    cur = _summary(dead_stock={"names": names, "count": 15, "days": 90, "as_of": "2025-01-31"})
    (draft,) = alerts.new_dead_stock(DS, prev, cur, T)
    assert draft.payload["count"] == 14 and len(draft.payload["names"]) == alerts.MAX_NAMES
    assert "and 4 more" in draft.body and draft.severity == "info"


def test_quality_score_drop_between_versions() -> None:
    prev = _summary(version=1, quality_score=90)
    assert alerts.quality_down(DS, prev, _summary(version=1, quality_score=70), T) == []
    assert alerts.quality_down(DS, prev, _summary(version=2, quality_score=86), T) == []
    (warning,) = alerts.quality_down(DS, prev, _summary(version=2, quality_score=84), T)
    assert warning.severity == "warning" and warning.payload["change_points"] == -6
    (critical,) = alerts.quality_down(DS, prev, _summary(version=2, quality_score=80), T)
    assert critical.severity == "critical"


def test_compare_ignores_summaries_of_another_schema() -> None:
    prev, cur = _pair(None)
    assert compare(DS, prev, cur, T)
    assert compare(DS, {**prev, "schema": 0}, cur, T) == []


def test_stale_data_respects_the_window() -> None:
    last = date(2025, 1, 1)
    assert alerts.stale_data(DS, "Sales", last, date(2025, 1, 15), T) == []
    (info,) = alerts.stale_data(DS, "Sales", last, date(2025, 1, 20), T)
    assert info.severity == "info" and info.payload["days"] == 19
    (warning,) = alerts.stale_data(DS, "Sales", last, date(2025, 3, 1), T)
    assert warning.severity == "warning"


def test_defaults_come_from_the_settings() -> None:
    defaults = Thresholds.defaults(get_settings())
    assert defaults.revenue_drop_pct == get_settings().alert_revenue_drop_pct
    assert defaults.freshness_enabled is False


# ---- endpoints ------------------------------------------------------------------------------


def _dataset(client: TestClient, owner: Any) -> dict[str, Any]:
    rows = [[f"2024-01-{d:02d}", d * 10] for d in range(1, 29)]
    return upload_ready(client, owner, csv_bytes(rows, ["order_date", "revenue"]), "small.csv")


def _add_alert(db: Session, tenant_id: str, dataset_id: str, key: str, severity: str) -> None:
    repo = AlertRepository(db, uuid.UUID(tenant_id))
    repo.insert_if_new(
        dataset_id=uuid.UUID(dataset_id),
        run_id=None,
        rule="quality_down",
        severity=severity,
        title=f"Alert {key}",
        body="Body",
        payload={"link": {"page": "dataset", "dataset_id": dataset_id}},
        dedupe_key=key,
    )
    db.commit()


def test_feed_unread_count_and_read_state(client: TestClient, db: Session) -> None:
    owner = register(client)
    dataset = _dataset(client, owner)
    for n, severity in enumerate(("info", "warning", "critical")):
        _add_alert(db, owner.tenant_id, dataset["id"], f"k{n}", severity)
    _add_alert(db, owner.tenant_id, dataset["id"], "k0", "info")  # same key: ignored
    feed = client.get(f"{API}/alerts", headers=owner.headers).json()
    assert feed["total"] == 3 and len(feed["items"]) == 3
    assert client.get(f"{API}/alerts/unread-count", headers=owner.headers).json() == {"count": 3}
    first = feed["items"][0]["id"]
    read = client.post(f"{API}/alerts/{first}/read", headers=owner.headers)
    assert read.status_code == 200 and read.json()["read_at"] is not None
    again = client.post(f"{API}/alerts/{first}/read", headers=owner.headers).json()
    assert again["read_at"] == read.json()["read_at"]
    unread = client.get(f"{API}/alerts?unread=true", headers=owner.headers).json()
    assert unread["total"] == 2
    page = client.get(f"{API}/alerts?limit=1&offset=1", headers=owner.headers).json()
    assert len(page["items"]) == 1 and page["total"] == 3
    done = client.post(f"{API}/alerts/read-all", headers=owner.headers).json()
    assert done == {"updated": 2}
    assert client.get(f"{API}/alerts/unread-count", headers=owner.headers).json() == {"count": 0}
    missing = client.post(f"{API}/alerts/{uuid.uuid4()}/read", headers=owner.headers)
    assert missing.status_code == 404


def test_alert_settings_defaults_permissions_and_bounds(client: TestClient) -> None:
    owner = register(client)
    member = add_member(client, owner, role="member")
    current = client.get(f"{API}/alert-settings", headers=member.headers).json()
    assert current["is_default"] is True and current["can_edit"] is False
    assert current["revenue_drop_pct"] == get_settings().alert_revenue_drop_pct
    body = {
        "revenue_drop_pct": 10,
        "orders_drop_pct": 12,
        "churn_increase_pct": 30,
        "quality_drop_points": 8,
        "freshness_enabled": True,
        "freshness_days": 7,
    }
    assert client.put(f"{API}/alert-settings", headers=member.headers, json=body).status_code == 403
    saved = client.put(f"{API}/alert-settings", headers=owner.headers, json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["is_default"] is False and saved.json()["can_edit"] is True
    assert client.get(f"{API}/alert-settings", headers=member.headers).json()["freshness_days"] == 7
    changed = client.put(
        f"{API}/alert-settings", headers=owner.headers, json={**body, "freshness_days": 30}
    )
    assert changed.json()["freshness_days"] == 30
    for key, bad in (
        ("revenue_drop_pct", 0),
        ("revenue_drop_pct", 95),
        ("churn_increase_pct", 501),
        ("quality_drop_points", 0),
        ("freshness_days", 366),
    ):
        response = client.put(
            f"{API}/alert-settings", headers=owner.headers, json={**body, key: bad}
        )
        assert response.status_code == 422, (key, bad)


def test_freshness_beat_check_only_for_companies_that_turned_it_on(
    client: TestClient, db: Session
) -> None:
    on, off = register(client), register(client)
    on_data, _ = _dataset(client, on), _dataset(client, off)
    body = {
        "revenue_drop_pct": 15,
        "orders_drop_pct": 15,
        "churn_increase_pct": 20,
        "quality_drop_points": 5,
        "freshness_enabled": True,
        "freshness_days": 14,
    }
    assert client.put(f"{API}/alert-settings", headers=on.headers, json=body).status_code == 200
    assert check_freshness(db, today=date(2024, 2, 5)) == 0  # 9 days after 2024-01-28
    assert check_freshness(db, today=date(2024, 3, 1)) == 1
    assert check_freshness(db, today=date(2024, 3, 2)) == 0  # same last date: deduplicated
    (alert,) = db.scalars(select(Alert)).all()
    assert str(alert.tenant_id) == on.tenant_id and str(alert.dataset_id) == on_data["id"]
    assert alert.rule == "stale_data" and alert.payload["last_date"] == "2024-01-28"


# ---- real runs ------------------------------------------------------------------------------


def _filter_rows(content: bytes, keep: Any, scale: Any = None) -> bytes:
    lines = content.decode("utf-8").splitlines()
    header = lines[0].split(",")
    day, revenue = header.index("order_date"), header.index("revenue")
    out = [lines[0]]
    for line in lines[1:]:
        cells = line.split(",")
        if not keep(cells[day]):
            continue
        if scale is not None:
            cells[revenue] = f"{float(cells[revenue]) * scale(cells[day]):.2f}"
        out.append(",".join(cells))
    return ("\n".join(out) + "\n").encode("utf-8")


@pytest.mark.slow
def test_a_december_far_below_the_forecast_alerts_once(
    client: TestClient, db: Session, tmp_path: Path
) -> None:
    owner = register(client)
    content, _ = make(tmp_path)
    v1 = _filter_rows(content, lambda d: d <= "2024-11-30")
    dataset_id, _, _ = analyze(client, owner, v1)
    assert client.get(f"{API}/alerts", headers=owner.headers).json()["total"] == 0  # first run

    # With 23 months the forecast is a flat ETS line with wide ranges (no December peak), so the
    # planted December has to fall below its 95% range to count; a milder drop rightly stays quiet.
    v2 = _filter_rows(content, lambda d: True, lambda d: 0.15 if d >= "2024-12-01" else 1.0)
    added = upload(client, owner, v2, "v2.csv", path=f"/datasets/{dataset_id}/versions")
    assert added.status_code == 201, added.text
    run = client.post(f"{API}/datasets/{dataset_id}/analyze", headers=owner.headers).json()
    assert run["status"].startswith("completed")
    feed = client.get(f"{API}/alerts?limit=100", headers=owner.headers).json()
    found = {a["rule"]: a for a in feed["items"]}
    drop = found["revenue_drop"]
    assert drop["severity"] == "critical" and drop["payload"]["method"] == "forecast"
    assert drop["payload"]["period"] == "2024-12-01" and drop["run_id"] == run["id"]
    assert drop["payload"]["current"] < drop["payload"]["range_low"]

    again = client.post(f"{API}/datasets/{dataset_id}/analyze", headers=owner.headers).json()
    assert again["status"].startswith("completed")
    assert client.get(f"{API}/alerts", headers=owner.headers).json()["total"] == feed["total"]
