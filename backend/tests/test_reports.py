"""Reports: content equals stored results and the dashboard, EN/AR rendering, honesty, security."""

import csv
import io
import json
import unicodedata
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.analysis import AnalysisRun
from app.models.dataset import DatasetVersion
from app.models.report import Report
from app.reports import charts
from app.reports.facts import ReportFacts, Unavailable
from app.reports.render import FONTS_DIR, fonts_only_fetcher, render_html, render_pdf
from app.services import reports as report_service
from app.services import storage
from tests.dataset_helpers import SAMPLES, csv_bytes, upload, upload_ready
from tests.ground_truth_fixtures import analyze, generator
from tests.helpers import API, add_member, register
from tests.report_fixtures import facts_for, page_image, pdf_text

PLANTED_FACTS = SAMPLES / "ground_truth_en_anomalies_churn.json"
SECTIONS_EN = (
    "Executive summary",
    "Key figures",
    "Trend and forecast",
    "Top contributors",
    "Unusual days",
    "Customer segments",
    "Churn risk",
    "Assumptions, caveats and data quality",
    "Appendix: evidence",
)
SECTIONS_AR = (
    "الملخص التنفيذي",
    "الأرقام الرئيسية",
    "الاتجاه والتوقعات",
    "أكبر المساهمين",
    "أيام غير اعتيادية",
    "شرائح العملاء",
    "خطر فقدان العملاء",
    "ملحق: الأدلة",
)


def _planted(tmp_path: Path) -> tuple[bytes, dict[str, Any]]:
    """Generate the planted file and check it still matches the committed facts JSON."""
    csv_path, _, facts = generator().generate(tmp_path, anomalies=True, churn=True)
    committed = json.loads(PLANTED_FACTS.read_text(encoding="utf-8"))
    assert json.loads(json.dumps(facts)) == committed, "generator drifted from the committed facts"
    return csv_path.read_bytes(), committed


def _report(client: TestClient, account: Any, dataset_id: str, **body: Any) -> dict[str, Any]:
    response = client.post(
        f"{API}/datasets/{dataset_id}/reports", headers=account.headers, json=body
    )
    assert response.status_code == 202, response.text
    report: dict[str, Any] = response.json()
    return report


def _download(client: TestClient, account: Any, report_id: str) -> tuple[bytes, str]:
    response = client.get(f"{API}/reports/{report_id}/download", headers=account.headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    return response.content, response.headers["content-disposition"]


def _plain(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def _ink_box(image: Any) -> tuple[int, int, int, int]:
    """Bounding box of dark pixels."""
    grey = image.convert("L").point(lambda v: 255 if v < 160 else 0)
    box = grey.getbbox()
    assert box is not None
    return box  # type: ignore[no-any-return]


# ---- the planted file, end to end -----------------------------------------------------------


@pytest.mark.slow
def test_reports_in_english_and_arabic_match_stored_results_and_the_dashboard(
    client: TestClient, db: Session, tmp_path: Path
) -> None:
    owner = register(client)
    content, planted = _planted(tmp_path)
    dataset_id, run, _ = analyze(client, owner, content)

    english = _report(client, owner, dataset_id, locale="en")
    assert english["status"] == "completed", english
    assert english["run_id"] == run["id"] and english["progress"] == 100
    assert english["size_bytes"] > 10_000
    pdf, disposition = _download(client, owner, english["id"])
    assert pdf.startswith(b"%PDF-")
    assert disposition.startswith('attachment; filename="report-sales-v1-en-')
    assert "/" not in disposition.split("filename=")[1].split(";")[0]

    # Facts: equal to the planted answer key ...
    facts = facts_for(db, run, "en")
    assert facts.meta.rows == planted["row_count"]
    assert (facts.meta.period_start, facts.meta.period_end) == (
        planted["first_date"],
        planted["last_date"],
    )
    assert not isinstance(facts.trend, Unavailable)
    months = {p.period[:7]: p.value for p in facts.trend.history}
    for month, cents in planted["monthly_revenue_cents"].items():
        if month in months:
            assert round(months[month] * 100) == cents, month
    assert isinstance(facts.contributors, list)
    products = next(t for t in facts.contributors if t.dimension == "product")
    assert products.items[0].item == planted["top_product"]
    assert round(products.items[0].revenue * 100) == planted["top_product_revenue_cents"]
    regions = next(t for t in facts.contributors if t.dimension == "region")
    assert regions.items[0].item == planted["top_region"]
    assert facts.dead_stock is not None
    assert sorted(facts.dead_stock.names) == planted["dead_stock"]
    assert not isinstance(facts.churn, Unavailable) and facts.churn.scored_customers > 0
    assert not isinstance(facts.unusual, Unavailable) and facts.unusual.days

    # ... and to the dashboard's default KPI cards (last 90 days vs the 90 before).
    end = date.fromisoformat(planted["last_date"])
    dashboard = client.get(
        f"{API}/datasets/{dataset_id}/timeseries",
        params={"start": (end - timedelta(days=89)).isoformat(), "end": end.isoformat()},
        headers=owner.headers,
    ).json()
    assert not isinstance(facts.kpis, Unavailable)
    # DuckDB sums in parallel, so the last bits of a float may differ between two queries;
    # compare at the precision the dashboard and the report display.
    for row, card in zip(facts.kpis.rows, dashboard["kpis"], strict=True):
        assert row.key == card["key"] and row.reason == card["reason"]
        for field in ("value", "previous_value", "change"):
            mine, theirs = getattr(row, field), card[field]
            assert (mine is None) == (theirs is None), (row.key, field)
            if mine is not None:
                assert mine == pytest.approx(theirs, rel=1e-9, abs=0.005), (row.key, field)
    assert facts.kpis.range == dashboard["range"]

    # The saved facts JSON next to the PDF is what was printed.
    stored = db.get(Report, uuid.UUID(english["id"]))
    assert stored is not None and stored.file_key is not None
    key = report_service.facts_key(stored)
    assert key is not None
    saved = ReportFacts.model_validate_json(storage.get_storage().local_path(key).read_text())
    assert saved.kpis == facts.kpis and saved.contributors == facts.contributors

    # The PDF text carries the sections and the key numbers.
    text = pdf_text(pdf)
    for heading in SECTIONS_EN:
        assert heading in text, heading
    net = next(k for k in facts.kpis.rows if k.key == "net_revenue")
    assert f"{net.value:,.0f}" in text
    assert planted["top_product"] in text and f"{products.items[0].revenue:,.0f}" in text
    for name in planted["dead_stock"]:
        assert name in text
    assert "What-ifscenariosarenotincluded" in "".join(text.split())  # lines wrap anywhere

    # Arabic: shaped headings in logical order, right-to-left page, same numbers.
    arabic = _report(client, owner, dataset_id, locale="ar")
    assert arabic["status"] == "completed", arabic
    ar_pdf, ar_disposition = _download(client, owner, arabic["id"])
    assert "-ar-" in ar_disposition
    ar_text = _plain(pdf_text(ar_pdf))
    for heading in SECTIONS_AR:
        assert heading in ar_text, heading
    assert f"{net.value:,.0f}" in ar_text
    assert "التسرب" not in ar_text  # churn is فقدان العملاء, as in the app
    width = page_image(ar_pdf).size[0]
    title_en = _ink_box(page_image(pdf).crop((0, 200, width, 420)))
    title_ar = _ink_box(page_image(ar_pdf).crop((0, 200, width, 420)))
    assert title_en[0] < width * 0.15 and title_ar[2] > width * 0.85  # start edge differs
    assert title_ar[0] > width * 0.3  # the Arabic title hugs the right margin

    listing = client.get(f"{API}/reports", headers=owner.headers).json()
    assert listing["total"] == 2 and listing["items"][0]["id"] == arabic["id"]
    only_en = client.get(
        f"{API}/reports",
        params={"dataset_id": dataset_id, "status": "completed"},
        headers=owner.headers,
    ).json()
    assert only_en["total"] == 2
    assert client.get(f"{API}/reports?status=failed", headers=owner.headers).json()["total"] == 0
    detail = client.get(f"{API}/reports/{english['id']}", headers=owner.headers).json()
    assert detail["locale"] == "en" and detail["title"] == "Sales"

    deleted = client.delete(f"{API}/reports/{english['id']}", headers=owner.headers)
    assert deleted.status_code == 204
    assert not storage.get_storage().exists(stored.file_key)
    gone = client.get(f"{API}/reports/{english['id']}", headers=owner.headers)
    assert gone.status_code == 404


def test_arabic_text_is_shaped() -> None:
    """Joined (shaped) Arabic draws differently from the same letters forced apart."""
    from weasyprint import HTML

    font = (FONTS_DIR / "IBMPlexSansArabic-Regular.ttf").as_uri()
    page = (
        f'<html dir="rtl" lang="ar"><style>@font-face{{font-family:P;src:url("{font}")}}'
        "html{font-family:P;font-size:40pt}</style><body><p>{}</p></body></html>"
    )
    word = "تقرير التحليل"
    joined = HTML(string=page.replace("{}", word), url_fetcher=fonts_only_fetcher()).write_pdf()
    apart = HTML(
        string=page.replace("{}", "‌".join(word)), url_fetcher=fonts_only_fetcher()
    ).write_pdf()
    a, b = page_image(joined), page_image(apart)
    assert a.tobytes() != b.tobytes()
    assert _ink_box(a)[2] - _ink_box(a)[0] < _ink_box(b)[2] - _ink_box(b)[0]


# ---- honesty, escaping and personal data ------------------------------------------------------


def _rewrite(content: bytes, change: Any) -> bytes:
    rows = list(csv.DictReader(io.StringIO(content.decode("utf-8"))))
    fields = list(rows[0].keys())
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(change(row))
    return out.getvalue().encode("utf-8")


HOSTILE_NAME = '<script>alert(1)</script><img src="http://169.254.169.254/x">{{7*7}}'
HOSTILE_ITEM = '<img src="http://127.0.0.1:9/leak">Item-07'


@pytest.mark.slow
def test_hostile_names_are_escaped_and_personal_data_never_appears(
    client: TestClient, db: Session, tmp_path: Path
) -> None:
    owner = register(client)
    content, _ = _planted(tmp_path)

    def change(row: dict[str, str]) -> dict[str, str]:
        row["customer_id"] = f"{row['customer_id'].lower()}@example.com"  # personal data
        if row["product"] == "Item-07":
            row["product"] = HOSTILE_ITEM
        return row

    hostile = _rewrite(content, change)
    made = upload(client, owner, hostile, "evil.csv", name=HOSTILE_NAME)
    assert made.status_code == 201, made.text
    dataset_id = made.json()["id"]
    run = client.post(f"{API}/datasets/{dataset_id}/analyze", headers=owner.headers).json()
    assert run["status"].startswith("completed")

    facts = facts_for(db, run, "en")
    html = render_html(facts)
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert '<img src="http' not in html and "&lt;img src=&#34;http" in html
    assert "{{7*7}}" in html and "49" not in facts.meta.dataset_name

    report = _report(client, owner, dataset_id, locale="en")
    assert report["status"] == "completed", report  # nothing was fetched, nothing failed
    pdf, disposition = _download(client, owner, report["id"])
    text = pdf_text(pdf)
    flat = "".join(text.split())
    assert "<script>alert(1)</script>" in flat  # shown as text, never run
    assert "".join(HOSTILE_ITEM.split()) in flat
    assert "@example.com" not in text and "c0430" not in text.lower()
    assert 'filename="report-script-alert-1-script-img-src-http-169-254-169-254-x-7-7-v1-en-' in (
        disposition
    )
    assert "\n" not in disposition and '";' in disposition


@pytest.mark.slow
def test_skipped_modules_show_not_available(
    client: TestClient, db: Session, tmp_path: Path
) -> None:
    owner = register(client)
    content, _ = _planted(tmp_path)
    no_customers = _rewrite(content, lambda r: {**r, "customer_id": ""})
    dataset_id, run, _ = analyze(client, owner, no_customers)
    facts = facts_for(db, run, "en")
    for section in (facts.segments, facts.churn):
        assert isinstance(section, Unavailable) and section.kind == "skipped"
        assert section.reason
    text = pdf_text(render_pdf(facts, 50_000_000))
    assert text.count("Not available") >= 2
    assert facts.segments.reason.split(",")[0] in text.replace("\n", " ")  # type: ignore[union-attr]
    ar_text = _plain(pdf_text(render_pdf(facts_for(db, run, "ar"), 50_000_000)))
    assert "غير متاح" in ar_text


# ---- limits, permissions and housekeeping -----------------------------------------------------


def _ready_dataset(client: TestClient, owner: Any, db: Session, name: str = "Small") -> str:
    """Upload a tiny dataset and mark its version as analysed (no real analysis needed)."""
    rows = [[f"2024-01-{d:02d}", d * 10] for d in range(1, 29)]
    made = upload_ready(client, owner, csv_bytes(rows, ["order_date", "revenue"]), "s.csv")
    version = db.scalars(
        select(DatasetVersion).where(DatasetVersion.dataset_id == uuid.UUID(made["id"]))
    ).one()
    db.add(
        AnalysisRun(
            tenant_id=version.tenant_id,
            dataset_version_id=version.id,
            status="completed",
            progress={"modules": {}},
            config={},
        )
    )
    db.commit()
    return str(made["id"])


def _row(db: Session, owner: Any, dataset_id: str, status: str, **extra: Any) -> Report:
    report = Report(
        tenant_id=uuid.UUID(owner.tenant_id),
        dataset_id=uuid.UUID(dataset_id),
        version=1,
        locale="en",
        status=status,
        progress=0,
        title="Small",
        created_by=uuid.UUID(owner.user_id),
        **extra,
    )
    db.add(report)
    db.commit()
    return report


def test_one_report_at_a_time_and_a_cap_per_company(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = register(client)
    dataset_id = _ready_dataset(client, owner, db)
    _row(db, owner, dataset_id, "running")
    busy = client.post(f"{API}/datasets/{dataset_id}/reports", headers=owner.headers, json={})
    assert busy.status_code == 429 and busy.json()["code"] == "report_busy"
    assert busy.headers["retry-after"] == "10"
    other = register(client)  # another company is not blocked
    other_data = _ready_dataset(client, other, db)
    monkeypatch.setattr(get_settings(), "report_max_per_tenant", 1)
    _row(db, other, other_data, "completed")
    capped = client.post(f"{API}/datasets/{other_data}/reports", headers=other.headers, json={})
    assert capped.status_code == 409 and capped.json()["code"] == "report_limit"


def test_report_needs_a_finished_analysis_and_a_valid_body(client: TestClient) -> None:
    owner = register(client)
    rows = [[f"2024-01-{d:02d}", d] for d in range(1, 10)]
    made = upload_ready(client, owner, csv_bytes(rows, ["order_date", "revenue"]), "n.csv")
    url = f"{API}/datasets/{made['id']}/reports"
    missing = client.post(url, headers=owner.headers, json={"locale": "en"})
    assert missing.status_code == 409 and missing.json()["code"] == "report_no_analysis"
    assert client.post(url, headers=owner.headers, json={"locale": "fr"}).status_code == 422
    assert client.post(url, headers=owner.headers, json={"version": 0}).status_code == 422
    assert client.post(url, headers=owner.headers, json={"version": 9}).status_code == 404


def test_a_render_failure_is_reported_honestly(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = register(client)
    dataset_id = _ready_dataset(client, owner, db)

    def boom(*_: Any, **__: Any) -> bytes:
        raise RuntimeError("renderer crashed")

    monkeypatch.setattr("app.reports.render.render_pdf", boom)
    failed = _report(client, owner, dataset_id, locale="en")
    assert failed["status"] == "failed" and failed["error_code"] == "render_failed"
    not_ready = client.get(f"{API}/reports/{failed['id']}/download", headers=owner.headers)
    assert not_ready.status_code == 409 and not_ready.json()["code"] == "report_not_ready"

    from app.reports.render import ReportTooLarge

    def too_big(*_: Any, **__: Any) -> bytes:
        raise ReportTooLarge("big")

    monkeypatch.setattr("app.reports.render.render_pdf", too_big)
    big = _report(client, owner, dataset_id, locale="ar")
    assert big["error_code"] == "too_large"


def test_a_minimal_run_still_renders_with_every_section_explained(
    client: TestClient, db: Session
) -> None:
    owner = register(client)
    dataset_id = _ready_dataset(client, owner, db)
    report = _report(client, owner, dataset_id, locale="en")
    assert report["status"] == "completed", report  # the summary is built lazily
    pdf, _ = _download(client, owner, report["id"])
    text = pdf_text(pdf)
    assert "Key figures" in text and "Not available" in text
    stored = db.get(AnalysisRun, uuid.UUID(report["run_id"]))
    assert stored is not None and stored.summary is not None


def test_delete_permissions_and_running_reports(client: TestClient, db: Session) -> None:
    owner = register(client)
    member = add_member(client, owner, "member")
    admin = add_member(client, owner, "admin")
    dataset_id = _ready_dataset(client, owner, db)
    mine = _row(db, owner, dataset_id, "completed")
    assert client.delete(f"{API}/reports/{mine.id}", headers=member.headers).status_code == 403
    assert client.delete(f"{API}/reports/{mine.id}", headers=admin.headers).status_code == 204
    theirs = _row(db, member, dataset_id, "completed")
    assert client.delete(f"{API}/reports/{theirs.id}", headers=member.headers).status_code == 204
    running = _row(db, owner, dataset_id, "running")
    busy = client.delete(f"{API}/reports/{running.id}", headers=owner.headers)
    assert busy.status_code == 409 and busy.json()["code"] == "report_running"


def test_stuck_reports_fail_and_old_reports_are_purged(client: TestClient, db: Session) -> None:
    owner = register(client)
    dataset_id = _ready_dataset(client, owner, db)
    backend = storage.get_storage()
    key = storage.report_key(uuid.UUID(owner.tenant_id))
    backend.save_bytes(key, b"%PDF-old")
    backend.save_bytes(key[:-4] + ".json", b"{}")
    old = _row(db, owner, dataset_id, "completed", file_key=key)
    stuck = _row(db, owner, dataset_id, "running")
    fresh = _row(db, owner, dataset_id, "completed")
    now = datetime.now(UTC)
    assert report_service.fail_stuck_reports(db, now) == 0
    assert report_service.fail_stuck_reports(db, now + timedelta(hours=1)) == 1
    db.refresh(stuck)
    assert stuck.status == "failed" and stuck.error_code == "timed_out"
    later = now + timedelta(days=get_settings().report_retention_days + 1)
    assert report_service.purge_expired_reports(db, later) == 3
    assert not backend.exists(key) and not backend.exists(key[:-4] + ".json")
    assert db.get(Report, old.id) is None and db.get(Report, fresh.id) is None


def test_download_of_a_missing_file_is_a_clear_conflict(client: TestClient, db: Session) -> None:
    owner = register(client)
    dataset_id = _ready_dataset(client, owner, db)
    lost = _row(db, owner, dataset_id, "completed", file_key=f"{owner.tenant_id}/reports/x.pdf")
    response = client.get(f"{API}/reports/{lost.id}/download", headers=owner.headers)
    assert response.status_code == 409 and response.json()["code"] == "report_missing"


# ---- units --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Sales 2024", "sales-2024"),
        ("../../etc/passwd", "etc-passwd"),
        ('a"; filename=evil.exe', "a-filename-evil-exe"),
        ("مبيعات", "dataset"),
        ("Café Ünïcode", "cafe-unicode"),
        ("x" * 200, "x" * 60),
    ],
)
def test_slug_is_ascii_and_safe(title: str, expected: str) -> None:
    assert report_service.slug(title) == expected


def test_content_disposition_carries_a_utf8_name_without_header_injection() -> None:
    report = Report(
        title='مبيعات\r\nX: "y"/../z',
        version=2,
        locale="ar",
        created_at=datetime(2026, 11, 4, tzinfo=UTC),
    )
    header = report_service.content_disposition(report)
    assert "\r" not in header and "\n" not in header
    assert "/" not in header.split("''")[1]
    assert header.startswith('attachment; filename="report-x-y-z-v2-ar-20261104.pdf"; ')
    assert "filename*=UTF-8''report-%D9%85" in header and "%22" not in header


def test_fetcher_serves_bundled_fonts_and_blocks_everything_else() -> None:
    from weasyprint import HTML
    from weasyprint.urls import FatalURLFetchingError

    fetcher = fonts_only_fetcher()
    font = (FONTS_DIR / "IBMPlexSans-Regular.ttf").as_uri()
    assert fetcher(font).read()[:4] in (b"\x00\x01\x00\x00", b"true", b"OTTO")
    outside = (FONTS_DIR / "OFL.txt").as_uri()
    for url in (
        "http://169.254.169.254/latest/meta-data",
        "https://example.com/x.png",
        "data:image/png;base64,iVBORw0KGgo=",
        "ftp://example.com/x",
        outside,
        Path(__file__).resolve().as_uri(),
        (FONTS_DIR / ".." / "render.py").as_uri(),
    ):
        with pytest.raises(FatalURLFetchingError):
            fetcher(url)
    with pytest.raises(FatalURLFetchingError):
        HTML(string='<img src="http://127.0.0.1:9/x">', url_fetcher=fetcher).write_pdf()


def test_charts_hold_only_numbers_and_periods() -> None:
    assert charts.compact(1_234_567) == "1.2M" and charts.compact(2_500_000_000) == "2.5B"
    assert charts.compact(4_200) == "4k" and charts.compact(12) == "12"
    assert charts.bars_svg([], "x") == ""
    svg = charts.bars_svg([10, 5, 0], 'label "quoted" <b>', 1, rtl=True)
    assert 'aria-label="label &quot;quoted&quot; &lt;b&gt;"' in svg
    assert svg.count("<rect") == 3


def test_kpi_change_of_zero_is_not_negative() -> None:
    from app.reports.render import Formatter

    facts = ReportFacts.model_validate_json(
        json.dumps(
            {
                "meta": {
                    "report_id": "r",
                    "dataset_name": "d",
                    "version": 1,
                    "locale": "en",
                    "run_id": "x",
                    "run_date": "2026-01-01",
                    "generated_at": "2026-01-01T00:00:00",
                    "period_start": None,
                    "period_end": None,
                    "rows": None,
                    "quality_score": None,
                    "currency": "SAR",
                },
                "findings": [],
                "kpis": {"kind": "empty", "reason": "r"},
                "trend": {"kind": "empty", "reason": "r"},
                "contributors": {"kind": "empty", "reason": "r"},
                "dead_stock": None,
                "unusual": {"kind": "empty", "reason": "r"},
                "segments": {"kind": "empty", "reason": "r"},
                "churn": {"kind": "empty", "reason": "r"},
                "caveats": [],
                "quality_issues": [],
                "modules": [],
                "appendix": [],
            }
        )
    )
    fmt = Formatter(facts)
    assert "-0.0" not in str(fmt.ch(-0.04, "points")) and "+1.5%" in str(fmt.ch(1.5, "percent"))
    assert "SAR 1,234" in str(fmt.money(1234.4)) and "–" in str(fmt.money(None))
    assert "Jan" in str(fmt.p("2025-01-01", "month")) and "2025-01-06" in str(
        fmt.p("2025-01-06", "week")
    )
    assert pdf_text(render_pdf(facts, 50_000_000)).count("Nothing to report") >= 5
