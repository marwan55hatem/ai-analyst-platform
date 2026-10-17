"""Date columns: formats, dd/mm ambiguity, range, granularity and gaps."""

from datetime import date, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from app.services.profiling.compute import compute_profile
from app.services.profiling.dates import classify_granularity, decide_order, gap_summary


def _dates(tmp_path: Path, values: list[str] | list[date]) -> dict:
    path = tmp_path / "d.parquet"
    pq.write_table(pa.table({"when": values}), path)
    column = compute_profile(path).columns[0]
    assert column.inferred_type == "datetime"
    return column.stats


def test_granularity_bands() -> None:
    assert classify_granularity(1) == "daily"
    assert classify_granularity(7) == "weekly"
    assert classify_granularity(30) == "monthly"
    assert classify_granularity(91) == "quarterly"
    assert classify_granularity(365) == "yearly"
    assert classify_granularity(500) == "irregular"


def test_daily_series_with_a_gap() -> None:
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(30)]
    del days[10:15]  # five missing days
    summary = gap_summary(days)
    assert summary["granularity"] == "daily"
    assert summary["gap_count"] == 1
    assert summary["largest_gap_days"] == 6
    assert summary["missing_periods"] == 5


def test_weekly_and_monthly_series() -> None:
    weekly = [date(2024, 1, 1) + timedelta(weeks=i) for i in range(10)]
    assert gap_summary(weekly)["granularity"] == "weekly"
    monthly = [date(2024, m, 1) for m in range(1, 13)]
    summary = gap_summary(monthly)
    assert summary["granularity"] == "monthly"
    assert summary["gap_count"] == 0


def test_single_date_has_no_granularity() -> None:
    assert gap_summary([date(2024, 1, 1)])["granularity"] is None


def test_order_decisions() -> None:
    assert decide_order(5, 0, 3) == ("dmy", False)
    assert decide_order(0, 5, 3) == ("mdy", False)
    assert decide_order(2, 2, 0) == ("mixed", True)
    assert decide_order(0, 0, 4) == (None, True)
    assert decide_order(0, 0, 0) == (None, False)


def test_native_date_column(tmp_path: Path) -> None:
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(40)]
    stats = _dates(tmp_path, days)
    assert stats["stored_as"] == "native"
    assert stats["min"].startswith("2024-01-01") and stats["max"].startswith("2024-02-09")
    assert stats["span_days"] == 39
    assert stats["granularity"] == "daily"
    assert stats["gap_count"] == 0
    assert stats["ambiguous"] is False


def test_day_first_is_detected_when_a_day_exceeds_twelve(tmp_path: Path) -> None:
    values = ["25/03/2024", "03/04/2024", "27/05/2024", "01/06/2024"] * 5
    stats = _dates(tmp_path, values)
    assert stats["order"] == "dmy"
    assert stats["ambiguous"] is False
    assert stats["dayfirst"] is True
    assert stats["conform_share"] == 1.0


def test_month_first_is_detected(tmp_path: Path) -> None:
    values = ["03/25/2024", "04/03/2024", "05/27/2024", "06/01/2024"] * 5
    stats = _dates(tmp_path, values)
    assert stats["order"] == "mdy"
    assert stats["dayfirst"] is False


def test_all_slash_dates_up_to_twelve_are_flagged_ambiguous(tmp_path: Path) -> None:
    values = ["01/02/2024", "03/04/2024", "05/06/2024", "07/08/2024"] * 5
    stats = _dates(tmp_path, values)
    assert stats["ambiguous"] is True
    assert stats["order"] is None
    assert stats["ambiguous_cells"] == 20


def test_conflicting_orders_are_flagged_as_mixed(tmp_path: Path) -> None:
    values = ["25/03/2024", "03/25/2024", "01/02/2024"] * 7
    stats = _dates(tmp_path, values)
    assert stats["order"] == "mixed"
    assert stats["ambiguous"] is True


def test_several_formats_are_counted(tmp_path: Path) -> None:
    values = ["2024-01-05", "25/03/2024", "5 Jan 2024"] * 7
    stats = _dates(tmp_path, values)
    assert stats["format_count"] == 3
    assert set(stats["format_families"]) == {"iso", "numeric", "textual"}


def test_unreadable_values_reduce_the_conforming_share(tmp_path: Path) -> None:
    values = ["2024-01-05"] * 19 + ["sometime"]
    stats = _dates(tmp_path, values)
    assert stats["invalid_count"] == 1
    assert stats["conform_share"] == 0.95


def test_times_are_detected(tmp_path: Path) -> None:
    stats = _dates(tmp_path, ["2024-01-05 10:30:00", "2024-01-06 11:00:00"] * 10)
    assert stats["has_time"] is True
