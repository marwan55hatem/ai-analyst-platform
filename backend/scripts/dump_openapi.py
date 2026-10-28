"""Write the API's OpenAPI schema to a file without starting a server.

Used by `make api-client`: the frontend generates its typed client from this file, and
CI fails when the committed copy is out of date. Importing the app needs no database or
Redis; it only needs settings that pass the startup checks, so a fixed placeholder
JWT secret is used when none is set (the schema does not depend on it).
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

# Not a secret: only satisfies the startup length check while the schema is built.
_PLACEHOLDER_JWT_SECRET = "openapi-dump-placeholder-not-a-secret-0000"


def build_schema() -> dict[str, Any]:
    """Import the FastAPI app and return its OpenAPI schema."""
    os.environ.setdefault("JWT_SECRET", _PLACEHOLDER_JWT_SECRET)
    # The schema never depends on the model provider; "none" avoids provider checks.
    os.environ["LLM_PROVIDER"] = "none"
    # Settings read .env and ../.env from the working directory. Import from an empty
    # folder so a developer's .env cannot make the schema differ from CI's.
    cwd = Path.cwd()
    with tempfile.TemporaryDirectory() as tmp:
        empty = Path(tmp) / "cwd"
        empty.mkdir()
        os.chdir(empty)
        try:
            from app.main import app
        finally:
            os.chdir(cwd)
    return app.openapi()


def render(schema: dict[str, Any]) -> str:
    """Serialise the schema deterministically (sorted keys, LF, trailing newline)."""
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Dump the schema to the given path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="where to write openapi.json")
    args = parser.parse_args(argv)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(render(build_schema()))
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.exit(main())
