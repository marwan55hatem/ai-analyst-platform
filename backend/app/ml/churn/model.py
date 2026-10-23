"""Churn models: logistic baseline and LightGBM, calibrated, checked out of time, explained.

Both models are calibrated with `CalibratedClassifierCV(ensemble=False)`: the model is fitted on the
whole snapshot and the calibrator on cross-validated predictions inside that same snapshot (folds
never cross time). Isotonic calibration needs about 1,000 customers; below that Platt (sigmoid).
SHAP values are computed on the uncalibrated model (log-odds), which ranks reasons the same way.
"""

import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import shap
from lightgbm import LGBMClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler

MODELS = ("logistic", "lightgbm")
ISOTONIC_MIN_ROWS = 1000
TOP_SHARE = 0.10
BACKGROUND_ROWS = 1000
# Calibrated risks are kept inside [0.5%, 99.5%]: isotonic steps can reach exactly 0 or 1, which
# would claim certainty the data cannot give.
PROBABILITY_FLOOR, PROBABILITY_CEILING = 0.005, 0.995


def _signed_log1p(values: np.ndarray) -> np.ndarray:
    out: np.ndarray = np.sign(values) * np.log1p(np.abs(values))
    return out


def build(kind: str, seed: int) -> Any:
    """Return an unfitted model of this kind (fixed seed, one thread)."""
    if kind == "logistic":
        return Pipeline(
            [
                ("log", FunctionTransformer(_signed_log1p)),
                ("scale", StandardScaler()),
                ("model", LogisticRegression(max_iter=2000, random_state=seed)),
            ]
        )
    return LGBMClassifier(
        n_estimators=200,
        learning_rate=0.05,
        num_leaves=15,
        min_child_samples=20,
        subsample=0.8,
        subsample_freq=1,
        colsample_bytree=0.9,
        random_state=seed,
        n_jobs=1,
        verbose=-1,
    )


def calibration_method(rows: int) -> str:
    """Isotonic with enough customers, otherwise Platt scaling."""
    return "isotonic" if rows >= ISOTONIC_MIN_ROWS else "sigmoid"


def calibrated(kind: str, seed: int, rows: int) -> CalibratedClassifierCV:
    """Return an unfitted calibrated model (calibration folds stay inside one snapshot)."""
    return CalibratedClassifierCV(
        build(kind, seed),
        method=calibration_method(rows),
        cv=StratifiedKFold(n_splits=3, shuffle=True, random_state=seed),
        ensemble=False,
    )


@dataclass(frozen=True)
class Metrics:
    """How well predicted risks separate churners from stayers on held-out customers."""

    auc: float
    pr_auc: float
    brier: float
    top_decile_lift: float
    customers: int
    churners: int

    def as_dict(self) -> dict[str, float | int]:
        """Return rounded values for payloads."""
        return {
            "auc": round(self.auc, 4),
            "pr_auc": round(self.pr_auc, 4),
            "brier": round(self.brier, 4),
            "top_decile_lift": round(self.top_decile_lift, 3),
            "customers": self.customers,
            "churners": self.churners,
        }


def metrics(y: np.ndarray, p: np.ndarray) -> Metrics:
    """AUC, PR-AUC, Brier score and the churn rate of the top 10% over the overall rate."""
    order = np.argsort(-p, kind="stable")
    top = order[: max(1, int(round(TOP_SHARE * len(p))))]
    base = float(y.mean())
    lift = float(y[top].mean()) / base if base > 0 else 0.0
    return Metrics(
        auc=float(roc_auc_score(y, p)),
        pr_auc=float(average_precision_score(y, p)),
        brier=float(brier_score_loss(y, p)),
        top_decile_lift=lift,
        customers=len(y),
        churners=int(y.sum()),
    )


@dataclass
class Comparison:
    """Metrics of every model that ran and the chosen one (best PR-AUC; ties go to logistic)."""

    scores: dict[str, Metrics]
    winner: str
    validation: str
    warnings: list[str] = field(default_factory=list)


def _pick(scores: dict[str, Metrics]) -> str:
    return max(scores, key=lambda k: (round(scores[k].pr_auc, 4), k == "logistic"))


def compare_out_of_time(
    train: tuple[np.ndarray, np.ndarray],
    evaluate: tuple[np.ndarray, np.ndarray],
    seed: int,
    out_of_time: Callable[[], bool],
) -> Comparison:
    """Fit on the earlier snapshot and score the later one, for each model."""
    (x1, y1), (x2, y2) = train, evaluate
    scores: dict[str, Metrics] = {}
    warnings: list[str] = []
    for kind in MODELS:
        if scores and out_of_time():
            warnings.append(f"The time limit was reached, so the {kind} model was not tried.")
            break
        model = calibrated(kind, seed, len(y1)).fit(x1, y1)
        scores[kind] = metrics(y2, model.predict_proba(x2)[:, 1])
    return Comparison(scores, _pick(scores), "out_of_time", warnings)


def compare_cross_validated(x: np.ndarray, y: np.ndarray, seed: int) -> Comparison:
    """Flag mode: stratified 5-fold predictions (there is no time to split on)."""
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=seed)
    scores = {
        kind: metrics(
            y,
            cross_val_predict(
                calibrated(kind, seed, len(y)), x, y, cv=folds, method="predict_proba"
            )[:, 1],
        )
        for kind in MODELS
    }
    return Comparison(scores, _pick(scores), "cross_validated")


@dataclass
class Fitted:
    """The chosen model refitted on the latest labelled snapshot."""

    kind: str
    model: CalibratedClassifierCV
    background: np.ndarray

    @property
    def base(self) -> Any:
        """The uncalibrated model fitted on all rows (ensemble=False keeps exactly one)."""
        return self.model.calibrated_classifiers_[0].estimator

    def predict(self, x: np.ndarray) -> np.ndarray:
        """Calibrated probabilities of churning, clipped to [0.5%, 99.5%]."""
        p = np.asarray(self.model.predict_proba(x)[:, 1], dtype=float)
        clipped: np.ndarray = np.clip(p, PROBABILITY_FLOOR, PROBABILITY_CEILING)
        return clipped


def fit_final(kind: str, x: np.ndarray, y: np.ndarray, seed: int) -> Fitted:
    """Refit the chosen model (calibrated) on a labelled snapshot."""
    model = calibrated(kind, seed, len(y)).fit(x, y)
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(x), size=min(BACKGROUND_ROWS, len(x)), replace=False)
    return Fitted(kind, model, x[np.sort(pick)])


def explain(fitted: Fitted, x: np.ndarray) -> np.ndarray:
    """SHAP values (log-odds of churning) per customer and feature, shape (rows, features)."""
    with warnings.catch_warnings():  # SHAP announces its (handled) output format on every call
        warnings.simplefilter("ignore", UserWarning)
        if fitted.kind == "lightgbm":
            values = shap.TreeExplainer(fitted.base).shap_values(x)
        else:
            pipe = fitted.base
            prep = pipe[:-1]
            background = prep.transform(fitted.background)
            masker = shap.maskers.Independent(background, max_samples=len(background))
            values = shap.LinearExplainer(pipe[-1], masker).shap_values(prep.transform(x))
    if isinstance(values, list):  # older SHAP: one array per class
        values = values[-1]
    array = np.asarray(values, dtype=float)
    if array.ndim == 3:
        array = array[:, :, -1]
    return array
