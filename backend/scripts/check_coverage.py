"""Fail unless line coverage of each guarded package meets the minimum.

Reads coverage.json (written by `pytest --cov-report=json`) and checks every package
separately, because coverage.py's own `fail_under` only checks the overall total.
"""

import argparse
import json
import sys
from pathlib import Path

GUARDED_PACKAGES = (
    "app/api",
    "app/core",
    "app/services/profiling",
    "app/ml",
    "app/ml/forecasting",
    "app/ml/anomaly",
    "app/ml/segmentation",
)
# Security-critical files held to a stricter bar than the package minimum.
GUARDED_FILES = {
    "app/services/query.py": 90.0,
    "app/services/analysis.py": 85.0,
    # Step 6 modules: each held to the package bar on its own
    "app/ml/modules/anomalies.py": 85.0,
    "app/ml/modules/segments.py": 85.0,
    "app/services/findings.py": 85.0,
}


def package_coverage(
    files: dict[str, dict[str, dict[str, float]]], package: str, only: str | None = None
) -> tuple[int, int]:
    """Return (covered_lines, statements) for files under `package` (or just `only`)."""
    covered = statements = 0
    for name, data in files.items():
        normalized = name.replace("\\", "/")
        if only is not None:
            if normalized != only:
                continue
        elif not normalized.startswith(package + "/"):
            continue
        statements += int(data["summary"]["num_statements"])
        covered += int(data["summary"]["covered_lines"])
    return covered, statements


def main() -> int:
    """Check each guarded package and return a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", default="coverage.json")
    parser.add_argument("--min", type=float, default=85.0, dest="minimum")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"coverage check: {path} not found; run pytest with --cov-report=json first")
        return 1
    files = json.loads(path.read_text())["files"]

    failed = False
    for package in GUARDED_PACKAGES:
        covered, statements = package_coverage(files, package)
        if statements == 0:
            print(f"coverage check: no statements measured for {package}")
            failed = True
            continue
        percent = 100.0 * covered / statements
        ok = percent >= args.minimum
        failed |= not ok
        verdict = "ok" if ok else "FAIL"
        print(f"coverage check: {package:<24} {percent:6.2f}% (min {args.minimum:.0f}%) {verdict}")
    for name, minimum in GUARDED_FILES.items():
        covered, statements = package_coverage(files, name, only=name)
        if statements == 0:
            print(f"coverage check: no statements measured for {name}")
            failed = True
            continue
        percent = 100.0 * covered / statements
        ok = percent >= minimum
        failed |= not ok
        verdict = "ok" if ok else "FAIL"
        print(f"coverage check: {name} {percent:6.2f}% (min {minimum:.0f}%) {verdict}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
