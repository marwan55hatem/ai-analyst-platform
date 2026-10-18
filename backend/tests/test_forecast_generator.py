"""The ground-truth generator's longer-history and daily options leave Step 4 untouched."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from tests.ground_truth_fixtures import GENERATOR, generator

# md5 of the default (2-year) English CSV and facts JSON as produced before Step 5
DEFAULT_CSV_MD5 = "6094c9ec25799ef0549ad1c57e3155c0"
DEFAULT_JSON_MD5 = "2e5e729261c4908b97b239ba64aa4f3f"


def test_the_default_two_year_dataset_is_byte_for_byte_what_step_4_used(tmp_path: Path) -> None:
    csv_path, json_path, facts = generator().generate(tmp_path)
    assert hashlib.md5(csv_path.read_bytes()).hexdigest() == DEFAULT_CSV_MD5
    assert hashlib.md5(json_path.read_bytes()).hexdigest() == DEFAULT_JSON_MD5
    assert "planted" not in facts and facts["last_date"] == "2024-12-31"


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
