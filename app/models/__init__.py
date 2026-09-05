"""Central registry of Beanie document models.

Anything that calls :func:`beanie.init_beanie` (the app's DB session and the test
suite) should bind :data:`document_models` so that every collection is
registered in exactly one place.
"""

from __future__ import annotations

from app.models.chat import ChatMessage, ChatSession
from app.models.project import Project
from app.models.report import Report
from app.models.user import RevokedToken, User

document_models: list[type] = [
    User,
    RevokedToken,
    Project,
    Report,
    ChatSession,
    ChatMessage,
]

__all__ = [
    "User",
    "RevokedToken",
    "Project",
    "Report",
    "ChatSession",
    "ChatMessage",
    "document_models",
]
