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

`anomalies=True` (CLI `--anomalies`) plants 5 dated point anomalies (3 spikes, one of them
caused by bulk orders of a single product, and 2 drops), a +50% level shift and row-level oddities
(unusual discounts in an extra `discount_pct` column, zero quantities, extreme prices).
`segments=True` (CLI `--segments`) replaces the random customer pool with 4 behavioural groups
(VIP, regular, lapsed, new) whose members are listed in the facts. Both use their own random
streams and only change the output when switched on.

`churn=True` (CLI `--churn`) replaces the customer pool with 1,500 customers who each sign up, buy at
their own rate and may leave on a known date; before leaving their purchase rate falls linearly to a
quarter over `CHURN_DECLINE_DAYS` (the planted "declining frequency" behaviour). It cannot be combined
with `segments` (both decide who buys). `elasticity=True` (CLI `--elasticity`) moves the prices of
category `ELASTIC_CATEGORY` by a weekly multiplier and draws that category's demand from it with a
known price elasticity (`true_elasticity`, default -1.5); its lines are added to orders already placed
that day, so no customer's purchase days change. `drivers=True` (CLI `--drivers`) adds a
`discount_pct` column whose discount lifts the units of a line (the planted driver). Each of these
uses its own random stream.

`years=3` (CLI `--years 3`) extends the history to 2023-2025 with a PLANTED upward trend
(`TREND_PER_YEAR`) on top of the same monthly seasonality, so the last year can be held out when
testing forecasts. The default (2 years) is byte-for-byte what Step 4 expects. `--daily-series`
writes a separate short daily file with a planted trend and weekly seasonality.

Run `python ground_truth_generator.py <output_dir> [--lang ar] [--refunds] [--years 3]
[--anomalies] [--segments] [--churn] [--elasticity] [--drivers] [--daily-series] [--seed N]`
to write the files.
"""

import csv
import json
import math
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

# Planted anomalies (all dates fall inside every supported history, which starts in 2023)
SPIKES = (date(2023, 3, 14), date(2023, 8, 22), date(2024, 2, 20))
DROPS = (date(2023, 5, 9), date(2023, 10, 17))
CAUSE_PRODUCT = "Item-12"
CAUSE_SPIKE = date(2023, 8, 22)
SPIKE_FACTOR = 12.0
LEVEL_SHIFT = date(2024, 6, 1)
LEVEL_SHIFT_FACTOR = 1.5
UNUSUAL_DISCOUNTS = 6
ZERO_QUANTITY_ROWS = 4
EXTREME_PRICE_ROWS = 5
EXTREME_PRICE_FACTOR = 20
NORMAL_DISCOUNTS = [0, 0, 0, 5, 10]
# Planted customer groups: (name, members, orders per member over its active window)
GROUPS: tuple[tuple[str, int, float], ...] = (
    ("vip", 40, 25.0),
    ("regular", 160, 8.0),
    ("lapsed", 160, 5.0),
    ("new", 220, 1.5),
)
LAPSED_UNTIL = date(2023, 9, 30)
NEW_DAYS = 90
ANOMALY_SEED_OFFSET = 7919
SEGMENT_SEED_OFFSET = 104729
# Planted churn: customers sign up, buy at their own rate and may leave on a known date
CHURN_CUSTOMERS = 1500
CHURN_MEAN_GAP_DAYS = 30.0
CHURN_RATE_SPREAD = 0.5  # sd of the log purchase rate across customers
CHURN_MEAN_LIFETIME_DAYS = 600.0
CHURN_DECLINE_DAYS = 120
CHURN_DECLINE_TO = 0.25
CHURN_SEED_OFFSET = 1299709
# Planted price elasticity: weekly price multipliers of one category and the demand they cause
ELASTIC_CATEGORY = "Office"
TRUE_ELASTICITY = -1.5
ELASTIC_MULTIPLIERS = (0.75, 0.875, 1.0, 1.125, 1.25)
ELASTIC_LINES_PER_DAY = 10.0
ELASTIC_SEED_OFFSET = 15485863
# Planted driver: a discount of d (fraction) lifts the units of a line by (1 + DRIVER_LIFT x d)
DRIVER_DISCOUNTS = (0, 10, 20, 30)
DRIVER_DISCOUNT_WEIGHTS = (4, 2, 2, 2)
DRIVER_LIFT = 4.0
DRIVER_SEED_OFFSET = 32452843

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
DISCOUNT_HEADER = {"en": "discount_pct", "ar": "نسبة_الخصم"}
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


def _build_rows(
    seed: int,
    refunds: bool,
    years: int = DEFAULT_YEARS,
    anomalies: bool = False,
    segments: bool = False,
    churn: bool = False,
    elasticity: float | None = None,
    drivers: bool = False,
) -> tuple[list[Row], dict[str, Any]]:
    rnd = random.Random(seed)
    products = _products(rnd)
    pool = [f"C{n:04d}" for n in range(1, CUSTOMER_POOL + 1)]
    rnd.shuffle(pool)
    seen: list[str] = []
    rows: list[Row] = []
    order_no = 100000
    day = START
    end = END if years == DEFAULT_YEARS else _end_date(years)
    groups = _Groups(random.Random(seed + SEGMENT_SEED_OFFSET), end) if segments else None
    population = _Churners(random.Random(seed + CHURN_SEED_OFFSET), end) if churn else None
    elastic = (
        _Elastic(random.Random(seed + ELASTIC_SEED_OFFSET), elasticity)
        if elasticity is not None
        else None
    )
    driver_rnd = random.Random(seed + DRIVER_SEED_OFFSET) if drivers else None
    while day <= end:
        live = [p for p in products if not (p["name"] in DEAD_PRODUCTS and day > DEAD_AFTER)]
        if elastic is not None:  # the elastic category sells only through its own demand below
            live = [p for p in live if p["category"] != ELASTIC_CATEGORY]
        weights = [p["weight"] for p in live]
        buyers = population.buyers(day) if population is not None else []
        count = len(buyers) if population is not None else _orders_on(day, rnd, years)
        first_today = len(rows)
        for n in range(count):
            order_no += 1
            group = None
            if population is not None:
                customer = buyers[n]
            elif groups is not None:
                group, customer = groups.pick(day)
            elif pool and (not seen or rnd.random() < NEW_CUSTOMER_RATE):
                customer = pool.pop()
                seen.append(customer)
            else:
                customer = rnd.choice(seen)
            product = rnd.choices(live, weights)[0]
            quantity = rnd.choices([1, 2, 3, 4, 5], [5, 3, 2, 1, 1])[0]
            if group == "vip":
                quantity *= 3
            elif group == "new":
                quantity = 1
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
        if elastic is not None and len(rows) > first_today:
            elastic.add_lines(rows, first_today, products, day)
        day += timedelta(days=1)
    if driver_rnd is not None:
        _plant_driver(rows, driver_rnd)
    planted: dict[str, Any] = {}
    if anomalies:
        rows, planted["anomalies"] = _plant_anomalies(
            rows, products, random.Random(seed + ANOMALY_SEED_OFFSET), order_no
        )
    if groups is not None:
        planted["segments"] = groups.members(rows)
    if population is not None:
        planted["churn"] = population.facts(rows)
    if elastic is not None:
        planted["elasticity"] = elastic.facts()
    if driver_rnd is not None:
        planted["drivers"] = {
            "driver": "discount",
            "column": "discount_pct",
            "levels_pct": list(DRIVER_DISCOUNTS),
            "units_multiplier": f"1 + {DRIVER_LIFT} x discount fraction",
        }
    if refunds:
        rows.extend(_refund_rows(rows, rnd, end))
    return rows, planted


class _Groups:
    """Planted customer groups: who may buy on a day, and how often each group buys."""

    def __init__(self, rnd: random.Random, end: date) -> None:
        self.rnd = rnd
        ids = [f"C{n:04d}" for n in range(1, sum(g[1] for g in GROUPS) + 1)]
        rnd.shuffle(ids)
        self.ids: dict[str, list[str]] = {}
        for name, size, _ in GROUPS:
            self.ids[name], ids = ids[:size], ids[size:]
        self.new_from = end - timedelta(days=NEW_DAYS - 1)
        total = (end - START).days + 1
        window = {
            "vip": total,
            "regular": total,
            "lapsed": (LAPSED_UNTIL - START).days + 1,
            "new": NEW_DAYS,
        }
        # orders per day each group places, relative to the others
        self.rate = {name: size * per / window[name] for name, size, per in GROUPS}

    def pick(self, day: date) -> tuple[str, str]:
        names = ["vip", "regular"]
        if day <= LAPSED_UNTIL:
            names.append("lapsed")
        if day >= self.new_from:
            names.append("new")
        group = self.rnd.choices(names, [self.rate[n] for n in names])[0]
        return group, self.rnd.choice(self.ids[group])

    def members(self, rows: list[Row]) -> dict[str, list[str]]:
        buyers = {r["customer_id"] for r in rows if r["revenue_cents"] > 0}
        return {name: sorted(c for c in ids if c in buyers) for name, ids in self.ids.items()}


class _Churners:
    """Planted churn: who buys on a day, given sign-up, own rate and (maybe) a leaving date."""

    def __init__(self, rnd: random.Random, end: date) -> None:
        self.rnd = rnd
        span = (end - START).days
        self.people: list[tuple[str, date, float, date | None]] = []
        for n in range(1, CHURN_CUSTOMERS + 1):
            signup = START + timedelta(days=rnd.randrange(max(1, span - 60)))
            rate = math.exp(rnd.gauss(math.log(1.0 / CHURN_MEAN_GAP_DAYS), CHURN_RATE_SPREAD))
            life = int(rnd.expovariate(1.0 / CHURN_MEAN_LIFETIME_DAYS))
            leaves = signup + timedelta(days=life)
            self.people.append(
                (f"C{n:04d}", signup, min(rate, 0.5), leaves if leaves <= end else None)
            )

    def buyers(self, day: date) -> list[str]:
        """Return the customers who place an order on `day` (rate falls before they leave)."""
        out = []
        season = SEASON.get(day.month, 1.0)
        for customer, signup, rate, leaves in self.people:
            if day < signup or (leaves is not None and day >= leaves):
                continue
            factor = 1.0
            if leaves is not None and (leaves - day).days <= CHURN_DECLINE_DAYS:
                share = (leaves - day).days / CHURN_DECLINE_DAYS
                factor = CHURN_DECLINE_TO + (1.0 - CHURN_DECLINE_TO) * share
            if self.rnd.random() < rate * factor * season:
                out.append(customer)
        return out

    def facts(self, rows: list[Row]) -> dict[str, Any]:
        """Leaving dates of the customers who left and bought at least once."""
        buyers = {r["customer_id"] for r in rows if r["revenue_cents"] > 0}
        left = {c: d.isoformat() for c, _, _, d in self.people if d is not None and c in buyers}
        return {
            "customers": len(buyers),
            "left": dict(sorted(left.items())),
            "decline_days": CHURN_DECLINE_DAYS,
            "decline_to": CHURN_DECLINE_TO,
        }


class _Elastic:
    """Planted price elasticity: weekly price multipliers of one category and its demand."""

    def __init__(self, rnd: random.Random, elasticity: float) -> None:
        self.rnd = rnd
        self.elasticity = elasticity
        self.weeks: dict[date, float] = {}

    def multiplier(self, day: date) -> float:
        week = day - timedelta(days=day.weekday())  # prices change on Mondays
        if week not in self.weeks:
            self.weeks[week] = self.rnd.choice(ELASTIC_MULTIPLIERS)
        return self.weeks[week]

    def _poisson(self, mean: float) -> int:
        limit, k, p = math.exp(-mean), 0, 1.0
        while True:
            p *= self.rnd.random()
            if p <= limit:
                return k
            k += 1

    def add_lines(
        self, rows: list[Row], first: int, products: list[dict[str, Any]], day: date
    ) -> None:
        """Add the category's lines (demand = base x price multiplier ^ elasticity) to today's orders."""
        m = self.multiplier(day)
        elastic = [p for p in products if p["category"] == ELASTIC_CATEGORY]
        mean = ELASTIC_LINES_PER_DAY * SEASON.get(day.month, 1.0) * m**self.elasticity
        for _ in range(self._poisson(mean)):
            host = rows[first + self.rnd.randrange(len(rows) - first)]
            product = self.rnd.choices(elastic, [p["weight"] for p in elastic])[0]
            price = round(product["price_cents"] * m)
            quantity = self.rnd.choice([1, 1, 2, 3])
            rows.append(
                {
                    **host,
                    "product": product["name"],
                    "category": product["category"],
                    "quantity": quantity,
                    "price_cents": price,
                    "revenue_cents": quantity * price,
                    "cost_cents": quantity * product["price_cents"] * 6 // 10,
                }
            )

    def facts(self) -> dict[str, Any]:
        return {
            "category": ELASTIC_CATEGORY,
            "true_elasticity": self.elasticity,
            "price_multipliers": list(ELASTIC_MULTIPLIERS),
            "weeks": len(self.weeks),
        }


def _plant_driver(rows: list[Row], rnd: random.Random) -> None:
    """Give every line a discount that lifts its units (unbiased stochastic rounding)."""
    for r in rows:
        pct = rnd.choices(DRIVER_DISCOUNTS, DRIVER_DISCOUNT_WEIGHTS)[0]
        before = r["quantity"]
        lifted = before * (1.0 + DRIVER_LIFT * pct / 100.0)
        r["quantity"] = int(lifted) + (1 if rnd.random() < lifted - int(lifted) else 0)
        r["discount_pct"] = pct
        r["revenue_cents"] = r["quantity"] * r["price_cents"] * (100 - pct) // 100
        r["cost_cents"] = r["cost_cents"] * r["quantity"] // before


def _plant_anomalies(
    rows: list[Row], products: list[dict[str, Any]], rnd: random.Random, order_no: int
) -> tuple[list[Row], dict[str, Any]]:
    """Plant spikes, drops, a level shift and row-level oddities; rows stay in date order."""
    by_day: dict[date, list[Row]] = defaultdict(list)
    for r in rows:
        by_day[r["order_date"]].append(r)
    customers = sorted({r["customer_id"] for r in rows})
    cause = next(p for p in products if p["name"] == CAUSE_PRODUCT)

    def copy_order(template: Row, day: date) -> Row:
        nonlocal order_no
        order_no += 1
        return {**template, "order_id": f"O{order_no}", "order_date": day}

    for day in sorted(by_day):
        if day >= LEVEL_SHIFT:
            today = list(by_day[day])
            extra = max(0, round(len(today) * (LEVEL_SHIFT_FACTOR - 1.0) + rnd.random() - 0.5))
            by_day[day].extend(copy_order(rnd.choice(today), day) for _ in range(extra))
    for day in SPIKES:
        today = by_day[day]
        # SPIKE_FACTOR times a normal day (the median of the 4 weeks around it), not of this day
        around = sorted(
            sum(r["revenue_cents"] for r in by_day[day + timedelta(days=k)])
            for k in range(-14, 15)
            if k
        )
        target = around[len(around) // 2] * SPIKE_FACTOR - sum(r["revenue_cents"] for r in today)
        added = 0
        while added < target:
            template = rnd.choice(rows)
            if day == CAUSE_SPIKE:
                template = {
                    **template,
                    "customer_id": rnd.choice(customers),
                    "product": cause["name"],
                    "category": cause["category"],
                    "quantity": 5,
                    "price_cents": cause["price_cents"],
                }
            row = copy_order(template, day)
            row["revenue_cents"] = row["quantity"] * row["price_cents"]
            today.append(row)
            added += row["revenue_cents"]
    for day in DROPS:
        by_day[day] = [min(by_day[day], key=lambda r: (r["revenue_cents"], r["order_id"]))]
    out = [r for day in sorted(by_day) for r in by_day[day]]
    for r in out:
        drawn = rnd.choice(NORMAL_DISCOUNTS)  # always drawn, so the stream stays the same
        r.setdefault("discount_pct", drawn)
        r["list_price_cents"] = r["price_cents"]
    special = set(SPIKES) | set(DROPS)
    quiet = [r for r in out if r["order_date"] not in special]
    discounted = rnd.sample(quiet, UNUSUAL_DISCOUNTS)
    rest = [r for r in quiet if r not in discounted]
    zero = rnd.sample(rest, ZERO_QUANTITY_ROWS)
    cheap = [r for r in rest if r not in zero and r["price_cents"] <= 1250]
    pricey = rnd.sample(cheap, EXTREME_PRICE_ROWS)
    for r in discounted:
        r["discount_pct"] = rnd.choice([70, 80, 90])
    for r in zero:
        r["quantity"], r["discount_pct"] = 0, 0
    for r in pricey:
        r["price_cents"] *= EXTREME_PRICE_FACTOR
        r["discount_pct"] = 0
    for r in out:
        r["revenue_cents"] = r["quantity"] * r["price_cents"] * (100 - r["discount_pct"]) // 100
        r["cost_cents"] = r["quantity"] * r.pop("list_price_cents") * 6 // 10
    planted = {
        "points": [
            {
                "date": d.isoformat(),
                "kind": "spike" if d in SPIKES else "drop",
                "cause_product": CAUSE_PRODUCT if d == CAUSE_SPIKE else None,
            }
            for d in sorted(special)
        ],
        "level_shift": {"date": LEVEL_SHIFT.isoformat(), "factor": LEVEL_SHIFT_FACTOR},
        "unusual_discount_orders": sorted(r["order_id"] for r in discounted),
        "zero_quantity_orders": sorted(r["order_id"] for r in zero),
        "extreme_price_orders": sorted(r["order_id"] for r in pricey),
    }
    return out, planted


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
    anomalies: bool = False,
    segments: bool = False,
    churn: bool = False,
    elasticity: bool = False,
    drivers: bool = False,
    true_elasticity: float = TRUE_ELASTICITY,
) -> tuple[Path, Path, dict[str, Any]]:
    """Write the CSV and its facts JSON into `directory`; return (csv, json, facts)."""
    if years < DEFAULT_YEARS:
        raise ValueError("years must be at least 2")
    if churn and segments:
        raise ValueError("churn and segments both decide who buys; use one of them")
    directory.mkdir(parents=True, exist_ok=True)
    rows, planted = _build_rows(
        seed,
        refunds,
        years,
        anomalies,
        segments,
        churn,
        true_elasticity if elasticity else None,
        drivers,
    )
    facts = compute_facts(rows, seed, lang, refunds, years)
    if facts["customer_count"] < 500 or facts["dead_stock"] != sorted(DEAD_PRODUCTS):
        raise AssertionError("generator no longer satisfies its planted facts")
    if "anomalies" in planted:
        position = {r["order_id"]: n for n, r in enumerate(rows, start=1)}
        for kind in ("unusual_discount", "zero_quantity", "extreme_price"):
            orders = planted["anomalies"][f"{kind}_orders"]
            planted["anomalies"][f"{kind}_rows"] = sorted(position[o] for o in orders)
    if planted:
        facts["planted"] = {**facts.get("planted", {}), **planted}
    suffix = "" if years == DEFAULT_YEARS else f"_{years}y"
    suffix += "_anomalies" if anomalies else ""
    suffix += "_segments" if segments else ""
    suffix += "_churn" if churn else ""
    suffix += "_elasticity" if elasticity else ""
    suffix += "_drivers" if drivers else ""
    discount = anomalies or drivers
    stem = f"ground_truth_{lang}{'_refunds' if refunds else ''}{suffix}"
    csv_path, json_path = directory / f"{stem}.csv", directory / f"{stem}.json"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADERS[lang] + ([DISCOUNT_HEADER[lang]] if discount else []))
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
                + ([r.get("discount_pct", 0)] if discount else [])
            )
    _write_json(json_path, facts, ensure_ascii=False)
    return csv_path, json_path, facts


def _write_json(path: Path, facts: dict[str, Any], ensure_ascii: bool = True) -> None:
    """Write facts with LF newlines on every OS (write_text would emit CRLF on Windows)."""
    with path.open("w", newline="\n", encoding="utf-8") as handle:
        handle.write(json.dumps(facts, indent=2, ensure_ascii=ensure_ascii))


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
    _write_json(json_path, facts)
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
            anomalies="--anomalies" in sys.argv,
            segments="--segments" in sys.argv,
            churn="--churn" in sys.argv,
            elasticity="--elasticity" in sys.argv,
            drivers="--drivers" in sys.argv,
        )
    print(f"wrote {written[0]}\nwrote {written[1]}")
