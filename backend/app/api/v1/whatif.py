"""What-if endpoint: compare up to 3 lever changes, each answered with a range."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api import responses
from app.api.deps import CurrentUser, DatasetRepo, DbSession
from app.schemas.whatif import WhatIfOut, WhatIfRequest
from app.services import whatif
from app.services.profiling import service as profiling

router = APIRouter(
    tags=["what-if"],
    responses={**responses.UNAUTHORIZED, **responses.NOT_FOUND},
)


@router.post(
    "/datasets/{dataset_id}/what-if",
    response_model=WhatIfOut,
    summary="Estimate the yearly effect of price, discount, volume or cost changes (ranges)",
    responses={**responses.CONFLICT, **responses.UNPROCESSABLE},
)
def what_if(
    dataset_id: uuid.UUID,
    body: WhatIfRequest,
    user: CurrentUser,
    db: DbSession,
    repo: DatasetRepo,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> WhatIfOut:
    """Answer each scenario with low/expected/high changes, assumptions and warnings."""
    target = profiling.resolve_version(db, repo.get_or_404(dataset_id), version)
    return WhatIfOut.model_validate(whatif.run_what_if(db, user.tenant_id, target, body))
