"""LLM role refinement: privacy of what is sent, and validation of what comes back."""

import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.llm import FakeLLMClient, LLMError, get_llm_client
from app.services.profiling.compute import compute_profile
from app.services.profiling.engine import open_parquet, schema_of
from app.services.profiling.results import ColumnResult
from app.services.profiling.role_refine import (
    MAX_SAMPLES,
    SYSTEM_PROMPT,
    apply_answer,
    build_request,
    candidates,
    collect_samples,
    is_consistent,
    refine_roles,
)
from tests.retail_fixtures import build_csv, ingest_csv

EMAIL = "secret.person@example.com"
PHONE_DIGITS = "201012345678"


def _file(tmp_path: Path) -> Path:
    n = 60
    path = tmp_path / "r.parquet"
    pq.write_table(
        pa.table(
            {
                "xq1": [float(i) * 3.5 for i in range(n)],  # unknown numeric -> weak role
                "notes": [f"call {PHONE_DIGITS}{i} or mail jo{i}@example.com" for i in range(n)],
                "cust_contact": [f"{EMAIL.split('@')[0]}{i}@example.com" for i in range(n)],
                "ph": [f"+{PHONE_DIGITS}{i:02d}" for i in range(n)],
                "label": [f"grp{i % 7}" for i in range(n)],
            }
        ),
        path,
    )
    return path


def _run(
    path: Path, answer: dict[str, Any] | None = None
) -> tuple[FakeLLMClient, list[ColumnResult]]:
    client = FakeLLMClient(answer if answer is not None else {"columns": {}})
    result = compute_profile(path, llm=client)
    return client, result.columns


def test_only_names_and_masked_samples_are_sent(tmp_path: Path) -> None:
    client, _ = _run(_file(tmp_path))
    assert len(client.calls) == 1
    call = client.calls[0]
    payload = call.payload()
    assert set(payload) == {"columns"}
    for entry in payload["columns"]:
        assert set(entry) == {"name", "key", "samples"}
        assert len(entry["samples"]) <= MAX_SAMPLES
    wire = call.system + call.user
    assert EMAIL.split("@")[0] not in wire and "example.com" not in wire
    assert PHONE_DIGITS not in wire


def test_no_values_of_pii_flagged_columns_are_sent(tmp_path: Path) -> None:
    client, columns = _run(_file(tmp_path))
    flagged = {c.name for c in columns if c.pii_flag}
    assert {"cust_contact", "ph"} <= flagged
    for entry in client.calls[0].payload()["columns"]:
        if entry["key"] in flagged:
            assert entry["samples"] == []


def test_free_text_samples_are_masked(tmp_path: Path) -> None:
    client, _ = _run(_file(tmp_path))
    notes = next(e for e in client.calls[0].payload()["columns"] if e["key"] == "notes")
    assert notes["samples"], "free text should still give masked examples"
    joined = " ".join(notes["samples"])
    assert "@example.com" not in joined and PHONE_DIGITS not in joined


def test_never_more_than_five_samples_and_never_rows(tmp_path: Path) -> None:
    path = _file(tmp_path)
    with open_parquet(path) as con:
        cols = [
            ColumnResult(name=n, original_name=n, inferred_type="text", stats={})
            for n, _ in schema_of(con)
        ]
        samples = collect_samples(con, cols, dict(schema_of(con)))
    assert all(len(v) <= MAX_SAMPLES for v in samples.values())
    request = json.loads(build_request(cols, samples))
    assert all(len(c["samples"]) <= MAX_SAMPLES for c in request["columns"])
    # A row would show values of two columns together; samples only list one column's values.
    assert all(isinstance(s, str) for c in request["columns"] for s in c["samples"])


def test_only_weak_columns_are_sent(tmp_path: Path) -> None:
    client, columns = _run(_file(tmp_path))
    sent = {e["key"] for e in client.calls[0].payload()["columns"]}
    assert sent == {c.name for c in candidates(columns)}


def test_no_call_when_all_roles_are_confident(tmp_path: Path) -> None:
    content = build_csv("clean")
    path, cmap = ingest_csv(tmp_path, content)
    client = FakeLLMClient()
    result = compute_profile(path, cmap, llm=client)
    weak = [c for c in result.columns if c.role_confidence < 0.7]
    assert len(client.calls) == (1 if weak else 0)


def test_valid_answer_is_applied(tmp_path: Path) -> None:
    answer = {"columns": {"xq1": {"role": "revenue", "confidence": 0.8}}}
    _, columns = _run(_file(tmp_path), answer)
    xq1 = next(c for c in columns if c.name == "xq1")
    assert (xq1.semantic_role, xq1.role_source, xq1.role_confidence) == ("revenue", "llm", 0.8)


def test_answer_conflicting_with_the_profile_is_discarded(tmp_path: Path) -> None:
    answer = {
        "columns": {
            "label": {"role": "revenue", "confidence": 0.95},  # text can't be revenue
            "xq1": {"role": "email", "confidence": 0.95},  # numeric can't be an email
            "notes": {"role": "date", "confidence": 0.9},  # text is not dates
        }
    }
    _, columns = _run(_file(tmp_path), answer)
    by_name = {c.name: c for c in columns}
    for name in ("label", "xq1", "notes"):
        assert by_name[name].role_source == "rule"
    assert by_name["label"].semantic_role != "revenue"


def test_invented_roles_and_malformed_answers_are_ignored(tmp_path: Path) -> None:
    path = _file(tmp_path)
    for answer in (
        {"columns": {"xq1": {"role": "banana", "confidence": 0.9}}},
        {"columns": {"missing": {"role": "revenue", "confidence": 0.9}}},
        {"columns": {"xq1": "revenue"}},
        {"columns": []},
        {"nothing": 1},
        {"columns": {"xq1": {"role": "revenue", "confidence": "high"}}},
    ):
        _, columns = _run(path, answer)
        assert all(c.role_source == "rule" for c in columns), answer


def test_user_roles_are_never_overridden(tmp_path: Path) -> None:
    path = _file(tmp_path)
    client = FakeLLMClient({"columns": {"xq1": {"role": "revenue", "confidence": 0.9}}})
    result = compute_profile(path, user_roles={"xq1": "cost"}, llm=client)
    xq1 = next(c for c in result.columns if c.name == "xq1")
    assert (xq1.semantic_role, xq1.role_source) == ("cost", "user")
    assert "xq1" not in {e["key"] for e in client.calls[0].payload()["columns"]}


def test_llm_must_beat_the_rule_confidence(tmp_path: Path) -> None:
    column = ColumnResult("x", "x", "float", {"min": 0, "max": 1}, role_confidence=0.6)
    column.semantic_role, column.role_source = "price", "rule"
    assert apply_answer({"columns": {"x": {"role": "revenue", "confidence": 0.5}}}, [column]) == 0
    assert apply_answer({"columns": {"x": {"role": "revenue", "confidence": 0.99}}}, [column]) == 1
    assert column.role_confidence == 0.9  # capped: the model is never fully trusted


def test_failures_leave_rule_roles_untouched(tmp_path: Path) -> None:
    def boom(_: dict[str, Any]) -> dict[str, Any]:
        raise LLMError("down")

    result = compute_profile(_file(tmp_path), llm=FakeLLMClient(boom))
    assert all(c.role_source == "rule" for c in result.columns)


def test_no_client_means_no_refinement(tmp_path: Path) -> None:
    path = _file(tmp_path)
    with open_parquet(path) as con:
        assert refine_roles(con, [], {}, None) == 0


@pytest.mark.parametrize(
    ("role", "ctype", "stats", "pii", "expected"),
    [
        ("revenue", "float", {}, None, True),
        ("revenue", "text", {}, None, False),
        ("date", "datetime", {}, None, True),
        ("date", "integer", {}, None, False),
        ("email", "text", {}, "email", True),
        ("email", "text", {}, None, False),
        ("churn_flag", "integer", {"min": 0, "max": 1}, None, True),
        ("churn_flag", "integer", {"min": 0, "max": 50}, None, False),
        ("customer_id", "identifier", {}, None, True),
        ("customer_id", "float", {}, None, False),
        ("region", "categorical", {}, None, True),
        ("region", "float", {}, None, False),
        ("nonsense", "text", {}, None, False),
    ],
)
def test_consistency_rules(
    role: str, ctype: str, stats: dict[str, Any], pii: str | None, expected: bool
) -> None:
    column = ColumnResult("c", "c", ctype, stats, pii_kind=pii)
    assert is_consistent(role, column) is expected


def test_prompt_lists_only_allowed_roles() -> None:
    assert "revenue" in SYSTEM_PROMPT and "churn_flag" in SYSTEM_PROMPT


def test_factory_returns_none_without_a_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "llm_provider", "none")
    assert get_llm_client() is None
    monkeypatch.setattr(get_settings(), "llm_provider", "fake")
    from app.llm import ScriptedAnalyst

    scripted = get_llm_client()
    assert isinstance(scripted, ScriptedAnalyst)
    assert scripted.complete_json("system", "{}") == {"columns": {}}  # roles stay rule-based
    monkeypatch.setattr(get_settings(), "llm_provider", "someone")
    with pytest.raises(LLMError):
        get_llm_client()
