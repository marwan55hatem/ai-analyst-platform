"""K-Means customer clusters: k by silhouette, bootstrap stability (ARI), deterministic names.

Works on a small numpy matrix (one row per customer, five features) that the module fetched from
DuckDB, capped at SEGMENT_MAX_CUSTOMERS rows for fitting.
"""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score, silhouette_score
from sklearn.preprocessing import StandardScaler

FEATURES = ("recency_days", "frequency", "monetary", "aov", "tenure_days")
K_RANGE = range(3, 9)
N_INIT = 10
BOOTSTRAP_N_INIT = 4
SILHOUETTE_SAMPLE = 5000
BOOTSTRAP_SAMPLE = 5000
HIGH_ARI = 0.8
MEDIUM_ARI = 0.6
MIN_BOOTSTRAP_ROUNDS = 5


@dataclass
class Clustering:
    """A fitted clustering, renumbered so cluster 0 has the highest average spend."""

    k: int
    scaler: StandardScaler
    model: KMeans
    order: list[int]  # order[new_id] = the model's label
    silhouette: dict[int, float]
    labels: np.ndarray  # new ids of the fitting rows
    warnings: list[str] = field(default_factory=list)

    def predict(self, features: np.ndarray) -> np.ndarray:
        """Assign customers (raw feature rows) to the renumbered clusters."""
        raw = self.model.predict(self.scaler.transform(transform(features)))
        remap = {old: new for new, old in enumerate(self.order)}
        return np.array([remap[int(x)] for x in raw], dtype=int)


@dataclass(frozen=True)
class Stability:
    """How much the clusters move when the customers are resampled."""

    median_ari: float | None
    p10_ari: float | None
    rounds: int

    @property
    def confidence(self) -> str:
        """Map the median adjusted Rand index to a confidence label."""
        if self.median_ari is None or self.rounds < MIN_BOOTSTRAP_ROUNDS:
            return "low"
        if self.median_ari >= HIGH_ARI:
            return "high"
        return "medium" if self.median_ari >= MEDIUM_ARI else "low"


def transform(features: np.ndarray) -> np.ndarray:
    """Return log1p of the (non-negative) features: spend and frequency are heavily skewed."""
    return np.asarray(np.log1p(np.clip(features, 0.0, None)), dtype=float)


def fit(
    features: np.ndarray, seed: int, out_of_time: Callable[[], bool] = lambda: False
) -> Clustering:
    """Choose k in 3..8 by silhouette score and fit K-Means (fixed seed, 10 starts)."""
    scaler = StandardScaler().fit(transform(features))
    x = scaler.transform(transform(features))
    sample = min(len(x), SILHOUETTE_SAMPLE)
    scores: dict[int, float] = {}
    models: dict[int, KMeans] = {}
    warnings: list[str] = []
    for k in K_RANGE:
        if k >= len(x):
            break
        if scores and out_of_time():
            warnings.append(
                f"The time limit was reached, so only k = 3..{max(scores)} were compared."
            )
            break
        model = KMeans(n_clusters=k, n_init=N_INIT, random_state=seed).fit(x)
        if len(set(model.labels_)) < 2:
            continue
        scores[k] = float(silhouette_score(x, model.labels_, sample_size=sample, random_state=seed))
        models[k] = model
    best = max(scores, key=lambda k: (round(scores[k], 6), -k))
    model = models[best]
    spend = [float(features[model.labels_ == c, 2].mean()) for c in range(best)]
    order = sorted(range(best), key=lambda c: (-spend[c], c))
    remap = {old: new for new, old in enumerate(order)}
    labels = np.array([remap[int(x)] for x in model.labels_], dtype=int)
    return Clustering(best, scaler, model, order, scores, labels, warnings)


def stability(
    features: np.ndarray,
    clustering: Clustering,
    rounds: int,
    seed: int,
    out_of_time: Callable[[], bool] = lambda: False,
) -> Stability:
    """Bootstrap: refit on resampled customers and compare labels with the adjusted Rand index."""
    rng = np.random.default_rng(seed)
    x = clustering.scaler.transform(transform(features))
    keep = rng.permutation(len(x))[: min(len(x), BOOTSTRAP_SAMPLE)]
    reference = x[keep]
    base = clustering.model.predict(reference)
    scores: list[float] = []
    for b in range(rounds):
        if out_of_time():
            break
        drawn = rng.integers(0, len(x), size=min(len(x), BOOTSTRAP_SAMPLE))
        refit = KMeans(
            n_clusters=clustering.k, n_init=BOOTSTRAP_N_INIT, random_state=seed + b + 1
        ).fit(x[drawn])
        scores.append(float(adjusted_rand_score(base, refit.predict(reference))))
    if not scores:
        return Stability(None, None, 0)
    return Stability(
        round(float(np.median(scores)), 3), round(float(np.percentile(scores, 10)), 3), len(scores)
    )


def _tier(value: float, low: float, high: float, names: tuple[str, str, str]) -> str:
    if value >= high:
        return names[0]
    return names[1] if value >= low else names[2]


def names(features: np.ndarray, labels: np.ndarray, k: int) -> list[str]:
    """Return a plain, deterministic, unique name per cluster, e.g. "High value, recent".

    Value and recency tiers compare the cluster's median customer with the 33rd/67th percentiles
    of all customers; frequency is added only when two names would otherwise be the same.
    """
    spend_lo, spend_hi = np.percentile(features[:, 2], [33.3, 66.7])
    rec_lo, rec_hi = np.percentile(features[:, 0], [33.3, 66.7])
    freq_lo, freq_hi = np.percentile(features[:, 1], [33.3, 66.7])
    parts: list[tuple[str, str, str]] = []
    for c in range(k):
        member = features[labels == c]
        spend = float(np.median(member[:, 2]))
        recency = float(np.median(member[:, 0]))
        frequency = float(np.median(member[:, 1]))
        value = _tier(spend, spend_lo, spend_hi, ("High value", "Mid value", "Low value"))
        # a short recency (few days since the last purchase) is "recent"
        when = "recent" if recency <= rec_lo else "lapsing" if recency <= rec_hi else "lapsed"
        often = (
            "one-time buyers"
            if frequency <= 1
            else _tier(frequency, freq_lo, freq_hi, ("frequent", "occasional", "rare"))
        )
        parts.append((value, when, often))
    base = [f"{v}, {w}" for v, w, _ in parts]
    counts = Counter(base)
    named = [f"{b}, {o}" if counts[b] > 1 else b for b, (_, _, o) in zip(base, parts, strict=True)]
    seen: Counter[str] = Counter()
    unique = []
    for n in named:
        seen[n] += 1
        unique.append(n if Counter(named)[n] == 1 else f"{n} ({seen[n]})")
    return unique
