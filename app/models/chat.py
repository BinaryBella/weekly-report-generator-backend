"""Beanie document models for the ``chat_sessions`` and ``chat_messages`` collections.

Backs the AI chat assistant (Manager only). A session is a conversation
thread; each message is one turn in it. Only the final user/assistant text of
each turn is persisted - the intermediate tool-calling round trips used to
answer a question (see :mod:`app.services.chat_service`) are not stored, since
they are always recomputed fresh from the current data on the next turn.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from beanie import Document, Indexed
from pydantic import Field


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class ChatSession(Document):
    """One conversation thread between a Manager and the AI assistant."""

    user_id: Annotated[str, Indexed()]
    title: str = "New chat"
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "chat_sessions"

    def touch(self) -> None:
        """Bump :attr:`updated_at` to the current UTC time."""
        self.updated_at = _utcnow()


class ChatMessage(Document):
    """One turn (user question or assistant reply) within a :class:`ChatSession`."""

    session_id: Annotated[str, Indexed()]
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "chat_messages"


__beanie_models__ = [ChatSession, ChatMessage]
