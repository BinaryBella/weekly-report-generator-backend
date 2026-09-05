"""Pydantic v2 request/response models for the AI chat assistant (Manager only)."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from app.models.chat import ChatMessage, ChatSession


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
class ChatSessionCreateRequest(BaseModel):
    """Payload for ``POST /chat/sessions``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str | None = Field(default=None, max_length=120)


class ChatSessionResponse(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_session(cls, session: "ChatSession") -> "ChatSessionResponse":
        return cls(
            id=str(session.id),
            title=session.title,
            created_at=session.created_at,
            updated_at=session.updated_at,
        )


class ChatSessionListResponse(BaseModel):
    items: list[ChatSessionResponse]


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------
class ChatMessageCreateRequest(BaseModel):
    """Payload for ``POST /chat/sessions/{id}/messages``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    content: str = Field(min_length=1, max_length=4000)


class ChatMessageResponse(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime

    @classmethod
    def from_message(cls, message: "ChatMessage") -> "ChatMessageResponse":
        return cls(
            id=str(message.id),
            role=message.role,
            content=message.content,
            created_at=message.created_at,
        )


class ChatMessageListResponse(BaseModel):
    items: list[ChatMessageResponse]


# ---------------------------------------------------------------------------
# AI-generated team summary
# ---------------------------------------------------------------------------
class TeamSummaryRequest(BaseModel):
    """Payload for ``POST /chat/summary``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    project_name_or_id: str | None = Field(default=None, max_length=120)
    date_from: date
    date_to: date

    @model_validator(mode="after")
    def _check_date_range(self) -> "TeamSummaryRequest":
        if self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        return self


class TeamSummaryResponse(BaseModel):
    summary: str
    project_name_or_id: str | None
    date_from: date
    date_to: date
    generated_at: datetime
