"""Churn risk without the database: label window, leakage, planted recovery, bands, privacy, skips."""

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.ml.base import DataFacts, ModuleSkipped
from app.ml.churn import labels
from app.ml.churn import model as churn_model
from app.ml.churn.features import FeatureSet, features_sql, labelled_sql, spend_trend
from app.ml.modules import churn
from app.ml.modules.churn import ChurnModule, rank_led, reason_text, risk_band, yearly_spend
from tests.ml_context import generate, module_context, read_rows, write_csv

FORBIDDEN = ("will leave", "will churn", "is leaving")


def _churn_csv(tmp_path: Path, seed: int = 11, years: int = 2) -> tuple[Path, dict[str, Any]]:
    return generate(tmp_path, seed=seed, years=years, churn=True)


def _by_type(insights: list[Any]) -> dict[str, Any]:
    return {i.type: i for i in insights}


def test_window_is_twice_the_median_gap_within_30_to_180_days() -> None:
    assert labels.choose_window(10) == 30
    assert labels.choose_window(20) == 40
    assert labels.choose_window(44.6) == 89
    assert labels.choose_window(120) == 180


def test_training_labels_end_where_evaluation_labels_begin() -> None:
    plan = labels.plan_for(date(2024, 12, 31), 25)
    assert plan.window_days == 50
    assert plan.eval_cutoff == date(2024, 11, 11)
    assert plan.train_cutoff == plan.eval_cutoff - timedelta(days=50)
    assert plan.history_needed() == 180
    assert labels.plan_for(date(2024, 12, 31), 90).history_needed() == 360


def _shift_future(rows: list[dict[str, str]], cutoff: date) -> list[dict[str, str]]:
    """Change everything after the cutoff: amounts, dates, deletions and new rows."""
    out: list[dict[str, str]] = []
    for n, r in enumerate(rows):
        day = date.fromisoformat(r["order_date"])
        if day <= cutoff:
            out.append(r)
            continue
        if n % 2:
            continue  # delete half of the future
        changed = dict(r)
        changed["revenue"] = f"{float(r['revenue']) * 3:.2f}"
        changed["order_date"] = (day + timedelta(days=2)).isoformat()
        out.append(changed)
        out.append({**r, "order_id": f"X{n}", "customer_id": f"N{n % 50:04d}"})
    return out


def test_leakage_changing_data_after_the_cutoff_changes_no_feature(tmp_path: Path) -> None:
    csv_path, _ = _churn_csv(tmp_path)
    rows = read_rows(csv_path)
    cutoff = date(2024, 6, 30)
    changed = write_csv(tmp_path / "changed.csv", _shift_future(rows, cutoff))
    assert changed.read_bytes() != csv_path.read_bytes()
    with module_context(csv_path) as ctx:
        before = ctx.db.rows(features_sql(ctx, cutoff))
    with module_context(changed) as ctx:
        after = ctx.db.rows(features_sql(ctx, cutoff))
    assert len(before) > 500
    assert before == after


def test_labels_only_look_at_the_window_after_the_cutoff(tmp_path: Path) -> None:
    csv_path, _ = _churn_csv(tmp_path)
    rows = read_rows(csv_path)
    cutoff, window = date(2024, 6, 30), 40
    late = cutoff + timedelta(days=window)
    beyond = [r for r in rows if date.fromisoformat(r["order_date"]) <= late]
    inside = [r for r in rows if not cutoff < date.fromisoformat(r["order_date"]) <= late]
    results = []
    for name, variant in (("all", rows), ("beyond", beyond), ("inside", inside)):
        path = write_csv(tmp_path / f"{name}.csv", variant)
        with module_context(path) as ctx:
            results.append(ctx.db.rows(labelled_sql(ctx, cutoff, window)))
    full, without_later, without_window = results
    assert full == without_later  # rows after cutoff + W never matter
    assert sum(r["churned"] for r in without_window) == len(without_window)  # nobody bought again
    assert sum(r["churned"] for r in full) < len(full)


@pytest.mark.parametrize(("seed", "years"), [(11, 2), (5, 2), (11, 3)])
def test_planted_churn_is_found_out_of_time(tmp_path: Path, seed: int, years: int) -> None:
    csv_path, _ = _churn_csv(tmp_path, seed, years)
    with module_context(csv_path) as ctx:
        insights = _by_type(ChurnModule().run(ctx))
    model = insights["churn_model"]
    winner = model.payload["winner"]
    assert model.payload["validation"] == "out_of_time"
    assert model.payload["models"][winner]["auc"] >= 0.75
    assert set(model.payload["models"]) == {"logistic", "lightgbm"}
    for metrics in model.payload["models"].values():
        assert {"auc", "pr_auc", "brier", "top_decile_lift"} <= set(metrics)
    label = model.payload["label"]
    assert 30 <= label["window_days"] <= 180
    assert date.fromisoformat(label["train_cutoff"]) < date.fromisoformat(label["eval_cutoff"])
    risk = insights["churn_risk"]
    assert risk.payload["basis"] == "probability"
    assert "basis: calibrated probability" in risk.summary
    assert "estimated risk" in risk.summary and "not a certainty" in risk.summary
    assert risk.result.sample_size > 500 and risk.result.confidence in ("high", "medium")
    text = (risk.title + risk.summary + model.title + model.summary).lower()
    assert not any(phrase in text for phrase in FORBIDDEN)
    top = risk.payload["importance"][0]["feature"]
    assert top in ("recency_days", "avg_gap_days", "frequency", "tenure_days", "spend_trend")


def test_scores_have_bands_reasons_and_pseudonyms_when_ids_are_personal(tmp_path: Path) -> None:
    csv_path, _ = _churn_csv(tmp_path)
    with module_context(csv_path, pii_roles=frozenset({"customer_id"})) as ctx:
        insights = ChurnModule().run(ctx)
        rows = [r for batch in ctx.cache[churn.CACHE_KEY]() for r in batch]
    assert len(rows) == insights[0].result.sample_size
    assert all(r["customer_key"].startswith("cust_") and len(r["customer_key"]) == 21 for r in rows)
    assert {r["risk_band"] for r in rows} <= {"high", "medium", "low"}
    top = [r for r in rows if r["rank_band"] == "top_10pct"]
    assert len(top) == round(0.1 * len(rows))
    assert min(r["probability"] for r in top) >= max(
        r["probability"] for r in rows if r["rank_band"] == "rest"
    )
    assert all(0.005 <= r["probability"] <= 0.995 for r in rows)
    for r in rows[:50]:
        assert len(r["reasons"]) == 3
        assert all(x["effect"] in ("raises", "lowers") and x["text"] for x in r["reasons"])
        assert r["expected_loss"] == pytest.approx(
            r["probability"] * r["revenue_at_stake"], abs=0.01
        )
    dumped = json.dumps(
        [[i.title, i.summary, i.payload, i.chart_spec, i.evidence] for i in insights] + rows,
        default=str,
    )
    raw_ids = {r["customer_id"] for r in read_rows(csv_path)}
    assert not [c for c in raw_ids if f'"{c}"' in dumped or f" {c} " in dumped]
    assert insights[0].payload["pseudonymized_customer_ids"] is True


def _low_probabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    real = churn_model.Fitted.predict

    def scaled(self: churn_model.Fitted, x: np.ndarray) -> np.ndarray:
        return np.asarray(real(self, x) * 0.2, dtype=float)

    monkeypatch.setattr(churn_model.Fitted, "predict", scaled)


def test_rank_view_leads_when_every_probability_is_below_25_percent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_path, _ = _churn_csv(tmp_path)
    _low_probabilities(monkeypatch)
    with module_context(csv_path) as ctx:
        risk = _by_type(ChurnModule().run(ctx))["churn_risk"]
    assert risk.payload["basis"] == "rank"
    assert risk.payload["max_probability"] < 0.25
    assert risk.title.startswith("Top 10% of customers by estimated churn risk")
    assert "ranked (basis: rank" in risk.summary
    assert "no customer reaches a 25% estimated probability" in risk.summary
    assert "The absolute probabilities are low" in risk.summary


def test_rank_view_leads_when_the_churn_rate_is_below_10_percent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_path, _ = _churn_csv(tmp_path)
    real = churn._fit_by_time

    def rare(ctx: Any, end: date) -> Any:
        fit = real(ctx, end)
        fit.label_rate = 0.06
        return fit

    monkeypatch.setattr(churn, "_fit_by_time", rare)
    with module_context(csv_path) as ctx:
        risk = _by_type(ChurnModule().run(ctx))["churn_risk"]
    assert risk.payload["basis"] == "rank"
    assert "only 6.0% of customers churned in the checked period" in risk.summary
    assert "The absolute probabilities are low" in risk.summary


def test_rank_rule() -> None:
    assert rank_led(0.2, 0.4) == "no customer reaches a 25% estimated probability"
    assert rank_led(0.9, 0.05) == "only 5.0% of customers churned in the checked period"
    assert rank_led(0.9, 0.3) is None


def test_bands_spend_and_reason_wording() -> None:
    assert [risk_band(p) for p in (0.7, 0.5, 0.3, 0.25, 0.1)] == [
        "high",
        "high",
        "medium",
        "medium",
        "low",
    ]
    assert yearly_spend(500, 100) == pytest.approx(500)
    assert yearly_spend(1460, 730) == pytest.approx(730)
    assert spend_trend(0, 0) == 0
    assert reason_text("recency_days", 140, 35, "raises", "en") == (
        "no purchase for 140 days (typical: 35) raises the estimated risk"
    )
    falling = reason_text("spend_trend", np.log(0.4), 0, "raises", "en")
    assert falling.startswith("spend in the last 90 days is down 60%")
    assert "about level" in reason_text("spend_trend", 0.01, 0, "lowers", "en")
    assert reason_text("spend_trend", 0.0, 0, "raises", "en", quiet=True) == (
        "no spend in the last 180 days raises the estimated risk"
    )
    assert reason_text("discount_share", 0.5, 0.1, "lowers", "en").startswith("50% of spend")
    assert reason_text("frequency", 2, 14, "raises", "en").startswith("2 orders (typical: 14)")
    assert reason_text("monetary", 1200, 600, "lowers", "en").startswith("total spend 1,200")


def test_too_few_customers_is_explained_up_front() -> None:
    reason = ChurnModule().data_requirement(DataFacts(distinct={"customer_id": 499}))
    assert reason == "only 499 customers; churn risk needs at least 500"
    assert ChurnModule().data_requirement(DataFacts(distinct={"customer_id": 500})) is None


def test_label_checks_skip_with_numbers() -> None:
    few = np.array([1] * 30 + [0] * 970)
    with pytest.raises(ModuleSkipped, match="only 30 customers churned"):
        churn._check_labels(few, "after x")
    rare = np.array([1] * 100 + [0] * 4900)
    with pytest.raises(ModuleSkipped, match="2.0% of customers churned"):
        churn._check_labels(rare, "after x")
    common = np.array([1] * 990 + [0] * 10)
    with pytest.raises(ModuleSkipped, match="99.0%"):
        churn._check_labels(common, "after x")
    churn._check_labels(np.array([1] * 100 + [0] * 900), "after x")


def test_short_history_is_skipped(tmp_path: Path) -> None:
    csv_path, _ = _churn_csv(tmp_path)
    rows = [r for r in read_rows(csv_path) if r["order_date"] < "2023-07-01"]
    short = write_csv(tmp_path / "short.csv", rows)
    with (
        module_context(short) as ctx,
        pytest.raises(ModuleSkipped, match="days of purchase history"),
    ):
        ChurnModule().run(ctx)


def test_churn_flag_replaces_the_label_and_is_cross_validated(tmp_path: Path) -> None:
    csv_path, facts = _churn_csv(tmp_path)
    left = set(facts["planted"]["churn"]["left"])
    rows = [
        {**r, "churned": "yes" if r["customer_id"] in left else "no"} for r in read_rows(csv_path)
    ]
    flagged = write_csv(tmp_path / "flagged.csv", rows)
    with module_context(flagged, extra_roles={"churn_flag": "churned"}) as ctx:
        assert FeatureSet.for_context(ctx, flag_mode=True).names[0] == "frequency"
        insights = _by_type(ChurnModule().run(ctx))
        scored = [r for batch in ctx.cache[churn.CACHE_KEY]() for r in batch]
    model = insights["churn_model"]
    assert model.payload["validation"] == "cross_validated"
    assert model.payload["label"] == {"source": "churn_flag"}
    assert "recency_days" not in model.payload["features"]
    assert model.result.confidence in ("medium", "low")
    assert any("churn_flag column, which has no date" in w for w in model.result.warnings)
    stayers = {r["customer_id"] for r in rows if r["churned"] == "no"}
    assert len(scored) == len(stayers)


def test_shap_values_match_lightgbm_tree_contributions() -> None:
    rng = np.random.default_rng(3)
    x = rng.normal(size=(400, 4))
    y = (x[:, 0] + 0.5 * x[:, 1] + rng.normal(scale=0.5, size=400) > 0).astype(int)
    fitted = churn_model.fit_final("lightgbm", x, y, 42)
    values = churn_model.explain(fitted, x[:50])
    native = fitted.base.predict(x[:50], pred_contrib=True)[:, :-1]
    assert values.shape == (50, 4)
    assert np.allclose(values, native, atol=1e-6)
    linear = churn_model.fit_final("logistic", x, y, 42)
    contributions = churn_model.explain(linear, x[:50])
    assert contributions.shape == (50, 4)
    assert np.abs(contributions[:, 0]).mean() > np.abs(contributions[:, 3]).mean()


def test_metrics_on_a_known_ranking() -> None:
    y = np.array([1] * 10 + [0] * 90)
    p = np.linspace(1, 0, 100)
    m = churn_model.metrics(y, p)
    assert m.auc == 1.0 and m.pr_auc == pytest.approx(1.0)
    assert m.top_decile_lift == pytest.approx(10.0)
    assert m.churners == 10 and m.customers == 100
    assert churn_model.calibration_method(999) == "sigmoid"
    assert churn_model.calibration_method(1000) == "isotonic"


def test_time_budget_skips_the_second_model_with_a_warning() -> None:
    rng = np.random.default_rng(5)
    x = rng.normal(size=(600, 3))
    y = (x[:, 0] + rng.normal(scale=0.7, size=600) > 0.3).astype(int)
    comparison = churn_model.compare_out_of_time(
        (x[:300], y[:300]), (x[300:], y[300:]), 42, lambda: True
    )
    assert list(comparison.scores) == ["logistic"] and comparison.winner == "logistic"
    assert comparison.warnings == [
        "The time limit was reached, so the lightgbm model was not tried."
    ]
