"""Unit tests of segmentation (no database): RFM map, actions, clustering, stability, names."""

import numpy as np
import pytest

from app.ml.base import DataFacts
from app.ml.modules.segments import SegmentationModule, pseudonym
from app.ml.segmentation import clusters, rfm

SEED = 42


def test_segment_map_covers_every_cell_with_a_known_segment() -> None:
    assert set(rfm.SEGMENT_MAP) == {(r, f) for r in range(1, 6) for f in range(1, 6)}
    assert set(rfm.SEGMENT_MAP.values()) == set(rfm.SEGMENTS)
    assert rfm.SEGMENT_MAP[(5, 5)] == "Champions"
    assert rfm.SEGMENT_MAP[(5, 1)] == "New"
    assert rfm.SEGMENT_MAP[(1, 5)] == "At risk"
    assert rfm.SEGMENT_MAP[(1, 1)] == "Lost"


def test_every_segment_has_a_suggestion() -> None:
    for name in rfm.SEGMENTS:
        action = rfm.suggestion(name)
        assert action["kind"] == "suggestion"
        assert action["text"].startswith("Suggestion: ")
    assert "win-back" in rfm.suggestion("At risk")["text"].lower()


def test_segment_case_has_all_cells() -> None:
    case = rfm.segment_case()
    assert case.count("WHEN") == 25 and case.startswith("CASE") and case.endswith("END")


def _groups(seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Four well separated customer groups (recency, frequency, spend, aov, tenure)."""
    rng = np.random.default_rng(seed)
    centres = [
        (5, 25, 9000, 360, 700),
        (20, 8, 1500, 190, 650),
        (450, 5, 700, 140, 700),
        (30, 1, 40, 40, 40),
    ]
    rows, labels = [], []
    for g, centre in enumerate(centres):
        for _ in range(60):
            rows.append([max(1.0, c * rng.lognormal(0, 0.15)) for c in centre])
            labels.append(g)
    return np.array(rows), np.array(labels)


def test_kmeans_recovers_separated_groups_and_is_stable() -> None:
    features, truth = _groups()
    fitted = clusters.fit(features, SEED)
    assert fitted.k == 4
    from sklearn.metrics import adjusted_rand_score

    assert adjusted_rand_score(truth, fitted.labels) > 0.95
    stable = clusters.stability(features, fitted, 10, SEED)
    assert stable.rounds == 10 and stable.median_ari is not None and stable.median_ari > 0.9
    assert stable.confidence == "high"
    assert np.array_equal(fitted.predict(features), fitted.labels)


def test_clusters_are_numbered_by_spend_and_reproducible() -> None:
    features, _ = _groups()
    first, second = clusters.fit(features, SEED), clusters.fit(features, SEED)
    assert np.array_equal(first.labels, second.labels)
    spend = [features[first.labels == c, 2].mean() for c in range(first.k)]
    assert spend == sorted(spend, reverse=True)


def test_time_limit_stops_the_k_search_and_the_bootstrap() -> None:
    features, _ = _groups()
    fitted = clusters.fit(features, SEED, out_of_time=lambda: True)
    assert fitted.k == 3 and "time limit" in fitted.warnings[0]
    stable = clusters.stability(features, fitted, 10, SEED, out_of_time=lambda: True)
    assert stable == clusters.Stability(None, None, 0)
    assert stable.confidence == "low"


@pytest.mark.parametrize(
    ("ari", "rounds", "level"),
    [(0.9, 20, "high"), (0.7, 20, "medium"), (0.4, 20, "low"), (0.95, 3, "low")],
)
def test_stability_maps_to_confidence(ari: float, rounds: int, level: str) -> None:
    assert clusters.Stability(ari, ari, rounds).confidence == level


def test_names_are_plain_deterministic_and_unique() -> None:
    features, _ = _groups()
    fitted = clusters.fit(features, SEED)
    names = clusters.names(features, fitted.labels, fitted.k)
    assert names[0] == "High value, recent"
    assert len(set(names)) == len(names)
    assert any("lapsed" in n for n in names)
    same = np.vstack([features[:10], features[:10]])
    twins = clusters.names(same, np.array([0] * 10 + [1] * 10), 2)
    assert twins[0] != twins[1]


def test_segmentation_needs_one_hundred_customers() -> None:
    module = SegmentationModule()
    reason = module.data_requirement(DataFacts(distinct={"customer_id": 40}))
    assert reason == "only 40 customers; customer segmentation needs at least 100"
    assert module.data_requirement(DataFacts(distinct={"customer_id": 100})) is None
    assert module.data_requirement(DataFacts()) is None


def test_pseudonyms_are_stable_per_tenant_and_hide_the_value() -> None:
    key = b"secret"
    a = pseudonym(key, "t1", "ann@example.com")
    assert a == pseudonym(key, "t1", "ann@example.com")
    assert a != pseudonym(key, "t2", "ann@example.com")
    assert a != pseudonym(b"other", "t1", "ann@example.com")
    assert a.startswith("cust_") and len(a) == 21 and "ann" not in a
