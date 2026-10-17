"""Messy numbers, booleans and dates parse the same way in profiling and cleaning."""

from datetime import datetime
from typing import Any

import duckdb
import pytest

from app.services.profiling.parsing import bool_sql, date_sql, looks_like_number, number_sql


def _eval(expr_builder: Any, value: str | None) -> Any:
    con = duckdb.connect()
    con.execute("CREATE TABLE t(v VARCHAR)")
    con.execute("INSERT INTO t VALUES (?)", [value])
    return con.execute(f"SELECT {expr_builder('v')} FROM t").fetchone()[0]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1234", 1234.0),
        ("-5", -5.0),
        ("$1,234.50", 1234.5),
        ("1,234.50 USD", 1234.5),
        ("€ 99", 99.0),
        ("EGP 1,000", 1000.0),
        ("١٢٫٥٠", 12.5),  # Arabic-Indic digits with an Arabic decimal separator
        ("١٬٢٣٤٫٥٠", 1234.5),  # plus an Arabic thousands separator
        ("٣", 3.0),
        ("15%", 0.15),
        ("١٥٪", 0.15),
        ("(1,200)", -1200.0),  # accounting negative
        ("1.234,50", 1234.5),  # European separators
        ("12,5", 12.5),  # decimal comma
        ("1,234", 1234.0),  # thousands comma
        ("1.234.567", 1234567.0),
        ("  42  ", 42.0),
        ("120 جنيه", 120.0),
        (".5", 0.5),
    ],
)
def test_number_parsing(text: str, expected: float) -> None:
    assert _eval(number_sql, text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["abc", "SKU-123", "", "12abc", "1/2/2024", "N/A", "--"])
def test_non_numbers_are_null(text: str) -> None:
    assert _eval(number_sql, text) is None


def test_null_stays_null() -> None:
    assert _eval(number_sql, None) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [("yes", True), ("Y", True), ("TRUE", True), ("نعم", True), ("no", False), ("لا", False)],
)
def test_booleans(text: str, expected: bool) -> None:
    assert _eval(bool_sql, text) is expected


def test_unknown_boolean_is_null() -> None:
    assert _eval(bool_sql, "maybe") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2024-03-05", datetime(2024, 3, 5)),
        ("2024/03/05", datetime(2024, 3, 5)),
        ("2024-03-05 14:30:00", datetime(2024, 3, 5, 14, 30)),
        ("25/04/2024", datetime(2024, 4, 25)),  # only day-first is possible
        ("04/25/2024", datetime(2024, 4, 25)),  # only month-first is possible
        ("25-04-2024", datetime(2024, 4, 25)),
        ("25.04.2024", datetime(2024, 4, 25)),
        ("5 Mar 2024", datetime(2024, 3, 5)),
        ("Mar 5, 2024", datetime(2024, 3, 5)),
        ("٢٥/٠٤/٢٠٢٤", datetime(2024, 4, 25)),  # Arabic-Indic digits
    ],
)
def test_date_parsing(text: str, expected: datetime) -> None:
    assert _eval(date_sql, text) == expected


def test_ambiguous_date_follows_the_chosen_order() -> None:
    assert _eval(lambda c: date_sql(c, dayfirst=True), "03/04/2024") == datetime(2024, 4, 3)
    assert _eval(lambda c: date_sql(c, dayfirst=False), "03/04/2024") == datetime(2024, 3, 4)


@pytest.mark.parametrize("text", ["not a date", "13/13/2024", "2024-13-45", "12"])
def test_non_dates_are_null(text: str) -> None:
    assert _eval(date_sql, text) is None


def test_python_twin_of_number_shape() -> None:
    assert looks_like_number("١٢٣")
    assert looks_like_number("12.5")
    assert not looks_like_number("abc")
