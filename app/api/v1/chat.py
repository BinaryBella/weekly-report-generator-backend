"""AI chat assistant endpoints (Manager only) - Good to Have.

Two capabilities, both grounded in real report data via OpenAI function
calling over the existing report/project services (see
:mod:`app.services.ai_tools`), never free generation:

* ``POST /chat/sessions/{id}/messages`` - conversational Q&A about team
  activity ("what did the design team work on last week").
* ``POST /chat/summary`` - a one-shot AI-generated team summary (completed
  work, recurring blockers, workload imbalance) for a date range.

Access is Manager-only, consistent with the rest of the manager dashboard - a
chat reply can only ever surface data a Manager could already see through the
existing dashboard endpoints (a private DRAFT report is never included).

The controller stays thin: it delegates to
:class:`~app.services.chat_service.ChatService` and lets the domain errors
raised there be turned into ``403`` / ``404`` / ``502`` / ``503`` responses by
the exception handlers registered in :mod:`app.main`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import require_roles
from app.models.user import Role, User
from app.schemas.chat import (
    ChatMessageCreateRequest,
    ChatMessageListResponse,
    ChatMessageResponse,
    ChatSessionCreateRequest,
    ChatSessionListResponse,
    ChatSessionResponse,
    TeamSummaryRequest,
    TeamSummaryResponse,
)
from app.services.chat_service import ChatService

router = APIRouter(prefix="/chat", tags=["chat"])

ManagerOnly = Annotated[User, Depends(require_roles(Role.MANAGER))]


def get_chat_service() -> ChatService:
    """FastAPI dependency provider - swappable in tests."""
    return ChatService()


ChatSvc = Annotated[ChatService, Depends(get_chat_service)]


@router.post(
    "/sessions",
    response_model=ChatSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a new chat session (Manager only)",
)
async def create_session(
    data: ChatSessionCreateRequest, user: ManagerOnly, service: ChatSvc
) -> ChatSessionResponse:
    session = await service.create_session(user, title=data.title)
    return ChatSessionResponse.from_session(session)


@router.get(
    "/sessions",
    response_model=ChatSessionListResponse,
    summary="List the caller's chat sessions, most recently active first (Manager only)",
)
async def list_sessions(user: ManagerOnly, service: ChatSvc) -> ChatSessionListResponse:
    sessions = await service.list_sessions(user)
    return ChatSessionListResponse(
        items=[ChatSessionResponse.from_session(s) for s in sessions]
    )


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a chat session and its messages (Manager only, owner only)",
)
async def delete_session(
    session_id: str, user: ManagerOnly, service: ChatSvc
) -> None:
    await service.delete_session(user, session_id)


@router.get(
    "/sessions/{session_id}/messages",
    response_model=ChatMessageListResponse,
    summary="Get a chat session's message history (Manager only, owner only)",
)
async def list_messages(
    session_id: str, user: ManagerOnly, service: ChatSvc
) -> ChatMessageListResponse:
    messages = await service.get_messages(user, session_id)
    return ChatMessageListResponse(
        items=[ChatMessageResponse.from_message(m) for m in messages]
    )


@router.post(
    "/sessions/{session_id}/messages",
    response_model=ChatMessageResponse,
    summary="Send a message and get the assistant's reply (Manager only, owner only)",
)
async def send_message(
    session_id: str,
    data: ChatMessageCreateRequest,
    user: ManagerOnly,
    service: ChatSvc,
) -> ChatMessageResponse:
    reply = await service.send_message(user, session_id, data.content)
    return ChatMessageResponse.from_message(reply)


@router.post(
    "/summary",
    response_model=TeamSummaryResponse,
    summary="Generate an AI team-activity summary for a date range (Manager only)",
)
async def generate_summary(
    data: TeamSummaryRequest, _: ManagerOnly, service: ChatSvc
) -> TeamSummaryResponse:
    summary = await service.generate_team_summary(
        project_name_or_id=data.project_name_or_id,
        date_from=data.date_from,
        date_to=data.date_to,
    )
    return TeamSummaryResponse(
        summary=summary,
        project_name_or_id=data.project_name_or_id,
        date_from=data.date_from,
        date_to=data.date_to,
        generated_at=datetime.now(tz=timezone.utc),
    )
