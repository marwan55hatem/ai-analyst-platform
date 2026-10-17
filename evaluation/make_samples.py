"""Generate the small committed sample files in evaluation/datasets/samples/.

Run from the repo root:  uv run --directory backend python ../evaluation/make_samples.py
The output is deterministic, so re-running produces identical files.
"""

import csv
import random
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook

OUT = Path(__file__).resolve().parent / "datasets" / "samples"
PRODUCTS = [("Notebook", 3.5), ("Pen", 1.2), ("Backpack", 24.0), ("Lamp", 18.5), ("Mug", 6.0)]
REGIONS = ["North", "South", "East", "West"]


def retail_rows(rng: random.Random, count: int) -> list[list[object]]:
    """Return deterministic retail order rows."""
    start = date(2024, 1, 1)
    rows: list[list[object]] = []
    for i in range(1, count + 1):
        product, price = rng.choice(PRODUCTS)
        qty = rng.randint(1, 6)
        rows.append(
            [
                1000 + i,
                (start + timedelta(days=rng.randint(0, 180))).isoformat(),
                rng.choice(REGIONS),
                product,
                qty,
                price,
                round(qty * price, 2),
            ]
        )
    return rows


def write_clean(rng: random.Random) -> None:
    """A tidy retail file."""
    header = ["order_id", "order_date", "region", "product", "quantity", "unit_price", "revenue"]
    with (OUT / "retail_clean.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(retail_rows(rng, 400))


def write_messy(rng: random.Random) -> None:
    """Awkward headers, blanks, currency text, duplicated names, a semicolon delimiter."""
    header = ["Order ID", " Order Date ", "Region", "Product Name", "Qty", "Unit Price ($)", "Qty"]
    rows = retail_rows(rng, 300)
    with (OUT / "retail_messy.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(header)
        for i, row in enumerate(rows):
            order_id, day, region, product, qty, price, _ = row
            if i % 17 == 0:
                region = ""
            if i % 29 == 0:
                price = f"${price}"
            writer.writerow([order_id, day, f" {region} ", product, qty, price, qty])


def write_xlsx(rng: random.Random) -> None:
    """A workbook with an orders sheet and a products sheet."""
    wb = Workbook()
    orders = wb.active
    assert orders is not None
    orders.title = "Orders"
    orders.append(["order_id", "order_date", "region", "product", "quantity", "revenue"])
    for row in retail_rows(rng, 250):
        orders.append([row[0], date.fromisoformat(str(row[1])), row[2], row[3], row[4], row[6]])
    products = wb.create_sheet("Products")
    products.append(["product", "list_price"])
    for name, price in PRODUCTS:
        products.append([name, price])
    wb.save(OUT / "retail_two_sheets.xlsx")


def write_arabic() -> None:
    """Arabic text saved as Windows-1256 with a semicolon delimiter."""
    cities = ["القاهرة", "الإسكندرية", "الجيزة", "أسوان", "الأقصر"]
    items = ["دفتر", "قلم", "حقيبة", "مصباح", "كوب"]
    rng = random.Random(7)
    lines = ["رقم_الطلب;المدينة;المنتج;الكمية;المبلغ"]
    for i in range(1, 121):
        qty = rng.randint(1, 5)
        lines.append(f"{i};{rng.choice(cities)};{rng.choice(items)};{qty};{qty * rng.randint(5, 40)}")
    (OUT / "retail_arabic_cp1256.csv").write_bytes("\r\n".join(lines).encode("cp1256") + b"\r\n")


def main() -> None:
    """Write every sample file."""
    OUT.mkdir(parents=True, exist_ok=True)
    write_clean(random.Random(1))
    write_messy(random.Random(2))
    write_xlsx(random.Random(3))
    write_arabic()
    for path in sorted(OUT.iterdir()):
        print(f"{path.name}: {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
