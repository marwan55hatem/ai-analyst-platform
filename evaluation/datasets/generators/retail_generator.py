"""Seeded generator for a retail dataset and its messy twin (reused by later steps).

`make_clean_csv` returns a tidy export; `make_messy_csv` returns the same business data as a
dirty export: mixed date formats, currency strings, Arabic-Indic digits, exact duplicates,
nulls, stray whitespace and inconsistent category case. Headers come in English and Arabic.
`planted_roles(variant, lang)` gives the ground truth for role-detection tests.

Run `python retail_generator.py <output_dir>` to write the four files for manual testing.
"""

import csv
import io
import random
import sys
from datetime import date, timedelta
from pathlib import Path

Row = dict[str, object]

# (canonical name, planted role)
COLUMNS: list[tuple[str, str]] = [
    ("order_id", "order_id"),
    ("order_date", "date"),
    ("delivery_date", "date"),
    ("customer_id", "customer_id"),
    ("product", "product"),
    ("category", "category"),
    ("region", "region"),
    ("channel", "channel"),
    ("status", "status"),
    ("quantity", "quantity"),
    ("unit_price", "price"),
    ("discount", "discount"),
    ("revenue", "revenue"),
    ("cost", "cost"),
    ("email", "email"),
    ("phone", "phone"),
    ("churned", "churn_flag"),
]
HEADERS: dict[tuple[str, str], list[str]] = {
    ("clean", "en"): [name for name, _ in COLUMNS],
    ("messy", "en"): [
        "Order ID",
        "Order Date",
        "Delivery Date",
        "Customer ID",
        "Product",
        "Category",
        "Region",
        "Channel",
        "Order Status",
        "Qty",
        "Unit Price ($)",
        "Discount %",
        "Revenue ($)",
        "Cost",
        "Customer Email",
        "Mobile Phone",
        "Churned",
    ],
    ("clean", "ar"): [
        "رقم_الطلب",
        "تاريخ_الطلب",
        "تاريخ_التسليم",
        "رقم_العميل",
        "المنتج",
        "الفئة",
        "المنطقة",
        "القناة",
        "الحالة",
        "الكمية",
        "سعر_الوحدة",
        "الخصم",
        "الإيرادات",
        "التكلفة",
        "البريد_الإلكتروني",
        "رقم_الهاتف",
        "تسرب_العميل",
    ],
}
HEADERS[("messy", "ar")] = HEADERS[("clean", "ar")]

PRODUCTS = [
    ("Laptop", "Electronics", 950.0),
    ("Phone", "Electronics", 600.0),
    ("Headphones", "Electronics", 120.0),
    ("Desk", "Furniture", 260.0),
    ("Chair", "Furniture", 140.0),
    ("Shelf", "Furniture", 90.0),
    ("Notebook", "Office", 4.0),
    ("Pen", "Office", 1.5),
    ("Stapler", "Office", 12.0),
    ("Coffee", "Grocery", 9.0),
    ("Tea", "Grocery", 6.0),
    ("Snacks", "Grocery", 3.0),
]
REGIONS = ["North", "South", "East", "West", "Central"]
CHANNELS = ["Online", "Store", "Partner"]
STATUSES = ["Completed", "Returned", "Pending"]
START = date(2023, 1, 1)
SPAN_DAYS = 547
ARABIC_DIGITS = str.maketrans("0123456789.", "٠١٢٣٤٥٦٧٨٩٫")
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def planted_roles(variant: str = "clean", lang: str = "en") -> dict[str, str]:
    """Return {file header: expected role} for a generated file."""
    headers = HEADERS[(variant, lang)]
    return {h: role for h, (_, role) in zip(headers, COLUMNS, strict=True)}


def _base_rows(n: int, seed: int) -> list[Row]:
    rnd = random.Random(seed)
    customers = [f"CUST-{i:04d}" for i in range(1, 301)]
    churned = {c: int(rnd.random() < 0.2) for c in customers}
    rows: list[Row] = []
    for i in range(n):
        product, category, base = rnd.choice(PRODUCTS)
        quantity = rnd.randint(1, 10)
        price = round(base * rnd.uniform(0.9, 1.1), 2)
        discount = round(rnd.choice([0, 0, 0.05, 0.1, 0.15, 0.2, 0.3]), 2)
        customer = rnd.choice(customers)
        order_day = START + timedelta(days=i * SPAN_DAYS // n)
        next_month = (order_day.replace(day=1) + timedelta(days=32)).replace(day=rnd.randint(1, 12))
        rows.append(
            {
                "order_id": str(100001 + i),
                "order_date": order_day,
                "delivery_date": next_month,
                "customer_id": customer,
                "product": product,
                "category": category,
                "region": rnd.choice(REGIONS),
                "channel": rnd.choice(CHANNELS),
                "status": rnd.choice(STATUSES),
                "quantity": quantity,
                "unit_price": price,
                "discount": discount,
                "revenue": round(quantity * price * (1 - discount), 2),
                "cost": round(quantity * base * 0.6, 2),
                "email": f"{customer.lower().replace('-', '')}@example.com",
                "phone": f"+2010{rnd.randint(10_000_000, 99_999_999)}",
                "churned": churned[customer],
            }
        )
    return rows


def _to_csv(headers: list[str], rows: list[list[object]], delimiter: str = ",") -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def make_clean_csv(n: int = 1500, seed: int = 7, lang: str = "en") -> bytes:
    """Return a tidy retail CSV (ISO dates, numbers as numbers, no nulls or duplicates)."""
    rows = [
        [
            r["order_id"],
            r["order_date"].isoformat(),  # type: ignore[attr-defined]
            r["delivery_date"].isoformat(),  # type: ignore[attr-defined]
            r["customer_id"],
            r["product"],
            r["category"],
            r["region"],
            r["channel"],
            r["status"],
            r["quantity"],
            r["unit_price"],
            r["discount"],
            r["revenue"],
            r["cost"],
            r["email"],
            r["phone"],
            r["churned"],
        ]
        for r in _base_rows(n, seed)
    ]
    return _to_csv(HEADERS[("clean", lang)], rows)


def _money(rnd: random.Random, value: float) -> str:
    style = rnd.random()
    if style < 0.5:
        return f"${value:,.2f}"
    if style < 0.8:
        return f"{value:,.2f} USD"
    if style < 0.9:
        return f"{value:.2f}".translate(ARABIC_DIGITS)  # Arabic-Indic digits, Arabic decimal
    return f"{value:.2f}"


def _messy_date(rnd: random.Random, day: date) -> str:
    style = rnd.random()
    if style < 0.45:
        return day.isoformat()
    if style < 0.70:
        return f"{day.day:02d}/{day.month:02d}/{day.year}"
    if style < 0.90:
        return f"{day.day} {MONTHS[day.month - 1]} {day.year}"
    return f"{MONTHS[day.month - 1]} {day.day}, {day.year}"


def _pad(rnd: random.Random, text: str, rate: float) -> str:
    return f" {text} " if rnd.random() < rate else text


def _case(rnd: random.Random, text: str, rate: float) -> str:
    if rnd.random() >= rate:
        return text
    return text.upper() if rnd.random() < 0.5 else text.lower()


def make_messy_csv(
    n: int = 1500, seed: int = 7, lang: str = "en", duplicate_pct: float = 0.10
) -> bytes:
    """Return the dirty twin of the clean file (same seed gives the same business data)."""
    rnd = random.Random(seed + 1000)
    out: list[list[object]] = []
    for r in _base_rows(n, seed):

        def blank(value: object, rate: float) -> object:
            return "" if rnd.random() < rate else value

        qty = r["quantity"]
        qty_text: object = str(qty).translate(ARABIC_DIGITS) if rnd.random() < 0.06 else qty
        discount = r["discount"]
        discount_text: object = (
            f"{int(float(discount) * 100)}%"  # type: ignore[arg-type]
            if rnd.random() < 0.4
            else discount
        )
        revenue = float(r["revenue"])  # type: ignore[arg-type]
        if rnd.random() < 0.01:
            revenue *= 60  # a few extreme values
        out.append(
            [
                r["order_id"],
                blank(_messy_date(rnd, r["order_date"]), 0.06),  # type: ignore[arg-type]
                (
                    r["delivery_date"].strftime("%d/%m/%Y")  # type: ignore[attr-defined]
                    if rnd.random() > 0.05
                    else ""
                ),
                r["customer_id"],
                _pad(rnd, _case(rnd, str(r["product"]), 0.12), 0.08),
                _case(rnd, str(r["category"]), 0.12),
                blank(_pad(rnd, _case(rnd, str(r["region"]), 0.1), 0.12), 0.08),
                blank(r["channel"], 0.05),
                _case(rnd, str(r["status"]), 0.08),
                blank(qty_text, 0.07),
                blank(_money(rnd, float(r["unit_price"])), 0.08),  # type: ignore[arg-type]
                discount_text,
                blank(_money(rnd, revenue), 0.08),
                blank(r["cost"], 0.12),
                blank(r["email"], 0.12),
                r["phone"],
                r["churned"],
            ]
        )
    for _ in range(int(n * duplicate_pct)):
        out.append(list(rnd.choice(out)))  # exact copies of already-corrupted rows
    return _to_csv(HEADERS[("messy", lang)], out)


def write_all(directory: Path) -> list[Path]:
    """Write the four generated files into `directory` and return their paths."""
    directory.mkdir(parents=True, exist_ok=True)
    files = {
        "retail_gen_clean_en.csv": make_clean_csv(),
        "retail_gen_messy_en.csv": make_messy_csv(),
        "retail_gen_clean_ar.csv": make_clean_csv(lang="ar"),
        "retail_gen_messy_ar.csv": make_messy_csv(lang="ar"),
    }
    paths = []
    for name, content in files.items():
        path = directory / name
        path.write_bytes(content)
        paths.append(path)
    return paths


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("generated")
    for written in write_all(target):
        print(written)
