"""Applying fixes writes a new Parquet; the source is untouched and only listed fixes run."""

import hashlib
from pathlib import Path

import duckdb
import pytest

from app.services.profiling.cleaning_apply import ApplyResult, apply_fixes
from app.services.profiling.compute import ProfileResult, compute_profile
from tests.retail_fixtures import build_csv, ingest_csv


class Messy:
    """The messy fixture ingested once, with its profile and plan."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.parquet, self.column_map = ingest_csv(folder, build_csv("messy"))
        self.profile: ProfileResult = compute_profile(self.parquet, self.column_map)
        self.plan = [f.as_dict() for f in self.profile.plan]
        self.recommended = {f["id"]: None for f in self.plan if f["recommended"]}

    def apply(self, selection: dict[str, str | None], name: str = "out") -> ApplyResult:
        return apply_fixes(self.parquet, self.folder / f"{name}.parquet", self.plan, selection)

    def query(self, sql: str, name: str = "out") -> list[tuple]:
        con = duckdb.connect()
        path = (self.folder / f"{name}.parquet").as_posix()
        return con.execute(sql.replace("{t}", f"read_parquet('{path}')")).fetchall()


@pytest.fixture(scope="module")
def messy(tmp_path_factory: pytest.TempPathFactory) -> Messy:
    return Messy(tmp_path_factory.mktemp("apply"))


def _source_fingerprint(messy: Messy) -> str:
    return hashlib.sha256(messy.parquet.read_bytes()).hexdigest()


def test_recommended_low_risk_fixes_keep_duplicates_and_nulls(messy: Messy) -> None:
    before = _source_fingerprint(messy)
    result = messy.apply(messy.recommended, "low")
    assert result.row_count == 1650  # no row removed
    assert result.new_columns == []
    types = dict(messy.query("DESCRIBE SELECT * FROM {t}", "low")[i][:2] for i in range(17))
    assert types["unit_price"] == "DOUBLE" and types["qty"] == "BIGINT"
    assert types["order_date"] == "DATE"
    assert types["delivery_date"] == "VARCHAR"  # high-risk fix was not listed
    nulls = messy.query("SELECT count(*) FILTER (WHERE cost IS NULL) FROM {t}", "low")[0][0]
    source_nulls = (
        duckdb.connect()
        .execute(
            f"SELECT count(*) FILTER (WHERE cost IS NULL) "
            f"FROM read_parquet('{messy.parquet.as_posix()}')"
        )
        .fetchone()[0]
    )
    assert nulls == source_nulls
    assert _source_fingerprint(messy) == before  # the source file is never modified


def test_trim_case_and_cast_results(messy: Messy) -> None:
    messy.apply(messy.recommended, "low2")
    regions = {r[0] for r in messy.query("SELECT DISTINCT region FROM {t}", "low2")}
    assert regions == {"North", "South", "East", "West", "Central", None}
    products = {r[0] for r in messy.query("SELECT DISTINCT product FROM {t}", "low2")}
    assert len(products) == 12 and all(p == p.strip() and p[0].isupper() for p in products)
    discounts = {r[0] for r in messy.query("SELECT DISTINCT discount FROM {t}", "low2")}
    assert discounts <= {None, 0.0, 0.05, 0.1, 0.15, 0.2, 0.3}
    assert messy.query("SELECT min(unit_price) > 0 FROM {t}", "low2")[0][0] is True


def test_logs_report_rows_affected_per_fix(messy: Messy) -> None:
    result = messy.apply(messy.recommended, "low3")
    logged = {a.fix_id: a.rows_affected for a in result.applied}
    assert set(logged) == set(messy.recommended)
    assert logged["trim:region"] > 0 and logged["cast:unit_price"] > 1000
    assert all(a.description for a in result.applied)


def test_dedupe_runs_only_when_listed(messy: Messy) -> None:
    result = messy.apply({"dedupe": None}, "dedupe")
    assert result.row_count == 1650 - 150
    assert result.applied[0].rows_affected == 150
    distinct = messy.query("SELECT count(*) FROM (SELECT DISTINCT * FROM {t})", "dedupe")[0][0]
    assert distinct == result.row_count


def test_null_strategy_keep_changes_nothing(messy: Messy) -> None:
    fixes = {"nulls:cost": "keep", "trim:region": None}
    result = messy.apply(fixes, "keep")
    assert [a.fix_id for a in result.applied] == ["trim:region"]


def test_impute_median_fills_nulls(messy: Messy) -> None:
    messy.apply({"nulls:cost": "impute_median"}, "median")
    assert messy.query("SELECT count(*) FILTER (WHERE cost IS NULL) FROM {t}", "median")[0][0] == 0
    stats = next(c for c in messy.profile.columns if c.name == "cost").stats
    filled = messy.query(
        "SELECT count(*) FROM {t} WHERE cost = ?".replace("?", str(stats["median"])), "median"
    )
    assert filled[0][0] >= 1


def test_impute_mode_fills_a_category(messy: Messy) -> None:
    messy.apply({"trim:region": None, "case:region": None, "nulls:region": "impute_mode"}, "mode")
    values = {r[0] for r in messy.query("SELECT DISTINCT region FROM {t}", "mode")}
    assert None not in values and "" not in values


def test_integer_columns_stay_integers_after_median_imputation(messy: Messy) -> None:
    messy.apply({"cast:qty": None, "nulls:qty": "impute_median"}, "qty")
    assert messy.query("SELECT typeof(qty) FROM {t} LIMIT 1", "qty")[0][0] == "BIGINT"
    assert messy.query("SELECT count(*) FILTER (WHERE qty IS NULL) FROM {t}", "qty")[0][0] == 0


def test_drop_rows_removes_rows_with_missing_values(messy: Messy) -> None:
    missing = next(c for c in messy.profile.columns if c.name == "channel").stats["null_count"]
    result = messy.apply({"nulls:channel": "drop_rows"}, "drop")
    assert result.row_count == 1650 - missing
    assert result.applied[0].rows_affected == missing


def test_outliers_are_flagged_never_deleted(messy: Messy) -> None:
    expected = next(c for c in messy.profile.columns if c.name == "cost").stats["outlier_count"]
    result = messy.apply({"flag_outliers:cost": None}, "flag")
    assert result.row_count == 1650
    assert result.new_columns == ["cost_is_outlier"]
    flagged = messy.query("SELECT count(*) FILTER (WHERE cost_is_outlier) FROM {t}", "flag")[0][0]
    assert flagged == expected > 0


def test_ambiguous_dates_follow_the_chosen_strategy(messy: Messy) -> None:
    messy.apply({"parse_dates:delivery_date": "dayfirst"}, "dmy")
    messy.apply({"parse_dates:delivery_date": "monthfirst"}, "mdy")
    sql = (
        "SELECT min(extract(day FROM delivery_date)), max(extract(day FROM delivery_date)) FROM {t}"
    )
    assert messy.query(sql, "dmy")[0] == (1, 12)
    # read month-first, the same text gives months up to 12 and days 1..12 swapped
    months = messy.query("SELECT max(extract(month FROM delivery_date)) FROM {t}", "mdy")[0][0]
    assert months == 12
    days = messy.query("SELECT max(extract(day FROM delivery_date)) FROM {t}", "mdy")[0][0]
    assert days == 12


def test_fixed_columns_are_unchanged_when_not_listed(messy: Messy) -> None:
    messy.apply({"trim:product": None}, "only_trim")
    types = messy.query("DESCRIBE SELECT * FROM {t}", "only_trim")
    assert {t[0]: t[1] for t in types}["unit_price"] == "VARCHAR"


def test_applying_the_plan_raises_the_score_by_at_least_25(messy: Messy, tmp_path: Path) -> None:
    low_risk = set(messy.recommended)
    explicit = {
        "dedupe": None,
        "parse_dates:delivery_date": "dayfirst",
        "nulls:order_date": "keep",
        "nulls:delivery_date": "keep",
        "nulls:region": "impute_mode",
        "nulls:channel": "impute_mode",
        "nulls:qty": "impute_median",
        "nulls:unit_price": "impute_median",
        "nulls:revenue": "impute_median",
        "nulls:cost": "impute_median",
    }
    selection = {**dict.fromkeys(low_risk), **explicit}
    messy.apply(selection, "full")
    cleaned = compute_profile(messy.folder / "full.parquet", messy.column_map)
    before = messy.profile.quality.score
    assert cleaned.quality.score >= before + 25, (before, cleaned.quality.breakdown)
    assert cleaned.dataset["duplicate_rows"] == 0
    assert before < 60
