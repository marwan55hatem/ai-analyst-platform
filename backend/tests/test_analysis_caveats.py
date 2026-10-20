"""Data-quality caveats (duplicates, nulls, low score) and the refunds/negatives rule."""

import csv
import io
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.ml.quality import QualityContext, cap_confidence
from app.models.dataset import DatasetVersion
from tests.dataset_helpers import upload_ready
from tests.ground_truth_fixtures import analyze, full, make, of_type
from tests.helpers import API, register

DUPLICATES = 150


def _rows(content: bytes) -> tuple[list[str], list[list[str]]]:
    rows = list(csv.reader(io.StringIO(content.decode("utf-8"))))
    return rows[0], rows[1:]


def _csv(header: list[str], body: list[list[str]]) -> bytes:
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows([header, *body])
    return out.getvalue().encode("utf-8")


def _details(
    client: TestClient, owner: Any, insights: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    return [full(client, owner, i) for i in insights]


# ---- duplicates ------------------------------------------------------------------------------


@pytest.mark.slow
def test_duplicate_rows_warn_every_insight_and_cap_confidence(
    client: TestClient, tmp_path: Path
) -> None:
    owner = register(client)
    content, _ = make(tmp_path)
    header, body = _rows(content)
    dirty = _csv(header, body + [list(r) for r in body[:DUPLICATES]])
    _, run, insights = analyze(client, owner, dirty)

    assert run["status"] == "completed"
    assert len(insights) >= 10
    expected = f"{DUPLICATES} exact duplicate rows are included in these"
    for item in insights:
        assert item["confidence"] != "high", item["title"]
        assert expected in item["summary"], item["title"]
        assert any(expected in w for w in full(client, owner, item)["payload"]["warnings"])
    assert {i["confidence"] for i in insights} <= {"medium", "low"}


def test_a_clean_dataset_has_no_caveats(client: TestClient, tmp_path: Path) -> None:
    owner = register(client)
    content, _ = make(tmp_path)
    _, _, insights = analyze(client, owner, content)
    for item in _details(client, owner, insights):
        if item["module"] != "forecast":  # forecasts carry honest model notes, not data caveats
            assert item["payload"]["warnings"] == [], item["title"]
        assert item["payload"]["excluded_rows"] == 0


# ---- nulls -----------------------------------------------------------------------------------


def test_null_heavy_key_columns_are_excluded_counted_and_warned(
    client: TestClient, tmp_path: Path
) -> None:
    owner = register(client)
    content, facts = make(tmp_path)
    header, body = _rows(content)
    rev, cust, day = (
        header.index("revenue"),
        header.index("customer_id"),
        header.index("order_date"),
    )
    lost_cents = 0
    blank_revenue = blank_customer = blank_date = 0
    for n, row in enumerate(body):
        if n % 12 == 0:  # ~8% of revenue cells
            lost_cents += int(Decimal(row[rev]) * 100)
            row[rev] = ""
            blank_revenue += 1
        if n % 9 == 0:  # ~11% of customer ids
            row[cust] = ""
            blank_customer += 1
        if n % 14 == 0:  # ~7% of dates
            row[day] = ""
            blank_date += 1
    _, run, insights = analyze(client, owner, _csv(header, body))
    assert run["status"] in {"completed", "completed_with_warnings"}
    detailed = {i["type"]: i for i in _details(client, owner, insights) if i["type"] != "pareto"}

    kpi = detailed["kpi_summary"]
    assert kpi["payload"]["excluded_rows"] == blank_revenue
    assert round(kpi["payload"]["net_revenue"] * 100) == facts["net_revenue_cents"] - lost_cents
    assert kpi["confidence"] in {"medium", "low"}
    assert f"{blank_revenue:,} rows with no value in 'revenue'" in kpi["summary"]
    assert "% empty" in kpi["summary"]

    cohort = detailed["cohort_retention"]
    assert cohort["confidence"] in {"medium", "low"}
    assert "'customer_id'" in " ".join(cohort["payload"]["warnings"])
    assert cohort["payload"]["excluded_rows"] >= blank_customer

    season = detailed["seasonality"]
    assert "'order_date'" in " ".join(season["payload"]["warnings"])
    assert season["confidence"] != "high"
    # rows without a date or revenue never reach the monthly series
    assert season["payload"]["excluded_rows"] >= max(blank_revenue, blank_date)


@pytest.mark.slow
def test_low_quality_score_caps_confidence(client: TestClient, db: Session, tmp_path: Path) -> None:
    owner = register(client)
    content, _ = make(tmp_path)
    dataset = upload_ready(client, owner, content, "q.csv")
    version = db.get(DatasetVersion, uuid.UUID(dataset["versions"][0]["id"]))
    assert version is not None

    for score, ceiling in ((55, {"medium", "low"}), (30, {"low"})):
        version.quality_score = score
        db.commit()
        run = client.post(f"{API}/datasets/{dataset['id']}/analyze", headers=owner.headers).json()
        insights = client.get(
            f"{API}/analysis-runs/{run['id']}/insights", headers=owner.headers
        ).json()
        assert insights and {i["confidence"] for i in insights} <= ceiling, score
        assert all(f"quality score is low ({score}/100)" in i["summary"] for i in insights)


def test_quality_context_rules() -> None:
    ctx = QualityContext(
        row_count=1000, duplicate_rows=0, quality_score=85, null_pct={"a": 5.0, "b": 5.1}
    )
    assert ctx.caveats(["a"]).cap is None  # exactly 5% is allowed
    heavy = ctx.caveats(["a", "b"], excluded_rows=51)
    assert (
        heavy.cap == "medium" and "51 rows with no value in 'b' (5.1% empty)" in heavy.warnings[0]
    )
    assert QualityContext(quality_score=59).caveats([]).cap == "medium"
    assert QualityContext(quality_score=39).caveats([]).cap == "low"
    assert QualityContext(duplicate_rows=3).caveats([]).warnings == (
        "3 exact duplicate rows are included in these figures.",
    )
    assert cap_confidence("high", "medium") == "medium"
    assert cap_confidence("low", "medium") == "low"  # a cap never raises confidence
    assert cap_confidence("high", None) == "high"


# ---- refunds and negatives -------------------------------------------------------------------


def test_refunds_are_reported_separately_and_headline_revenue_is_net(
    client: TestClient, tmp_path: Path
) -> None:
    owner = register(client)
    content, facts = make(tmp_path, refunds=True)
    _, run, insights = analyze(client, owner, content)
    assert facts["returns_count"] == 25 and facts["returns_total_cents"] > 0
    assert run["status"] == "completed"

    kpi = full(client, owner, of_type(insights, "kpi_summary")[0])
    payload = kpi["payload"]
    assert payload["revenue_basis"] == "net" and kpi["title"].startswith("Net revenue")
    assert round(payload["net_revenue"] * 100) == facts["net_revenue_cents"]
    assert round(payload["gross_revenue"] * 100) == facts["gross_revenue_cents"]
    assert round(payload["returns_total"] * 100) == facts["returns_total_cents"]
    assert payload["returns_count"] == facts["returns_count"]
    assert payload["units"]["returned"] == facts["units_returned"]
    assert payload["orders"] == facts["order_count"]  # refund rows are not orders
    assert "Returns/refunds total" in kpi["summary"] and "already subtracted" in kpi["summary"]

    returns = full(client, owner, of_type(insights, "returns")[0])
    assert returns["payload"]["returns_count"] == 25
    assert round(returns["payload"]["returns_total"] * 100) == facts["returns_total_cents"]


def test_negatives_never_distort_pareto_trend_or_cohorts(
    client: TestClient, tmp_path: Path
) -> None:
    owner = register(client)
    content, facts = make(tmp_path, refunds=True)
    _, _, insights = analyze(client, owner, content)

    pareto = {
        p["payload"]["dimension"]: p for p in _details(client, owner, of_type(insights, "pareto"))
    }
    for dim in ("product", "region"):
        assert round(pareto[dim]["payload"]["total_revenue"] * 100) == facts["gross_revenue_cents"]
        assert pareto[dim]["payload"]["revenue_basis"] == "positive sales only"
        assert "return/refund rows were left out" in pareto[dim]["summary"]
    assert pareto["product"]["payload"]["top_item"] == facts["top_product"]
    assert pareto["region"]["payload"]["top_item"] == facts["top_region"]
    assert sum(i["returns"] for i in pareto["product"]["payload"]["items"]) > 0

    dead = full(client, owner, of_type(insights, "dead_stock")[0])
    assert sorted(dead["payload"]["item_names"]) == facts["dead_stock"]

    growth = full(client, owner, of_type(insights, "growth")[0])
    for point in growth["payload"]["series"]:
        assert round(point["revenue"] * 100) == facts["monthly_revenue_cents"][point["period"][:7]]
    assert "net revenue" in growth["summary"].lower()
    ytd = full(client, owner, of_type(insights, "ytd_vs_prior_year")[0])["payload"]
    assert round(ytd["ytd"] * 100) == facts["ytd_cents"]
    season = full(client, owner, of_type(insights, "seasonality")[0])
    assert season["payload"]["net_revenue_basis"] == "net"
    assert season["payload"]["peak_month"] == 12

    cohort = full(client, owner, of_type(insights, "cohort_retention")[0])
    assert cohort["payload"]["customers"] == facts["customer_count"]
    assert cohort["payload"]["month1_retention_pct"] == pytest.approx(
        facts["month1_retention_pct"], abs=0.01
    )
    assert "25 return/refund rows were not counted as purchases" in cohort["summary"]
