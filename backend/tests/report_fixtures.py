"""Helpers for report tests: facts of an analysed file, PDF text and page images."""

import io
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pypdf import PdfReader
from sqlalchemy.orm import Session

from app.models.analysis import AnalysisRun
from app.models.dataset import Dataset, DatasetVersion
from app.reports.facts import ReportFacts, build_report_facts

NOW = datetime(2026, 11, 4, 9, 30, tzinfo=UTC)


def facts_for(db: Session, run: dict[str, Any], locale: Literal["en", "ar"] = "en") -> ReportFacts:
    """Build the facts of a report on an analysed run (as the worker does)."""
    stored = db.get(AnalysisRun, uuid.UUID(run["id"]))
    assert stored is not None
    version = db.get(DatasetVersion, stored.dataset_version_id)
    assert version is not None
    dataset = db.get(Dataset, version.dataset_id)
    assert dataset is not None
    return build_report_facts(
        db,
        stored.tenant_id,
        report_id=uuid.UUID(int=1),
        dataset=dataset,
        version=version,
        run=stored,
        locale=locale,
        now=NOW,
    )


def pdf_text(pdf: bytes) -> str:
    """Return the text of every page."""
    reader = PdfReader(io.BytesIO(pdf))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def page_image(pdf: bytes, page: int = 0, scale: float = 1.5) -> Any:
    """Render one page to a PIL image."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf)
    try:
        return document[page].render(scale=scale).to_pil()
    finally:
        document.close()
