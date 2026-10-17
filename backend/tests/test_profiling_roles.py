"""Role detection and quality score on the generated clean/messy fixtures."""

from pathlib import Path

import pytest

from app.services.profiling.compute import ProfileResult
from app.services.profiling.results import ColumnResult
from app.services.profiling.roles import detect_role
from tests.retail_fixtures import build_csv, generator, profile_csv

CASES = [(v, lang) for v in ("clean", "messy") for lang in ("en", "ar")]


@pytest.fixture(scope="session")
def profiles(tmp_path_factory: pytest.TempPathFactory) -> dict[tuple[str, str], ProfileResult]:
    """Profile each generated fixture once."""
    result = {}
    for variant, lang in CASES:
        folder = tmp_path_factory.mktemp(f"{variant}_{lang}")
        result[(variant, lang)] = profile_csv(folder, build_csv(variant, lang))
    return result


@pytest.mark.parametrize(("variant", "lang"), CASES)
def test_planted_roles_are_found(
    profiles: dict[tuple[str, str], ProfileResult], variant: str, lang: str
) -> None:
    planted = generator().planted_roles(variant, lang)
    found = {c.original_name: c.semantic_role for c in profiles[(variant, lang)].columns}
    correct = [h for h, role in planted.items() if found.get(h) == role]
    wrong = {h: (planted[h], found.get(h)) for h in planted if h not in correct}
    assert len(correct) / len(planted) >= 0.9, wrong
    assert all(c.role_source == "rule" for c in profiles[(variant, lang)].columns)


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_clean_fixture_scores_90_or_more(
    profiles: dict[tuple[str, str], ProfileResult], lang: str
) -> None:
    quality = profiles[("clean", lang)].quality
    assert quality.score >= 90, quality.breakdown
    assert quality.confidence == "high" and quality.sample_size == 1500


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_messy_fixture_scores_below_60(
    profiles: dict[tuple[str, str], ProfileResult], lang: str
) -> None:
    quality = profiles[("messy", lang)].quality
    assert quality.score < 60, quality.breakdown


def test_breakdown_adds_up_and_respects_the_weights(
    profiles: dict[tuple[str, str], ProfileResult],
) -> None:
    for result in profiles.values():
        q = result.quality
        maxima = {
            "completeness": 30,
            "validity": 25,
            "uniqueness": 15,
            "consistency": 15,
            "sufficiency": 15,
        }
        assert set(q.breakdown) == set(maxima)
        for name, value in q.breakdown.items():
            assert 0 <= value <= maxima[name]
        assert q.score == round(sum(q.breakdown.values()))
        assert q.as_dict()["max"] == maxima


def test_top_issues_are_plain_language_with_a_suggested_fix(
    profiles: dict[tuple[str, str], ProfileResult],
) -> None:
    q = profiles[("messy", "en")].quality
    assert 1 <= len(q.issues) <= 5
    losses = [i.points_lost for i in q.issues]
    assert losses == sorted(losses, reverse=True)
    for issue in q.issues:
        assert issue.title and issue.detail and issue.suggested_fix
        assert issue.sample_size == 1650 and issue.confidence == "high"
    assert q.issues[0].code == "duplicate_rows"
    assert q.issues[0].fix_id == "dedupe"


def test_messy_fixture_findings(profiles: dict[tuple[str, str], ProfileResult]) -> None:
    result = profiles[("messy", "en")]
    columns = {c.name: c for c in result.columns}
    assert result.dataset["duplicate_rows"] == 150
    assert columns["order_date"].stats["format_count"] == 3
    assert columns["delivery_date"].stats["ambiguous"] is True
    assert columns["unit_price"].stats["needs_cast"] is True
    assert columns["qty"].inferred_type == "integer" and columns["qty"].stats["needs_cast"]
    assert columns["customer_email"].pii_flag and columns["mobile_phone"].pii_flag
    assert columns["customer_id"].inferred_type == "identifier"
    assert columns["region"].stats["case_variant_groups"] >= 1
    assert columns["region"].stats["whitespace_count"] > 0
    assert result.dataset["date_range"]["column"] in {"order_date", "delivery_date"}


def test_tiny_datasets_report_low_confidence_and_a_warning(tmp_path: Path) -> None:
    csv = b"order_id,qty\n1,2\n2,3\n3,4\n"
    quality = profile_csv(tmp_path, csv).quality
    assert quality.confidence == "low"
    assert quality.warnings and "Not enough data" in quality.warnings[0]
    assert any(i.code == "few_rows" for i in quality.issues)


def _column(name: str, ctype: str, **stats: object) -> ColumnResult:
    base = {"distinct_ratio": 0.5, "null_pct": 0.0, "null_count": 0, "non_null_count": 100}
    return ColumnResult(name=name, original_name=name, inferred_type=ctype, stats={**base, **stats})


@pytest.mark.parametrize(
    ("name", "ctype", "role"),
    [
        ("total_amount", "float", "revenue"),
        ("net_sales", "float", "revenue"),
        ("units_sold", "integer", "quantity"),
        ("cogs", "float", "cost"),
        ("promo_discount", "float", "discount"),
        ("sales_channel", "categorical", "channel"),
        ("city", "categorical", "region"),
        ("order_status", "categorical", "status"),
        ("created_at_date", "datetime", "date"),
        ("المبلغ", "float", "revenue"),
        ("رقم_الطلب", "identifier", "order_id"),
        ("المدينة", "categorical", "region"),
    ],
)
def test_role_rules(name: str, ctype: str, role: str) -> None:
    assert detect_role(_column(name, ctype, distinct_ratio=1.0, max=10, min=0)).role == role


def test_a_revenue_name_on_a_text_column_is_not_revenue() -> None:
    assert detect_role(_column("revenue_note", "text")).role != "revenue"


def test_unknown_names_are_other_with_low_confidence() -> None:
    guess = detect_role(_column("xyz", "text"))
    assert guess.role == "other" and guess.confidence <= 0.5


def test_profile_numbers_match_an_independent_pandas_calculation(
    profiles: dict[tuple[str, str], ProfileResult],
) -> None:
    """Cross-validation (validate-data skill): recompute key figures a second way."""
    import io

    import pandas as pd

    frame = pd.read_csv(io.BytesIO(build_csv("messy")), dtype=str, keep_default_na=False)
    result = profiles[("messy", "en")]
    columns = {c.name: c for c in result.columns}
    assert result.dataset["duplicate_rows"] == int(frame.duplicated().sum())
    for header, name in (("Cost", "cost"), ("Customer Email", "customer_email")):
        blank = (frame[header].str.strip() == "").mean() * 100
        assert columns[name].stats["null_pct"] == pytest.approx(blank, abs=0.01)
    cost = pd.to_numeric(frame["Cost"].replace("", None)).dropna()
    assert columns["cost"].stats["median"] == pytest.approx(cost.median())
    assert columns["cost"].stats["mean"] == pytest.approx(cost.mean())
    region = frame["Region"].str.strip()
    region = region[region != ""]
    variants = region.groupby(region.str.lower()).nunique()
    assert columns["region"].stats["case_variant_groups"] == int((variants > 1).sum())
