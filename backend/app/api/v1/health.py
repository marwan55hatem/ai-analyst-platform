"""Health endpoint."""

from fastapi import APIRouter

from app import __version__

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Report liveness and the running application version."""
    return {"status": "ok", "version": __version__}
