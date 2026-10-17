"""PII detection and masking."""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.services.profiling import pii
from app.services.profiling.compute import compute_profile
from app.services.profiling.results import ColumnResult


def _profile(tmp_path: Path, columns: dict[str, list[str]]) -> dict[str, ColumnResult]:
    path = tmp_path / "p.parquet"
    pq.write_table(pa.table(columns), path)
    return {c.name: c for c in compute_profile(path).columns}


def test_masking_never_leaks_the_original() -> None:
    assert pii.mask_email("jane.doe@example.com") == "j***@e***.com"
    assert pii.mask_digits("+201012345678") == "+**********78"
    assert pii.mask_value("jane@example.com", "email") == "j***@e***.com"
    assert pii.mask_value("12345678901234", "national_id").endswith("34")
    assert "1234567890" not in pii.mask_value("12345678901234", "national_id")
    assert pii.mask_value("Alice", None) == "A***"


def test_free_text_is_masked() -> None:
    text = pii.mask_text("Contact jane.doe@example.com or +20 100 123 4567 id 12345678901")
    assert "jane.doe" not in text and "example.com" not in text
    assert "100 123 4567" not in text
    assert "12345678901" not in text


def test_safe_sample_is_masked_and_truncated() -> None:
    sample = pii.safe_sample("x" * 200)
    assert len(sample) == pii.MAX_SAMPLE_CHARS
    assert "@" not in pii.safe_sample("mail me: bob@site.org")[:6] or "bob" not in pii.safe_sample(
        "mail me: bob@site.org"
    )


def test_email_phone_and_national_id_columns_are_flagged(tmp_path: Path) -> None:
    n = 40
    cols = _profile(
        tmp_path,
        {
            "contact": [f"user{i}@example.com" for i in range(n)],
            "line": [f"+2010{10_000_000 + i}" for i in range(n)],
            "formatted": [f"(555) 123-{1000 + i}" for i in range(n)],
            "gov": [f"{29_000_000_000_000 + i}" for i in range(n)],
            "ssn_like": [f"123-45-{1000 + i}" for i in range(n)],
            "city": ["Cairo", "Giza"] * (n // 2),
        },
    )
    assert cols["contact"].pii_kind == "email"
    assert cols["line"].pii_kind == "phone"
    assert cols["formatted"].pii_kind == "phone"
    assert cols["gov"].pii_kind == "national_id"
    assert cols["ssn_like"].pii_kind == "national_id"
    assert cols["city"].pii_kind is None


def test_name_hint_flags_columns_conservatively(tmp_path: Path) -> None:
    cols = _profile(tmp_path, {"customer_email": ["n/a"] * 5, "mobile": ["unknown"] * 5})
    assert cols["customer_email"].pii_kind == "email"
    assert cols["mobile"].pii_kind == "phone"


def test_plain_numbers_are_not_phones(tmp_path: Path) -> None:
    cols = _profile(tmp_path, {"amount": [f"{1_000_000 + i}" for i in range(40)]})
    assert cols["amount"].pii_kind is None


def test_stored_top_values_of_pii_columns_are_masked(tmp_path: Path) -> None:
    cols = _profile(tmp_path, {"email": [f"user{i}@example.com" for i in range(30)]})
    values = [t["value"] for t in cols["email"].stats["top_values"]]
    assert values
    assert all("example.com" not in v and v.startswith("u***@") for v in values)
    assert cols["email"].pii_flag is True


@pytest.mark.parametrize(
    ("name", "kind"),
    [("email", "email"), ("work_mail", "email"), ("phone_number", "phone"), ("ssn", "national_id")],
)
def test_name_hints(name: str, kind: str) -> None:
    assert pii.name_hint(name) == kind


def test_name_hint_none() -> None:
    assert pii.name_hint("revenue") is None
