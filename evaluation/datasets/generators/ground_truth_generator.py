"""Seeded retail dataset with GROUND TRUTH: every planted fact is computed in plain Python.

Two years of orders (2023-01-01 to 2024-12-31), 500+ customers, 30 products, 4 regions. Money is
generated in integer cents, so totals are exact to the cent. The facts the analytics modules must
recover are computed here directly from the generated rows (independent of the module code) and
saved as JSON next to the CSV:

* total (net) revenue, gross sales, returns/refunds, orders, units and margin
* the top product and top region by revenue
* a December peak (December volume is multiplied) and the monthly revenue table
* two products that stop selling on 2024-07-31 (dead stock)
* cohort retention of the first cohort and across all cohorts

`refunds=True` adds negative rows (returns) with known count and total. `lang="ar"` writes the
same data with Arabic column names, to prove roles (not names) drive the modules.

`years=3` (CLI `--years 3`) extends the history to 2023-2025 with a PLANTED upward trend
(`TREND_PER_YEAR`) on top of the same monthly seasonality, so the last year can be held out when
testing forecasts. The default (2 years) is byte-for-byte what Step 4 expects. `--daily-series`
writes a separate short daily file with a planted trend and weekly seasonality.

Run `python ground_truth_generator.py <output_dir> [--lang ar] [--refunds] [--years 3]
[--daily-series] [--seed N]` to write the files.
"""

import csv
import json
import random
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

START = date(2023, 1, 1)
END = date(2024, 12, 31)
DEAD_AFTER = date(2024, 7, 31)
DEAD_DAYS = 90
N_PRODUCTS = 30
DEAD_PRODUCTS = ("Item-29", "Item-30")
REGIONS = ["North", "South", "East", "West"]
REGION_WEIGHTS = [4, 3, 2, 1]
CHANNELS = ["Online", "Store", "Partner"]
CATEGORIES = ["Electronics", "Furniture", "Office", "Grocery", "Apparel"]
SEASON = {11: 1.4, 12: 2.6, 1: 0.8, 2: 0.8, 6: 0.9}
CUSTOMER_POOL = 700
NEW_CUSTOMER_RATE = 0.12
REFUND_ROWS = 25
DEFAULT_YEARS = 2
# Extended histories: yearly growth of order volume, planted so forecasts have a trend to find.
TREND_PER_YEAR = 0.25
# Daily variant: weekday multipliers (Mon..Sun), base level and growth per day
WEEKDAY_EFFECT = [0.9, 1.0, 1.0, 1.1, 1.3, 1.6, 0.7]
DAILY_BASE_CENTS = 500_000
DAILY_GROWTH_PER_DAY = 0.002
DAILY_NOISE = 0.06

HEADERS = {
    "en": [
        "order_id",
        "order_date",
        "customer_id",
        "product",
        "category",
        "region",
        "channel",
        "quantity",
        "unit_price",
        "revenue",
        "cost",
    ],
    "ar": [
        "رقم_الطلب",
        "تاريخ_الطلب",
        "رقم_العميل",
        "المنتج",
        "الفئة",
        "المنطقة",
        "القناة",
        "الكمية",
        "سعر_الوحدة",
        "الإيرادات",
        "التكلفة",
    ],
}
KEYS = [
    "order_id",
    "order_date",
    "customer_id",
    "product",
    "category",
    "region",
    "channel",
    "quantity",
    "price_cents",
    "revenue_cents",
    "cost_cents",
]

Row = dict[str, Any]


def _products(rnd: random.Random) -> list[dict[str, Any]]:
    products = []
    for i in range(1, N_PRODUCTS + 1):
        products.append(
            {
                "name": f"Item-{i:02d}",
                "category": CATEGORIES[(i - 1) % len(CATEGORIES)],
                "price_cents": rnd.choice([499, 1250, 2999, 4500, 9900, 24900, 59900]),
                "weight": rnd.choice([1, 1, 2, 2, 3, 5]),
            }
        )
    # one clear best seller so the "top product" fact is unambiguous
    products[6]["price_cents"], products[6]["weight"] = 59900, 9
    return products


def _orders_on(day: date, rnd: random.Random, years: int = DEFAULT_YEARS) -> int:
    if years == DEFAULT_YEARS:
        base = 6.0 * SEASON.get(day.month, 1.0) * (1.0 if day.year == 2023 else 1.2)
    else:
        grown = 1.0 + TREND_PER_YEAR * (day - START).days / 365.25
        base = 6.0 * SEASON.get(day.month, 1.0) * grown
    return max(1, round(base) + rnd.choice([-1, 0, 0, 1]))


def _end_date(years: int) -> date:
    return date(START.year + years - 1, 12, 31)


def _build_rows(seed: int, refunds: bool, years: int = DEFAULT_YEARS) -> list[Row]:
    rnd = random.Random(seed)
    products = _products(rnd)
    pool = [f"C{n:04d}" for n in range(1, CUSTOMER_POOL + 1)]
    rnd.shuffle(pool)
    seen: list[str] = []
    rows: list[Row] = []
    order_no = 100000
    day = START
    end = END if years == DEFAULT_YEARS else _end_date(years)
    while day <= end:
        live = [p for p in products if not (p["name"] in DEAD_PRODUCTS and day > DEAD_AFTER)]
        weights = [p["weight"] for p in live]
        for _ in range(_orders_on(day, rnd, years)):
            order_no += 1
            if pool and (not seen or rnd.random() < NEW_CUSTOMER_RATE):
                customer = pool.pop()
                seen.append(customer)
            else:
                customer = rnd.choice(seen)
            product = rnd.choices(live, weights)[0]
            quantity = rnd.choices([1, 2, 3, 4, 5], [5, 3, 2, 1, 1])[0]
            revenue = quantity * product["price_cents"]
            rows.append(
                {
                    "order_id": f"O{order_no}",
                    "order_date": day,
                    "customer_id": customer,
                    "product": product["name"],
                    "category": product["category"],
                    "region": rnd.choices(REGIONS, REGION_WEIGHTS)[0],
                    "channel": rnd.choice(CHANNELS),
                    "quantity": quantity,
                    "price_cents": product["price_cents"],
                    "revenue_cents": revenue,
                    "cost_cents": revenue * 6 // 10,
                }
            )
        day += timedelta(days=1)
    if refunds:
        rows.extend(_refund_rows(rows, rnd, end))
    return rows


def _refund_rows(rows: list[Row], rnd: random.Random, end: date = END) -> list[Row]:
    top = _top(rows, "product")
    candidates = [r for r in rows if r["product"] not in DEAD_PRODUCTS and r["product"] != top]
    out = []
    for n, original in enumerate(rnd.sample(candidates, REFUND_ROWS), start=1):
        when = min(end, original["order_date"] + timedelta(days=rnd.randint(1, 20)))
        out.append(
            {
                **original,
                "order_id": f"R{n:04d}",
                "order_date": when,
                "quantity": -original["quantity"],
                "revenue_cents": -original["revenue_cents"],
                "cost_cents": -original["cost_cents"],
            }
        )
    return out


def _top(rows: list[Row], key: str) -> str:
    totals: dict[str, int] = defaultdict(int)
    for r in rows:
        if r["revenue_cents"] > 0:
            totals[r[key]] += r["revenue_cents"]
    return sorted(totals, key=lambda k: (-totals[k], k))[0]


def _month(day: date) -> str:
    return f"{day.year}-{day.month:02d}"


def _cohorts(rows: list[Row]) -> dict[str, Any]:
    """First-cohort and overall month-1 retention, from positive purchases of complete months."""
    months: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        if r["revenue_cents"] > 0:
            months[r["customer_id"]].add(_month(r["order_date"]))
    first_year = min(r["order_date"].year for r in rows)
    last_year = max(r["order_date"].year for r in rows)
    order = [f"{y}-{m:02d}" for y in range(first_year, last_year + 1) for m in range(1, 13)]
    index = {m: i for i, m in enumerate(order)}
    sizes: dict[str, int] = defaultdict(int)
    back: dict[str, int] = defaultdict(int)
    for have in months.values():
        first = min(have, key=lambda m: index[m])
        sizes[first] += 1
        if index[first] + 1 < len(order) and order[index[first] + 1] in have:
            back[first] += 1
    eligible = [c for c in sizes if index[c] + 1 < len(order)]
    first_cohort = order[0]
    return {
        "first_cohort": {
            "month": first_cohort,
            "size": sizes[first_cohort],
            "month1_retained": back[first_cohort],
        },
        "cohort_sizes": dict(sorted(sizes.items())),
        "month1_retention_pct": 100.0
        * sum(back[c] for c in eligible)
        / sum(sizes[c] for c in eligible),
        "customers": len(months),
    }


def compute_facts(
    rows: list[Row], seed: int, lang: str, refunds: bool, years: int = DEFAULT_YEARS
) -> dict[str, Any]:
    """Compute every planted fact directly from the rows."""
    positive = [r for r in rows if r["revenue_cents"] > 0]
    negative = [r for r in rows if r["revenue_cents"] < 0]
    gross = sum(r["revenue_cents"] for r in positive)
    returns = -sum(r["revenue_cents"] for r in negative)
    net = gross - returns
    cost = sum(r["cost_cents"] for r in rows)
    orders = len({r["order_id"] for r in positive})
    monthly: dict[str, int] = defaultdict(int)
    for r in rows:
        monthly[_month(r["order_date"])] += r["revenue_cents"]
    by_calendar: dict[int, list[int]] = defaultdict(list)
    for key, value in monthly.items():
        by_calendar[int(key[5:])].append(value)
    peak = max(by_calendar, key=lambda m: sum(by_calendar[m]) / len(by_calendar[m]))
    last = max(r["order_date"] for r in rows)
    cutoff = last - timedelta(days=DEAD_DAYS)
    last_sale: dict[str, date] = {}
    for r in positive:
        last_sale[r["product"]] = max(last_sale.get(r["product"], r["order_date"]), r["order_date"])
    dead = sorted(p for p, d in last_sale.items() if d < cutoff)
    region_totals: dict[str, int] = defaultdict(int)
    for r in positive:
        region_totals[r["region"]] += r["revenue_cents"]
    ytd = sum(r["revenue_cents"] for r in rows if r["order_date"].year == last.year)
    prior = sum(r["revenue_cents"] for r in rows if r["order_date"].year == last.year - 1)
    cohorts = _cohorts(rows)
    facts = {
        "seed": seed,
        "lang": lang,
        "refunds": refunds,
        "row_count": len(rows),
        "order_count": orders,
        "gross_revenue_cents": gross,
        "returns_total_cents": returns,
        "returns_count": len(negative),
        "net_revenue_cents": net,
        "total_revenue": net / 100,
        "aov_cents": gross / orders,
        "units_gross": sum(r["quantity"] for r in positive),
        "units_returned": -sum(r["quantity"] for r in negative),
        "total_cost_cents": cost,
        "margin_pct": 100.0 * (net - cost) / net,
        "customer_count": cohorts["customers"],
        "product_count": len({r["product"] for r in rows}),
        "region_count": len({r["region"] for r in rows}),
        "top_product": _top(rows, "product"),
        "top_product_revenue_cents": max(
            sum(r["revenue_cents"] for r in positive if r["product"] == p)
            for p in {r["product"] for r in positive}
        ),
        "top_region": sorted(region_totals, key=lambda k: (-region_totals[k], k))[0],
        "peak_month": peak,
        "monthly_revenue_cents": dict(sorted(monthly.items())),
        "dead_stock": dead,
        "dead_stock_days": DEAD_DAYS,
        "first_date": min(r["order_date"] for r in rows).isoformat(),
        "last_date": last.isoformat(),
        "ytd_cents": ytd,
        "prior_ytd_cents": prior,
        "yoy_pct": 100.0 * (ytd - prior) / prior,
        **cohorts,
    }
    if years != DEFAULT_YEARS:
        facts["planted"] = _planted(monthly, last.year, years)
    return facts


def _planted(monthly: dict[str, int], holdout_year: int, years: int) -> dict[str, Any]:
    """Facts for forecast tests: the planted trend and the held-out final year."""
    return {
        "years": years,
        "trend_per_year": TREND_PER_YEAR,
        "seasonal_multipliers": {str(m): v for m, v in sorted(SEASON.items())},
        "holdout_year": holdout_year,
        "holdout_monthly_cents": {
            k: v for k, v in monthly.items() if k.startswith(str(holdout_year))
        },
        "training_months": sum(1 for k in monthly if not k.startswith(str(holdout_year))),
    }


def _money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}{abs(cents) // 100}.{abs(cents) % 100:02d}"


def generate(
    directory: Path,
    seed: int = 11,
    lang: str = "en",
    refunds: bool = False,
    years: int = DEFAULT_YEARS,
) -> tuple[Path, Path, dict[str, Any]]:
    """Write the CSV and its facts JSON into `directory`; return (csv, json, facts)."""
    if years < DEFAULT_YEARS:
        raise ValueError("years must be at least 2")
    directory.mkdir(parents=True, exist_ok=True)
    rows = _build_rows(seed, refunds, years)
    facts = compute_facts(rows, seed, lang, refunds, years)
    if facts["customer_count"] < 500 or facts["dead_stock"] != sorted(DEAD_PRODUCTS):
        raise AssertionError("generator no longer satisfies its planted facts")
    suffix = "" if years == DEFAULT_YEARS else f"_{years}y"
    stem = f"ground_truth_{lang}{'_refunds' if refunds else ''}{suffix}"
    csv_path, json_path = directory / f"{stem}.csv", directory / f"{stem}.json"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADERS[lang])
        for r in rows:
            writer.writerow(
                [
                    r["order_id"],
                    r["order_date"].isoformat(),
                    r["customer_id"],
                    r["product"],
                    r["category"],
                    r["region"],
                    r["channel"],
                    r["quantity"],
                    _money(r["price_cents"]),
                    _money(r["revenue_cents"]),
                    _money(r["cost_cents"]),
                ]
            )
    json_path.write_text(json.dumps(facts, indent=2, ensure_ascii=False), encoding="utf-8")
    return csv_path, json_path, facts


def generate_daily(
    directory: Path, seed: int = 11, days: int = 140
) -> tuple[Path, Path, dict[str, Any]]:
    """Write a short daily revenue file (planted trend + weekly pattern) and its facts."""
    directory.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(seed)
    rows = []
    for i in range(days):
        day = START + timedelta(days=i)
        level = DAILY_BASE_CENTS * (1 + DAILY_GROWTH_PER_DAY * i) * WEEKDAY_EFFECT[day.weekday()]
        rows.append((day, round(level * (1 + rnd.gauss(0, DAILY_NOISE)))))
    facts = {
        "seed": seed,
        "days": days,
        "first_date": rows[0][0].isoformat(),
        "last_date": rows[-1][0].isoformat(),
        "weekday_effect": WEEKDAY_EFFECT,
        "growth_per_day": DAILY_GROWTH_PER_DAY,
        "base_cents": DAILY_BASE_CENTS,
        "net_revenue_cents": sum(c for _, c in rows),
    }
    csv_path = directory / "ground_truth_daily.csv"
    json_path = directory / "ground_truth_daily.json"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["order_date", "revenue"])
        for day, cents in rows:
            writer.writerow([day.isoformat(), _money(cents)])
    json_path.write_text(json.dumps(facts, indent=2), encoding="utf-8")
    return csv_path, json_path, facts


def _option(name: str, default: str) -> str:
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


if __name__ == "__main__":
    first = sys.argv[1] if len(sys.argv) > 1 else "."
    target = Path(first) if not first.startswith("--") else Path(".")
    chosen_seed = int(_option("--seed", "11"))
    if "--daily-series" in sys.argv:
        written = generate_daily(target, seed=chosen_seed)
    else:
        written = generate(
            target,
            seed=chosen_seed,
            lang=_option("--lang", "en"),
            refunds="--refunds" in sys.argv,
            years=int(_option("--years", str(DEFAULT_YEARS))),
        )
    print(f"wrote {written[0]}\nwrote {written[1]}")
