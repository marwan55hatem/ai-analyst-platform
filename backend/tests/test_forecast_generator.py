"""The ground-truth generator's longer-history and daily options leave Step 4 untouched."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from tests.ground_truth_fixtures import GENERATOR, generator

# md5 of the default (2-year) English CSV as produced before Step 5. The CSV writer fixes its own
# line endings (newline=""), so its bytes are identical on every OS.
DEFAULT_CSV_MD5 = "6094c9ec25799ef0549ad1c57e3155c0"

# The facts JSON is compared by parsed value, not by md5: raw JSON bytes used to depend on the OS
# (Path.write_text turned "\n" into "\r\n" on Windows, so Windows and Linux hashes differed).
DEFAULT_FACTS: dict[str, Any] = {
    "seed": 11,
    "lang": "en",
    "refunds": False,
    "row_count": 5392,
    "order_count": 5392,
    "gross_revenue_cents": 303068482,
    "returns_total_cents": 0,
    "returns_count": 0,
    "net_revenue_cents": 303068482,
    "total_revenue": 3030684.82,
    "aov_cents": 56207.06268545994,
    "units_gross": 11630,
    "units_returned": 0,
    "total_cost_cents": 181840689,
    "margin_pct": 40.000132049363025,
    "customer_count": 619,
    "product_count": 30,
    "region_count": 4,
    "top_product": "Item-07",
    "top_product_revenue_cents": 84459000,
    "top_region": "North",
    "peak_month": 12,
    "monthly_revenue_cents": {
        "2023-01": 8505095,
        "2023-02": 8060345,
        "2023-03": 11522395,
        "2023-04": 8821679,
        "2023-05": 9428731,
        "2023-06": 9164089,
        "2023-07": 9471376,
        "2023-08": 11180935,
        "2023-09": 8707243,
        "2023-10": 11774153,
        "2023-11": 13094566,
        "2023-12": 28314823,
        "2024-01": 9993571,
        "2024-02": 10776442,
        "2024-03": 11235882,
        "2024-04": 12364957,
        "2024-05": 10922395,
        "2024-06": 10109929,
        "2024-07": 11594259,
        "2024-08": 12426086,
        "2024-09": 10370094,
        "2024-10": 12998028,
        "2024-11": 14776674,
        "2024-12": 37454735,
    },
    "dead_stock": ["Item-29", "Item-30"],
    "dead_stock_days": 90,
    "first_date": "2023-01-01",
    "last_date": "2024-12-31",
    "ytd_cents": 165023052,
    "prior_ytd_cents": 138045430,
    "yoy_pct": 19.54256797925147,
    "first_cohort": {"month": "2023-01", "size": 17, "month1_retained": 16},
    "cohort_sizes": {
        "2023-01": 17,
        "2023-02": 16,
        "2023-03": 15,
        "2023-04": 27,
        "2023-05": 29,
        "2023-06": 12,
        "2023-07": 20,
        "2023-08": 16,
        "2023-09": 23,
        "2023-10": 24,
        "2023-11": 38,
        "2023-12": 42,
        "2024-01": 21,
        "2024-02": 25,
        "2024-03": 25,
        "2024-04": 23,
        "2024-05": 25,
        "2024-06": 28,
        "2024-07": 26,
        "2024-08": 18,
        "2024-09": 27,
        "2024-10": 26,
        "2024-11": 31,
        "2024-12": 65,
    },
    "month1_retention_pct": 56.31768953068592,
    "customers": 619,
}


def test_the_default_two_year_dataset_is_what_step_4_used(tmp_path: Path) -> None:
    csv_path, json_path, facts = generator().generate(tmp_path)
    assert hashlib.md5(csv_path.read_bytes()).hexdigest() == DEFAULT_CSV_MD5
    assert facts == DEFAULT_FACTS and "planted" not in facts
    assert json.loads(json_path.read_text(encoding="utf-8")) == DEFAULT_FACTS


def test_facts_json_uses_lf_newlines_on_every_platform(tmp_path: Path) -> None:
    _, json_path, _ = generator().generate(tmp_path)
    _, daily_path, _ = generator().generate_daily(tmp_path)
    assert b"\r" not in json_path.read_bytes() and b"\r" not in daily_path.read_bytes()


def test_three_years_plants_a_trend_with_a_held_out_final_year(tmp_path: Path) -> None:
    csv_path, json_path, facts = generator().generate(tmp_path, years=3)
    assert csv_path.name == "ground_truth_en_3y.csv" and json_path.exists()
    assert facts["first_date"] == "2023-01-01" and facts["last_date"] == "2025-12-31"
    planted = facts["planted"]
    assert planted["holdout_year"] == 2025 and planted["training_months"] == 24
    assert set(planted["holdout_monthly_cents"]) == {f"2025-{m:02d}" for m in range(1, 13)}
    yearly = {
        y: sum(v for k, v in facts["monthly_revenue_cents"].items() if k.startswith(str(y)))
        for y in (2023, 2024, 2025)
    }
    assert yearly[2023] < yearly[2024] < yearly[2025]  # the planted upward trend
    assert facts["peak_month"] == 12  # and the December season survives
    assert facts["dead_stock"] == ["Item-29", "Item-30"]  # Step 4 facts still hold
    assert json.loads(json_path.read_text(encoding="utf-8"))["planted"] == planted


def test_longer_histories_work_for_arabic_and_refunds_and_reject_one_year(tmp_path: Path) -> None:
    _, _, facts = generator().generate(tmp_path, lang="ar", refunds=True, years=3)
    assert facts["returns_count"] == 25 and facts["lang"] == "ar"
    with pytest.raises(ValueError, match="at least 2"):
        generator().generate(tmp_path, years=1)


def test_the_daily_variant_has_a_planted_trend_and_weekly_pattern(tmp_path: Path) -> None:
    csv_path, _, facts = generator().generate_daily(tmp_path)
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "order_date,revenue" and len(lines) == facts["days"] + 1
    revenue = np.array([float(line.split(",")[1]) for line in lines[1:]])
    assert revenue[-14:].mean() > revenue[:14].mean() * 1.15  # growth per day
    weekdays = np.arange(len(revenue)) % 7  # 2023-01-01 is a Sunday; effects are Mon..Sun
    saturday = revenue[weekdays == 5].mean()
    sunday = revenue[weekdays == 0].mean()
    assert saturday > sunday * 1.5  # the planted Saturday peak vs Sunday dip
    assert sum(int(round(v * 100)) for v in revenue) == facts["net_revenue_cents"]


def test_the_command_line_accepts_years_seed_and_daily_series(tmp_path: Path) -> None:
    run = [sys.executable, str(GENERATOR), str(tmp_path)]
    done = subprocess.run([*run, "--years", "3", "--seed", "5"], capture_output=True, text=True)
    assert done.returncode == 0 and (tmp_path / "ground_truth_en_3y.csv").exists()
    done = subprocess.run([*run, "--daily-series"], capture_output=True, text=True)
    assert done.returncode == 0 and (tmp_path / "ground_truth_daily.csv").exists()
