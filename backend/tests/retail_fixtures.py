"""Generated retail fixtures (clean/messy, English/Arabic) and helpers to profile them."""

import importlib.util
from functools import cache
from pathlib import Path
from types import ModuleType

from app.services.ingest import ingest_file
from app.services.profiling.compute import ProfileResult, compute_profile

GENERATOR = (
    Path(__file__).resolve().parents[2]
    / "evaluation"
    / "datasets"
    / "generators"
    / "retail_generator.py"
)


@cache
def generator() -> ModuleType:
    """Import the shared fixture generator from evaluation/."""
    spec = importlib.util.spec_from_file_location("retail_generator", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_csv(variant: str, lang: str = "en") -> bytes:
    """Return the bytes of a generated fixture (`clean` or `messy`)."""
    gen = generator()
    return gen.make_clean_csv(lang=lang) if variant == "clean" else gen.make_messy_csv(lang=lang)


def profile_csv(tmp_path: Path, content: bytes, name: str = "data") -> ProfileResult:
    """Ingest CSV bytes to Parquet and profile the result (no database involved)."""
    src = tmp_path / f"{name}.csv"
    src.write_bytes(content)
    dst = tmp_path / f"{name}.parquet"
    result = ingest_file(src, dst, "csv", max_rows=1_000_000)
    return compute_profile(dst, result.column_map)


def ingest_csv(tmp_path: Path, content: bytes, name: str = "data") -> tuple[Path, list[dict]]:
    """Ingest CSV bytes and return (Parquet path, column map)."""
    src = tmp_path / f"{name}.csv"
    src.write_bytes(content)
    dst = tmp_path / f"{name}.parquet"
    result = ingest_file(src, dst, "csv", max_rows=1_000_000)
    return dst, result.column_map
