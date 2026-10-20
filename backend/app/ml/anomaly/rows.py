"""Row-level anomalies: zero or inconsistent quantities, unusual discounts and extreme prices.

Counts come from SQL over every row (stored as evidence). The Isolation Forest only sees a
bounded reservoir sample (`ANOMALY_ROW_SAMPLE`), so millions of rows never reach pandas.
"""

from typing import Any

import numpy as np
from sklearn.ensemble import IsolationForest

from app.ml.base import AnalysisContext, Insight
from app.ml.engine import ROW_NUMBER, num
from app.ml.format import fmt_number
from app.ml.modules.common import (
    SENSITIVITY,
    as_date,
    build_insight,
    evidence_query,
    revenue_expr,
)
from app.services.profiling.results import confidence_for

DISCOUNT_LIMIT = 0.5
DISCOUNT_Z = 3.5
PRICE_FACTOR = 3.0
SAMPLE_ROWS = 10
CANDIDATE_LIMIT = 200
FOREST_TREES = 200
FOREST_RATE = 0.01
FOREST_TREE_ROWS = 1024
MIN_TRAIN_ROWS = 40
DISCOUNT_FRACTION_MAX = 1
DISCOUNT_PERCENT_MAX = 100
NOT_ERRORS = "These are findings from the data, not proven errors."


def discount_rate_expr(ctx: AnalysisContext, alias: str = "") -> str | None:
    """Return SQL for a row's discount as a fraction (the column may be a fraction, % or amount)."""
    if not ctx.roles.has("discount"):
        return None
    disc = ctx.roles.q("discount", alias)
    if "discount_scale" not in ctx.cache:
        top = ctx.db.scalar(f"SELECT max(abs({ctx.roles.q('discount')})) FROM data")
        ctx.cache["discount_scale"] = num(top)
    top = ctx.cache["discount_scale"]
    if top <= DISCOUNT_FRACTION_MAX:
        return disc
    if top <= DISCOUNT_PERCENT_MAX:
        return f"({disc} / 100.0)"
    rev = ctx.roles.revenue_expr(alias)
    return f"({disc} / nullif(abs({rev}) + {disc}, 0))"


def _price_expr(ctx: AnalysisContext, alias: str = "") -> str | None:
    if ctx.roles.has("price"):
        return ctx.roles.q("price", alias)
    if ctx.roles.has("quantity") and ctx.roles.has("revenue"):
        qty = ctx.roles.q("quantity", alias)
        return f"({ctx.roles.revenue_expr(alias)} / nullif({qty}, 0))"
    return None


def _discount_condition(ctx: AnalysisContext, rate: str) -> tuple[str, str]:
    """Return the SQL condition for an unusual discount and the rule in words."""
    stats = ctx.db.one(
        f"WITH r AS (SELECT {rate} AS rate FROM data WHERE {rate} IS NOT NULL), "
        f"m AS (SELECT median(rate) AS med FROM r) "
        f"SELECT m.med AS med, median(abs(r.rate - m.med)) AS mad FROM r, m GROUP BY m.med"
    )
    med, mad = num(stats.get("med")), num(stats.get("mad"))
    if mad > 0:
        cut = round(med + DISCOUNT_Z * mad / 0.6745, 4)
        cut = min(cut, DISCOUNT_LIMIT)
        return f"{rate} > {cut}", f"a discount above {cut * 100:.0f}% (robust z >= {DISCOUNT_Z})"
    return f"{rate} > {DISCOUNT_LIMIT}", f"a discount above {DISCOUNT_LIMIT * 100:.0f}%"


def row_anomalies(ctx: AnalysisContext) -> Insight | None:
    """Return one insight with row-level counts and sample references, or None if all is well."""
    rev = revenue_expr(ctx)
    qty = ctx.roles.q("quantity") if ctx.roles.has("quantity") else None
    rate = discount_rate_expr(ctx)
    checks: dict[str, tuple[str, str]] = {}
    if qty is not None:
        checks["zero_quantity"] = (f"{qty} = 0", "zero quantity")
        checks["negative_quantity_positive_revenue"] = (
            f"{qty} < 0 AND {rev} > 0",
            "a negative quantity with positive revenue",
        )
    if rate is not None:
        checks["unusual_discount"] = _discount_condition(ctx, rate)
    price = _price_expr(ctx)
    typical = _typical_price_cte(ctx, price) if price is not None else None
    if not checks and typical is None:
        return None
    counts_sql = _counts_sql(ctx, checks, price, typical, qty, rev)
    counts_row = ctx.db.one(counts_sql)
    counts = {k: int(v or 0) for k, v in counts_row.items() if k != "rows"}
    total_rows = int(counts_row.get("rows") or 0)
    flagged = {k: v for k, v in counts.items() if k != "returns" and v > 0}
    if not flagged:
        return None
    samples = {
        kind: _sample_rows(ctx, condition)
        for kind, (condition, _) in checks.items()
        if counts[kind]
    }
    forest: dict[str, Any] = {}
    if typical is not None and price is not None and counts.get("extreme_price", 0):
        forest = _extreme_prices(ctx, price, typical, rate)
        samples["extreme_price"] = forest["rows"]
    rules = {k: words for k, (_, words) in checks.items()}
    if typical is not None:
        rules["extreme_price"] = (
            f"a unit price at least {PRICE_FACTOR:g}x or at most 1/{PRICE_FACTOR:g} of the typical "
            "(median) price of the same product"
        )
    return _insight(
        ctx, counts, flagged, samples, rules, forest, counts_sql, counts_row, total_rows
    )


def _typical_price_cte(ctx: AnalysisContext, price: str) -> str:
    group = ctx.roles.q("product") if ctx.roles.has("product") else "'all'"
    return (
        f"typical AS (SELECT {group} AS typical_item, median({price}) AS typical_price FROM data "
        f"WHERE {price} > 0 GROUP BY 1)"
    )


def _join_typical(ctx: AnalysisContext, alias: str) -> str:
    key = ctx.roles.q("product", alias) if ctx.roles.has("product") else "'all'"
    return f"JOIN typical ON {key} = typical.typical_item"


def _counts_sql(
    ctx: AnalysisContext,
    checks: dict[str, tuple[str, str]],
    price: str | None,
    typical: str | None,
    qty: str | None,
    rev: str,
) -> str:
    parts = [f"count(*) FILTER (WHERE {cond}) AS {kind}" for kind, (cond, _) in checks.items()]
    if qty is not None:
        parts.append(f"count(*) FILTER (WHERE {qty} < 0 AND {rev} < 0) AS returns")
    if typical is None or price is None:
        return f"SELECT {', '.join(parts)}, count(*) AS rows FROM data"
    aliased = _price_expr(ctx, "data")
    extreme = (
        f"{aliased} > 0 AND ({aliased} >= {PRICE_FACTOR} * typical.typical_price "
        f"OR {aliased} <= typical.typical_price / {PRICE_FACTOR})"
    )
    parts.append(f"count(*) FILTER (WHERE {extreme}) AS extreme_price")
    return (
        f"WITH {typical} SELECT {', '.join(parts)}, count(*) AS rows FROM data "
        f"LEFT {_join_typical(ctx, 'data')}"
    )


def _reference(ctx: AnalysisContext, alias: str = "") -> str:
    """Columns shown for a row reference: never customer ids or personal data."""
    prefix = f"{alias}." if alias else ""
    cols = [f"{prefix}{ROW_NUMBER} AS row_number"]
    for role in ("date", "product", "quantity", "price"):
        if ctx.roles.has(role):
            cols.append(f"{ctx.roles.q(role, alias)} AS {role}")
    cols.append(f"{ctx.roles.revenue_expr(alias)} AS revenue")
    rate = discount_rate_expr(ctx, alias)
    if rate is not None:
        cols.append(f"{rate} AS discount_rate")
    return ", ".join(cols)


def _clean(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if key == "date" and value is not None:
            out[key] = as_date(value).isoformat()
        elif key in {"quantity", "price", "revenue", "discount_rate"}:
            out[key] = None if value is None else round(num(value), 4)
        else:
            out[key] = value
    return out


def _sample_rows(ctx: AnalysisContext, condition: str) -> list[dict[str, Any]]:
    rows = ctx.db.rows(
        f"SELECT {_reference(ctx)} FROM data WHERE {condition} "
        f"ORDER BY {ROW_NUMBER} LIMIT {SAMPLE_ROWS}"
    )
    return [_clean(r) for r in rows]


def _extreme_prices(
    ctx: AnalysisContext, price: str, typical: str, rate: str | None
) -> dict[str, Any]:
    """Rank extreme-price rows with an Isolation Forest fitted on a bounded reservoir sample."""
    limit, seed = ctx.settings.anomaly_row_sample, ctx.settings.ml_seed
    qty = ctx.roles.q("quantity", "s") if ctx.roles.has("quantity") else "1"
    rate_s = discount_rate_expr(ctx, "s") if rate is not None else "0"
    price_s = _price_expr(ctx, "s")
    features = (
        f"ln({price_s}) AS log_price, ln(1 + greatest({qty}, 0)) AS log_quantity, "
        f"ln({price_s} / typical.typical_price) AS log_ratio, "
        f"coalesce({rate_s}, 0) AS discount_rate"
    )
    total = int(ctx.db.scalar("SELECT count(*) FROM data") or 0)
    sampled = total > limit
    # a hash order is a deterministic sample, whatever the number of threads
    source = (
        f"(SELECT * FROM data ORDER BY md5(CAST({ROW_NUMBER} AS VARCHAR) || '{seed}') "
        f"LIMIT {limit})"
        if sampled
        else "data"
    )
    train = ctx.db.rows(
        f"WITH {typical} SELECT {features} FROM {source} AS s {_join_typical(ctx, 's')} "
        f"WHERE {price_s} > 0 AND typical.typical_price > 0"
    )
    candidates = ctx.db.rows(
        f"WITH {typical} SELECT {_reference(ctx, 's')}, {features} FROM data AS s "
        f"{_join_typical(ctx, 's')} WHERE {price_s} > 0 AND typical.typical_price > 0 "
        f"AND ({price_s} >= {PRICE_FACTOR} * typical.typical_price "
        f"OR {price_s} <= typical.typical_price / {PRICE_FACTOR}) "
        f"ORDER BY abs(ln({price_s} / typical.typical_price)) DESC, s.{ROW_NUMBER} "
        f"LIMIT {CANDIDATE_LIMIT}"
    )
    names = ["log_price", "log_quantity", "log_ratio", "discount_rate"]
    isolated = 0
    if len(train) >= MIN_TRAIN_ROWS and candidates:
        x_cand = np.array([[num(r[n]) for n in names] for r in candidates])
        # The rule candidates join the sample: a tree that never sees an odd price cannot split
        # on the price ratio, so rare rows would look normal to it.
        x_train = np.vstack([np.array([[num(r[n]) for n in names] for r in train]), x_cand])
        forest = IsolationForest(
            n_estimators=FOREST_TREES,
            max_samples=min(len(x_train), FOREST_TREE_ROWS),
            contamination=FOREST_RATE,
            random_state=seed,
        ).fit(x_train)
        verdict = forest.predict(x_cand) == -1
        order = np.argsort(forest.score_samples(x_cand), kind="stable")
        candidates = [{**candidates[i], "isolated_by_forest": bool(verdict[i])} for i in order]
        isolated = int(verdict.sum())
    rows = [
        _clean({k: v for k, v in r.items() if k not in names}) for r in candidates[:SAMPLE_ROWS]
    ]
    return {
        "rows": rows,
        "isolated": isolated,
        "checked": min(len(candidates), CANDIDATE_LIMIT),
        "sampled": sampled,
        "sample_size": min(total, limit),
    }


def _insight(
    ctx: AnalysisContext,
    counts: dict[str, int],
    flagged: dict[str, int],
    samples: dict[str, list[dict[str, Any]]],
    rules: dict[str, str],
    forest: dict[str, Any],
    sql: str,
    counts_row: dict[str, Any],
    total_rows: int,
) -> Insight:
    loc = ctx.locale
    parts = [f"{fmt_number(n, loc)} rows with {rules[k]}" for k, n in flagged.items()]
    warnings: list[str] = []
    if forest:
        parts[-1] += (
            f" ({fmt_number(forest['isolated'], loc)} of the {fmt_number(forest['checked'], loc)} "
            "checked also isolated by an Isolation Forest on price, quantity and discount)"
        )
        if forest["sampled"]:
            warnings.append(
                f"The Isolation Forest learned from a random sample of "
                f"{fmt_number(forest['sample_size'], loc)} rows (ANOMALY_ROW_SAMPLE); the counts "
                "use every row."
            )
    version = ctx.version_number
    returns = counts.get("returns", 0)
    summary = (
        f"Row checks on {fmt_number(total_rows, loc)} rows found "
        + "; ".join(parts)
        + "."
        + (
            f" {fmt_number(returns, loc)} rows with negative quantity and negative revenue are "
            "treated as returns, not anomalies."
            if returns
            else ""
        )
        + f" Row numbers refer to the stored data of version {version}, not necessarily the row "
        f"numbers in the original upload. {NOT_ERRORS}"
    )
    flagged_revenue = sum(abs(num(r.get("revenue"))) for rows in samples.values() for r in rows)
    return build_insight(
        ctx,
        module="anomalies",
        type="row_anomalies",
        title=f"{fmt_number(sum(flagged.values()), loc)} unusual rows found",
        summary=summary,
        value=flagged,
        sample_size=total_rows,
        method=(
            "SQL rules over every row (zero/negative quantities, unusual discounts, prices far "
            "from the product's median); extreme prices ranked by an Isolation Forest "
            f"({FOREST_TREES} trees, fixed seed) trained on a reservoir sample"
        ),
        key_columns=[],
        money_effect=SENSITIVITY * flagged_revenue,
        payload={
            "counts": counts,
            "rules": rules,
            "samples": samples,
            "dataset_version_id": str(ctx.version_id),
            "dataset_version_number": version,
            "row_numbers_refer_to": f"stored data of version {version}",
            "forest": {k: v for k, v in forest.items() if k != "rows"},
        },
        chart_spec={
            "type": "bar",
            "title": "Unusual rows by check",
            "x": "check",
            "y": "rows",
            "data": "payload.counts",
        },
        evidence=[evidence_query("Row-level checks over every row", sql, [counts_row])],
        confidence=confidence_for(total_rows),
        warnings=tuple(warnings),
    )
