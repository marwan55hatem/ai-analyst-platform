"""Driver model: LightGBM on log revenue per line, explained by SHAP and checked by permutation.

The target is log(1 + revenue) of positive lines, so an effect reads as "x% more revenue per line"
whatever the size of the line. Rows are split by time (the last 20% of dates are the holdout), SHAP
importance is measured on a fixed sample of the training rows, permutation importance on the
holdout, and partial dependence (averaged predictions over a grid) for the top factors.
"""

import warnings
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import shap
from lightgbm import LGBMRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import r2_score

NUMERIC = ("price", "discount")
CATEGORICAL = ("category", "region", "channel", "weekday")
HOLDOUT_SHARE = 0.2
PD_POINTS = 8
PD_LEVELS = 10
PERMUTATION_REPEATS = 5

# factor -> plain words
LABELS = {
    "price": "unit price",
    "discount": "discount",
    "category": "category",
    "region": "region",
    "channel": "channel",
    "weekday": "day of the week",
}


@dataclass
class DriverResult:
    """Importances by both methods, partial dependence and the holdout fit."""

    factors: list[str]
    shap_share: dict[str, float]
    permutation: dict[str, float]
    r2_holdout: float
    partial_dependence: dict[str, list[dict[str, Any]]]
    train_rows: int
    holdout_rows: int
    shap_rows: int
    warnings: list[str] = field(default_factory=list)

    def ranking(self) -> list[str]:
        """Factors by SHAP share, largest first (ties by name)."""
        return sorted(self.factors, key=lambda f: (-self.shap_share[f], f))

    def permutation_ranking(self) -> list[str]:
        """Factors by permutation importance, largest first."""
        return sorted(self.factors, key=lambda f: (-self.permutation[f], f))

    def agrees(self) -> bool:
        """Whether SHAP and permutation importance name the same top factor."""
        return self.ranking()[0] == self.permutation_ranking()[0]


def frame(rows: list[dict[str, Any]], factors: list[str]) -> pd.DataFrame:
    """Build the model frame: numeric factors as floats, the others as pandas categories."""
    data: dict[str, Any] = {}
    for f in factors:
        values = [r[f] for r in rows]
        if f in NUMERIC:
            data[f] = pd.to_numeric(pd.Series(values, dtype=object), errors="coerce").astype(float)
        else:
            data[f] = pd.Series(
                ["(empty)" if v is None else str(v) for v in values], dtype="category"
            )
    return pd.DataFrame(data)


def _model(seed: int) -> LGBMRegressor:
    return LGBMRegressor(
        n_estimators=300,
        learning_rate=0.05,
        num_leaves=31,
        min_child_samples=20,
        subsample=0.8,
        subsample_freq=1,
        random_state=seed,
        n_jobs=1,
        verbose=-1,
    )


def _grid(column: pd.Series) -> list[Any]:
    if isinstance(column.dtype, pd.CategoricalDtype):
        return list(column.value_counts().index[:PD_LEVELS])
    values = column.dropna()
    if values.nunique() <= PD_POINTS:
        return sorted(float(v) for v in values.unique())
    qs = np.quantile(values, np.linspace(0.05, 0.95, PD_POINTS))
    return sorted({round(float(q), 4) for q in qs})


def partial_dependence(model: LGBMRegressor, x: pd.DataFrame, factor: str) -> list[dict[str, Any]]:
    """Average prediction with the factor set to each grid value, as % vs the average line."""
    base = float(np.mean(model.predict(x)))
    out = []
    for value in _grid(x[factor]):
        changed = x.copy()
        if isinstance(x[factor].dtype, pd.CategoricalDtype):
            changed[factor] = pd.Categorical([value] * len(x), categories=x[factor].cat.categories)
        else:
            changed[factor] = float(value)
        level = float(np.mean(model.predict(changed)))
        out.append(
            {
                "value": value if isinstance(value, str) else round(float(value), 4),
                "relative_revenue_pct": round(100.0 * (np.exp(level - base) - 1.0), 2),
            }
        )
    return out


def fit(
    x: pd.DataFrame, y: np.ndarray, holdout: np.ndarray, seed: int, shap_rows: int
) -> DriverResult:
    """Fit on the earlier rows, check on the holdout and explain (y is log1p revenue)."""
    factors = list(x.columns)
    train_x, train_y = x[~holdout], y[~holdout]
    test_x, test_y = x[holdout], y[holdout]
    model = _model(seed).fit(train_x, train_y)
    r2 = float(r2_score(test_y, model.predict(test_x))) if len(test_y) > 1 else float("nan")
    rng = np.random.default_rng(seed)
    sample_index = np.sort(
        rng.choice(len(train_x), size=min(shap_rows, len(train_x)), replace=False)
    )
    sample = train_x.iloc[sample_index]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        values = np.asarray(shap.TreeExplainer(model).shap_values(sample), dtype=float)
    mean_abs = np.abs(values).mean(axis=0)
    total = float(mean_abs.sum()) or 1.0
    perm = permutation_importance(
        model, test_x, test_y, n_repeats=PERMUTATION_REPEATS, random_state=seed, scoring="r2"
    )
    result = DriverResult(
        factors=factors,
        shap_share={f: 100.0 * float(mean_abs[i]) / total for i, f in enumerate(factors)},
        permutation={f: float(perm.importances_mean[i]) for i, f in enumerate(factors)},
        r2_holdout=r2,
        partial_dependence={},
        train_rows=len(train_x),
        holdout_rows=len(test_x),
        shap_rows=len(sample),
    )
    for f in result.ranking()[:3]:
        result.partial_dependence[f] = partial_dependence(model, sample, f)
    return result
