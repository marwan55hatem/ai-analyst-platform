"""Every planted fact of the synthetic dataset is recovered exactly (English and Arabic headers)."""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.ground_truth_fixtures import analyze, full, make, of_type
from tests.helpers import register


def _one(
    client: TestClient, account: Any, insights: list[dict[str, Any]], kind: str
) -> dict[str, Any]:
    found = of_type(insights, kind)
    assert len(found) == 1, (kind, [i["type"] for i in insights])
    return full(client, account, found[0])


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_planted_facts_are_recovered_exactly(client: TestClient, tmp_path: Path, lang: str) -> None:
    owner = register(client)
    content, facts = make(tmp_path, lang=lang)
    _, run, insights = analyze(client, owner, content)

    assert run["status"] == "completed", run["progress"]
    modules = run["progress"]["modules"]
    assert {m: v["status"] for m, v in modules.items()} == {
        "kpis": "completed",
        "trend_seasonality": "completed",
        "pareto": "completed",
        "cohort_retention": "completed",
        "forecast": "completed",
    }

    kpi = _one(client, owner, insights, "kpi_summary")["payload"]
    assert kpi["revenue_basis"] == "net"
    assert round(kpi["net_revenue"] * 100) == facts["net_revenue_cents"]
    assert round(kpi["gross_revenue"] * 100) == facts["gross_revenue_cents"]
    assert kpi["orders"] == facts["order_count"]
    assert kpi["returns_count"] == 0 and of_type(insights, "returns") == []
    assert kpi["units"]["net"] == facts["units_gross"]
    assert kpi["margin_pct"] == pytest.approx(facts["margin_pct"], abs=0.01)
    assert kpi["average_order_value"] == pytest.approx(facts["aov_cents"] / 100, abs=0.01)

    season = _one(client, owner, insights, "seasonality")
    assert season["payload"]["peak_month"] == facts["peak_month"] == 12
    assert season["payload"]["peak_month_name"] == "December"
    assert "STL" in season["payload"]["method"]

    ytd = _one(client, owner, insights, "ytd_vs_prior_year")["payload"]
    assert round(ytd["ytd"] * 100) == facts["ytd_cents"]
    assert round(ytd["prior_ytd"] * 100) == facts["prior_ytd_cents"]
    assert ytd["change_pct"] == pytest.approx(facts["yoy_pct"], abs=0.01)

    by_dim = {
        i["payload"]["dimension"]: i
        for i in (full(client, owner, x) for x in of_type(insights, "pareto"))
    }
    assert set(by_dim) == {"product", "category", "region", "channel"}
    assert by_dim["product"]["payload"]["top_item"] == facts["top_product"]
    assert (
        round(by_dim["product"]["payload"]["items"][0]["revenue"] * 100)
        == facts["top_product_revenue_cents"]
    )
    assert by_dim["region"]["payload"]["top_item"] == facts["top_region"]
    assert by_dim["product"]["payload"]["item_count"] == facts["product_count"]
    assert by_dim["region"]["payload"]["item_count"] == facts["region_count"]

    dead = _one(client, owner, insights, "dead_stock")["payload"]
    assert sorted(dead["item_names"]) == facts["dead_stock"]
    assert dead["days"] == facts["dead_stock_days"]

    cohort = _one(client, owner, insights, "cohort_retention")["payload"]
    first = cohort["matrix"][0]
    assert first["cohort"] == f"{facts['first_cohort']['month']}-01"
    assert first["size"] == facts["first_cohort"]["size"]
    assert (
        round(first["retention"][1] * facts["first_cohort"]["size"] / 100)
        == facts["first_cohort"]["month1_retained"]
    )
    assert cohort["customers"] == facts["customer_count"] >= 500
    assert cohort["month1_retention_pct"] == pytest.approx(facts["month1_retention_pct"], abs=0.01)


def test_insights_are_ranked_and_carry_confidence_and_sample_size(
    client: TestClient, tmp_path: Path
) -> None:
    owner = register(client)
    content, _ = make(tmp_path)
    _, run, insights = analyze(client, owner, content)

    scores = [i["impact_score"] for i in insights]
    assert scores == sorted(scores, reverse=True) and max(scores) <= 100 and min(scores) >= 0
    assert all(
        i["confidence"] in {"high", "medium", "low"} and i["sample_size"] > 0 for i in insights
    )
    assert any(i["confidence"] == "high" for i in insights)
    assert all(i["summary"] and i["title"] for i in insights)
    only = client.get(
        f"/api/v1/analysis-runs/{run['id']}/insights?module=pareto&confidence=high",
        headers=owner.headers,
    ).json()
    assert only and {i["module"] for i in only} == {"pareto"}
    typed = client.get(
        f"/api/v1/analysis-runs/{run['id']}/insights?type=dead_stock", headers=owner.headers
    ).json()
    assert [i["type"] for i in typed] == ["dead_stock"]
