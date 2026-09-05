"""Data-access layer for the ``chat_sessions`` and ``chat_messages`` collections.

This module is the only place that talks to Beanie/Mongo for the AI chat
assistant; the service layer depends on it through the small interface below
so the business rules stay storage-agnostic and unit-testable.
"""

from __future__ import annotations

from bson.errors import InvalidId

from app.models.chat import ChatMessage, ChatSession


class ChatRepository:
    """Thin CRUD wrapper around the chat session / message documents."""

    async def add_session(self, session: ChatSession) -> ChatSession:
        await session.insert()
        return session

    async def save_session(self, session: ChatSession) -> ChatSession:
        await session.save()
        return session

    async def get_session(self, session_id: str) -> ChatSession | None:
        """Fetch a session by id. Returns ``None`` for unknown or malformed ids."""
        try:
            return await ChatSession.get(session_id)
        except (InvalidId, ValueError):
            return None

    async def delete_session(self, session: ChatSession) -> None:
        """Remove a session and every message in it."""
        await ChatMessage.find(ChatMessage.session_id == str(session.id)).delete()
        await session.delete()

    async def list_sessions(self, user_id: str) -> list[ChatSession]:
        """Return a user's chat sessions, most recently active first."""
        return (
            await ChatSession.find(ChatSession.user_id == user_id)
            .sort("-updated_at")
            .to_list()
        )

    async def add_message(self, message: ChatMessage) -> ChatMessage:
        await message.insert()
        return message

    async def list_messages(
        self, session_id: str, *, limit: int | None = None
    ) -> list[ChatMessage]:
        """Return a session's messages, oldest first.

        When *limit* is given, only the most recent *limit* messages are
        returned (still oldest-first) - used to bound how much history is fed
        back to the model on each turn.
        """
        messages = (
            await ChatMessage.find(ChatMessage.session_id == session_id)
            .sort("+created_at")
            .to_list()
        )
        if limit is not None and len(messages) > limit:
            return messages[-limit:]
        return messages
