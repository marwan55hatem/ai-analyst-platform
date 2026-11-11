"""Codes back to real ids for display, and real ids typed by the user to codes for the model."""

from app.agent.demask import (
    StreamDemasker,
    demask_evidence,
    demask_text,
    pseudonymise_question,
)
from app.agent.verifier import untraced_numbers
from app.core.pseudonyms import PseudonymMap

MAP = PseudonymMap.build(b"k" * 32, "t1", "d1", ["C-001", "7003", "ACME Ltd"], column="customer")
C1 = MAP.to_pseudonym("C-001") or ""
N1 = MAP.to_pseudonym("7003") or ""


def test_text_shows_real_ids_and_leaves_unknown_codes() -> None:
    text = f"Top customer {C1} bought more than {N1}; CUST-ZZZZZZZZ is not ours."
    assert (
        demask_text(MAP, text)
        == "Top customer C-001 bought more than 7003; CUST-ZZZZZZZZ is not ours."
    )


def test_stream_holds_back_a_code_split_across_deltas() -> None:
    demasker = StreamDemasker(MAP)
    pieces = ["Top is ", C1[:3], C1[3:7], C1[7:] + " with", " 5 orders", f" and {N1}"]
    shown = "".join(demasker.feed(p) for p in pieces) + demasker.flush()
    assert shown == "Top is C-001 with 5 orders and 7003"
    assert all(C1[:5] not in demasker.feed(p) for p in ["x"])


def test_stream_without_a_map_passes_text_through() -> None:
    demasker = StreamDemasker(None)
    assert demasker.feed("CUST-AB") + demasker.flush() == "CUST-AB"


def test_evidence_rows_sql_and_nested_values_are_mapped() -> None:
    evidence = {
        "sql": f"SELECT * FROM data WHERE customer = '{C1}'",
        "rows": [[C1, 10.5], [N1, 7003]],
        "result": {"top": [C1]},
        "stdout": f"{N1}\n",
        "row_count": 2,
    }
    shown = demask_evidence(MAP, evidence)
    assert shown["sql"] == "SELECT * FROM data WHERE customer = 'C-001'"
    assert shown["rows"] == [["C-001", 10.5], ["7003", 7003]]
    assert shown["result"] == {"top": ["C-001"]} and shown["stdout"] == "7003\n"
    assert evidence["rows"][0][0] == C1  # the input is not changed


def test_typed_ids_become_codes_in_any_case_and_with_punctuation() -> None:
    assert pseudonymise_question(MAP, "How much did c-001 spend?") == f"How much did {C1} spend?"
    assert pseudonymise_question(MAP, "Orders of (C-001), please") == f"Orders of ({C1}), please"
    assert pseudonymise_question(MAP, "Is customer 7003 active?") == f"Is customer {N1} active?"
    assert pseudonymise_question(MAP, "Revenue last month?") == "Revenue last month?"
    assert (
        pseudonymise_question(MAP, "What about ACME Ltd?")
        == f"What about {MAP.to_pseudonym('ACME Ltd')}?"
    )
    assert pseudonymise_question(None, "c-001") == "c-001"


def test_verifier_ignores_digits_inside_customer_codes() -> None:
    code = "CUST-45KK9932"
    answer = f"{code} spent 120.5 last year; cust_0123456789abcdef too."
    assert untraced_numbers(answer, [{"rows": [[code, 120.5]]}], "") == []


def test_hidden_personal_ids_show_the_pages_pseudonym_not_the_value() -> None:
    from app.ml.modules.segments import pseudonym as legacy

    hidden = MAP.for_display(hidden=True)
    shown = legacy(b"k" * 32, "t1", "C-001")
    assert demask_text(hidden, f"Top is {C1}") == f"Top is {shown}"
    assert demask_text(MAP.for_display(hidden=False), f"Top is {C1}") == "Top is C-001"
    # a user can paste the pseudonym the page shows, or the real id
    assert pseudonymise_question(hidden, f"orders of {shown}?") == f"orders of {C1}?"
    assert pseudonymise_question(hidden, "orders of C-001?") == f"orders of {C1}?"


def test_evidence_dict_keys_are_mapped_too() -> None:
    """A Python result such as groupby(...).to_dict() is keyed by customer code."""
    shown = demask_evidence(MAP, {"result": {C1: 120.5, N1: 7.0, "total": 1}})
    assert shown == {"result": {"C-001": 120.5, "7003": 7.0, "total": 1}}


NUMERIC = PseudonymMap.build(b"k" * 32, "t1", "d1", ["10", "2024", "5000", "7003"], column="id")


def test_plain_numbers_stay_numbers_when_ids_are_numeric() -> None:
    """Review finding: an integer id column must not turn amounts, years or counts into codes."""
    question = "Top 10 customers in 2024 with revenue over 5000?"
    assert pseudonymise_question(NUMERIC, question) == question
    code = NUMERIC.to_pseudonym("7003")
    assert pseudonymise_question(NUMERIC, "How much did customer 7003 spend?") == (
        f"How much did customer {code} spend?"
    )
    assert pseudonymise_question(NUMERIC, "orders of client #7003") == f"orders of client #{code}"
    assert pseudonymise_question(NUMERIC, "العميل ٧٠٠٣ نشط؟") == f"العميل {code} نشط؟"


def test_ids_inside_common_typed_forms_are_found() -> None:
    """Review finding: #id, id=, customer:id and a/b lists must not slip through."""
    c2 = MAP.to_pseudonym("C-001")
    assert pseudonymise_question(MAP, "compare C-001/7003") == f"compare {c2}/{N1}"
    assert pseudonymise_question(MAP, "customer:C-001 and id=7003") == (
        f"customer:{c2} and id={N1}"
    )
    assert pseudonymise_question(MAP, "is #7003 active") == f"is #{N1} active"
