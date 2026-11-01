"""Company profile, conversations and the streaming chat endpoint (Server-Sent Events)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse

from app.agent.service import ChatSession, message_cost, require_manage
from app.agent.streams import get_stream_slots, with_heartbeat
from app.api import responses
from app.api.deps import CurrentUser, DatasetRepo, DbSession, Limiter, require_role
from app.core.config import get_settings
from app.db.repository import BusinessProfileRepository, ConversationRepository, MessageRepository
from app.models.agent import BusinessProfile
from app.models.user import Role
from app.schemas.agent import (
    BusinessProfileIn,
    BusinessProfileOut,
    ChatRequest,
    ConversationCreate,
    ConversationOut,
    ConversationRename,
    MessageOut,
)

router = APIRouter(tags=["chat"], responses={**responses.UNAUTHORIZED})
# no-transform stops the Next.js proxy's compression, which otherwise holds the whole stream back.
SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}


# -- company profile ------------------------------------------------------------------------------


@router.get(
    "/company/profile", response_model=BusinessProfileOut, summary="Get the company profile"
)
def get_profile(user: CurrentUser, db: DbSession) -> BusinessProfileOut:
    """Return the profile the agent uses (defaults when it was never saved)."""
    profile = BusinessProfileRepository(db, user.tenant_id).current()
    if profile is None:
        return BusinessProfileOut()
    return BusinessProfileOut.model_validate(profile)


@router.put(
    "/company/profile",
    response_model=BusinessProfileOut,
    summary="Save the company profile (owner or admin)",
    responses={**responses.FORBIDDEN},
    dependencies=[Depends(require_role(Role.owner, Role.admin))],
)
def put_profile(body: BusinessProfileIn, user: CurrentUser, db: DbSession) -> BusinessProfileOut:
    """Create or replace the profile; `allow_pii` lets personal data appear in answers."""
    repo = BusinessProfileRepository(db, user.tenant_id)
    profile: BusinessProfile = repo.current() or repo.add()
    for field, value in body.model_dump().items():
        setattr(profile, field, value.upper() if field == "currency" else value)
    db.commit()
    db.refresh(profile)
    return BusinessProfileOut.model_validate(profile)


# -- conversations --------------------------------------------------------------------------------


@router.post(
    "/conversations",
    response_model=ConversationOut,
    status_code=201,
    summary="Start a conversation about a dataset",
    responses={**responses.NOT_FOUND},
)
def create_conversation(
    body: ConversationCreate, user: CurrentUser, db: DbSession, datasets: DatasetRepo
) -> ConversationOut:
    """Create the conversation; the dataset must be the caller's tenant's (404 otherwise)."""
    dataset = datasets.get_or_404(body.dataset_id)
    conversation = ConversationRepository(db, user.tenant_id).add(
        user_id=user.id, dataset_id=dataset.id, title=body.title or "New conversation"
    )
    db.commit()
    db.refresh(conversation)
    return ConversationOut.model_validate(conversation)


@router.get("/conversations", response_model=list[ConversationOut], summary="List conversations")
def list_conversations(
    user: CurrentUser,
    db: DbSession,
    mine: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ConversationOut]:
    """Most recently updated first; `mine=true` lists only the caller's conversations."""
    rows = ConversationRepository(db, user.tenant_id).list_recent(
        user_id=user.id if mine else None, limit=limit, offset=offset
    )
    return [ConversationOut.model_validate(r) for r in rows]


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationOut,
    summary="Get a conversation",
    responses={**responses.NOT_FOUND},
)
def get_conversation(
    conversation_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> ConversationOut:
    """Return one conversation of the caller's tenant."""
    conversation = ConversationRepository(db, user.tenant_id).get_or_404(conversation_id)
    return ConversationOut.model_validate(conversation)


@router.patch(
    "/conversations/{conversation_id}",
    response_model=ConversationOut,
    summary="Rename a conversation (creator, owner or admin)",
    responses={**responses.NOT_FOUND, **responses.FORBIDDEN},
)
def rename_conversation(
    conversation_id: uuid.UUID, body: ConversationRename, user: CurrentUser, db: DbSession
) -> ConversationOut:
    """Change the title."""
    conversation = ConversationRepository(db, user.tenant_id).get_or_404(conversation_id)
    require_manage(user, conversation)
    conversation.title = body.title
    db.commit()
    db.refresh(conversation)
    return ConversationOut.model_validate(conversation)


@router.delete(
    "/conversations/{conversation_id}",
    status_code=204,
    summary="Delete a conversation and its messages (creator, owner or admin)",
    responses={**responses.NOT_FOUND, **responses.FORBIDDEN},
)
def delete_conversation(conversation_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Response:
    """Delete permanently; messages go with it."""
    repo = ConversationRepository(db, user.tenant_id)
    conversation = repo.get_or_404(conversation_id)
    require_manage(user, conversation)
    repo.delete(conversation)
    db.commit()
    return Response(status_code=204)


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[MessageOut],
    summary="List a conversation's messages with tool calls, evidence and costs",
    responses={**responses.NOT_FOUND},
)
def list_messages(
    conversation_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[MessageOut]:
    """Messages in order; model reasoning is never included."""
    conversation = ConversationRepository(db, user.tenant_id).get_or_404(conversation_id)
    rows = MessageRepository(db, user.tenant_id).for_conversation(conversation.id, limit=limit)
    return [
        MessageOut.model_validate(
            {
                **{c: getattr(r, c) for c in MessageOut.model_fields if c != "cost_usd_approx"},
                "cost_usd_approx": message_cost(r),
            }
        )
        for r in rows
    ]


# -- streaming chat -------------------------------------------------------------------------------


@router.post(
    "/chat/stream",
    summary="Ask a question; answers stream as Server-Sent Events",
    responses={
        **responses.NOT_FOUND,
        **responses.RATE_LIMITED,
        200: {
            "content": {"text/event-stream": {}},
            "description": (
                "Events: token, tool_call, tool_result, chart, caveat, status, final, error"
            ),
        },
    },
    response_class=StreamingResponse,
)
def chat_stream(
    body: ChatRequest, user: CurrentUser, db: DbSession, limiter: Limiter
) -> StreamingResponse:
    """Errors found before streaming (auth, limits, unknown conversation) are JSON errors."""
    settings = get_settings()
    ConversationRepository(db, user.tenant_id).get_or_404(body.conversation_id)
    limiter.hit(f"chat:{user.id}", settings.chat_rate_limit_per_minute, 60)
    slots = get_stream_slots()
    token = slots.acquire(user.id, settings.chat_max_concurrent_streams)
    session = ChatSession(user, body.conversation_id, body.message, settings=settings)

    def frames() -> object:
        try:
            yield from session.frames()
        finally:
            slots.release(user.id, token)

    # The slot is released inside the stream's thread: a client that left still holds it until a
    # running tool has finished.
    return StreamingResponse(
        with_heartbeat(frames()),  # type: ignore[arg-type]
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
