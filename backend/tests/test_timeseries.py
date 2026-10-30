"""Dashboard time series: KPIs vs the previous window, filled series, partial periods and limits."""

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.ml.timeseries import SeriesUnavailable, Window, buckets, compute
from tests.ml_context import module_context, write_csv

START = date(2024, 1, 1)  # a Monday


def _rows(days: int = 14, *, refund_on: date | None = date(2024, 1, 10)) -> list[dict[str, Any]]:
    rows = []
    for i in range(days):
        day = START + timedelta(days=i)
        if day == date(2024, 1, 5):
            continue  # a day without sales: the series shows zero, not a gap
        rows.append(
            {
                "order_id": f"O{i}",
                "customer_id": f"C{i % 3}",
                "order_date": day.isoformat(),
                "quantity": 1,
                "unit_price": 10,
                "revenue": 10,
                "cost": 4,
            }
        )
    if refund_on is not None:
        rows.append(
            {
                "order_id": "R1",
                "customer_id": "C1",
                "order_date": refund_on.isoformat(),
                "quantity": -1,
                "unit_price": 5,
                "revenue": -5,
                "cost": 0,
            }
        )
    return rows


def _kpi(found: dict[str, Any], key: str) -> dict[str, Any]:
    return next(k for k in found["kpis"] if k["key"] == key)


def _run(tmp_path: Path, rows: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    csv_path = write_csv(tmp_path / "sales.csv", rows)
    args = {"start": None, "end": None, "grain": "day", "max_points": 1500, **kwargs}
    with module_context(csv_path) as ctx:
        return compute(ctx, args["start"], args["end"], args["grain"], args["max_points"])


def test_kpis_compare_the_range_with_the_window_just_before_it(tmp_path: Path) -> None:
    found = _run(tmp_path, _rows(), start=date(2024, 1, 8), end=date(2024, 1, 14))
    assert found["range"] == {"start": "2024-01-08", "end": "2024-01-14"}
    assert found["previous_range"] == {"start": "2024-01-01", "end": "2024-01-07"}
    revenue = _kpi(found, "net_revenue")
    # 7 sales of 10 minus a refund of 5, against 6 sales of 10 (2024-01-05 had none)
    assert revenue["value"] == 65 and revenue["previous_value"] == 60
    assert revenue["change"] == pytest.approx(100 * 5 / 60)
    assert revenue["change_unit"] == "percent" and revenue["unit"] == "money"
    assert _kpi(found, "orders")["value"] == 7  # the refund row is not an order
    assert _kpi(found, "aov")["value"] == pytest.approx(10.0)  # gross sales per order
    assert _kpi(found, "units")["value"] == 6
    margin = _kpi(found, "margin_pct")
    assert margin["value"] == pytest.approx(100 * (65 - 28) / 65)
    assert margin["change"] == pytest.approx(margin["value"] - 100 * (60 - 24) / 60)
    assert margin["change_unit"] == "points"
    assert found["sample_size"] == 8 and found["grain"] == "day"
    assert [e["sql"] for e in found["evidence"]] and "BETWEEN" in found["evidence"][0]["sql"]


def test_series_is_filled_with_zero_days_and_marks_no_partial_day(tmp_path: Path) -> None:
    found = _run(tmp_path, _rows())
    series = found["series"]
    assert len(series) == 14 and not any(p["partial"] for p in series)
    gap = next(p for p in series if p["period"] == "2024-01-05")
    assert gap["net_revenue"] == 0 and gap["rows"] == 0 and gap["aov"] is None
    refund_day = next(p for p in series if p["period"] == "2024-01-10")
    assert refund_day["net_revenue"] == 5


def test_no_change_when_the_previous_window_is_before_the_data(tmp_path: Path) -> None:
    found = _run(tmp_path, _rows())  # the whole data: nothing before it
    revenue = _kpi(found, "net_revenue")
    assert revenue["change"] is None and revenue["previous_value"] is None
    assert "starts on 2024-01-01" in revenue["reason"]


def test_weeks_start_on_monday_and_edges_are_partial(tmp_path: Path) -> None:
    found = _run(tmp_path, _rows(), start=date(2024, 1, 3), end=date(2024, 1, 14), grain="week")
    assert [(p["period"], p["partial"]) for p in found["series"]] == [
        ("2024-01-01", True),
        ("2024-01-08", False),
    ]
    assert any("partly inside" in w for w in found["warnings"])
    assert found["series"][0]["net_revenue"] == 40  # 01-03 to 01-07 in range, 01-05 had no sale


def test_a_range_wider_than_the_data_is_trimmed_with_a_warning(tmp_path: Path) -> None:
    found = _run(tmp_path, _rows(), start=date(2023, 6, 1), end=date(2024, 6, 1), grain="month")
    assert found["range"] == {"start": "2024-01-01", "end": "2024-01-14"}
    assert any("trimmed" in w for w in found["warnings"])
    assert found["series"][0]["partial"] is True  # January is cut at the 14th


def test_missing_cost_and_quantity_are_unavailable_with_a_reason(tmp_path: Path) -> None:
    rows = [
        {k: r[k] for k in ("order_id", "customer_id", "order_date", "revenue")} for r in _rows()
    ]
    found = _run(tmp_path, rows, start=date(2024, 1, 8))
    margin, units = _kpi(found, "margin_pct"), _kpi(found, "units")
    assert margin["available"] is False and "cost" in margin["reason"]
    assert units["available"] is False and "quantity" in units["reason"]
    assert all(p["margin_pct"] is None for p in found["series"])


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"start": date(2024, 1, 9), "end": date(2024, 1, 2)}, "invalid_range"),
        ({"start": date(2025, 1, 1), "end": date(2025, 2, 1)}, "range_outside_data"),
        ({"max_points": 5}, "range_too_long"),
    ],
)
def test_bad_requests_are_refused_with_a_code(
    tmp_path: Path, kwargs: dict[str, Any], code: str
) -> None:
    with pytest.raises(SeriesUnavailable) as caught:
        _run(tmp_path, _rows(), **kwargs)
    assert caught.value.code == code and caught.value.message


def test_no_date_column_means_no_series(tmp_path: Path) -> None:
    rows = [
        {"order_id": "1", "customer_id": "C1", "revenue": 10},
        {"order_id": "2", "customer_id": "C2", "revenue": 20},
    ]
    with pytest.raises(SeriesUnavailable) as caught:
        _run(tmp_path, rows)
    assert caught.value.code == "not_enough_data" and "date" in caught.value.message


def test_month_buckets_cross_the_year() -> None:
    window = Window(date(2023, 11, 15), date(2024, 2, 1))
    assert buckets(window, "month") == [
        date(2023, 11, 1),
        date(2023, 12, 1),
        date(2024, 1, 1),
        date(2024, 2, 1),
    ]
