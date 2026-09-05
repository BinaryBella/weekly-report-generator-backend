"""AI chat assistant: OpenAI orchestration for the manager Q&A / summary features.

Two capabilities, both grounded in tool-call results rather than free
generation (see :mod:`app.services.ai_tools`):

* :meth:`ChatService.send_message` - a multi-turn conversation. The model is
  given a small set of read-only data tools and decides which to call to
  answer the manager's question; the loop keeps calling tools until the model
  produces a plain-text reply (or a turn cap is hit).
* :meth:`ChatService.generate_team_summary` - a one-shot summary. Data is
  fetched directly (no tool-calling round trip) and handed to the model in a
  single completion call, since the shape of the answer is fixed.

Data-privacy note: only report content a Manager could already see through
the existing dashboard endpoints is ever sent to OpenAI (private drafts are
excluded by :meth:`~app.services.report_service.ReportService.team_activity_details`),
and no email addresses or credentials are included in any prompt.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone

from openai import AsyncOpenAI, OpenAIError

from app.core.config import settings
from app.models.chat import ChatMessage, ChatSession
from app.models.user import User
from app.repositories.chat_repository import ChatRepository
from app.services.ai_tools import TOOL_DEFINITIONS, resolve_project_id, run_tool
from app.services.report_service import ReportService

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 4
MAX_HISTORY_MESSAGES = 20

SYSTEM_PROMPT_TEMPLATE = """You are an AI assistant embedded in a Weekly Report Generator app, helping an engineering manager understand their team's weekly reports.

Today's date is {today}.

Rules:
- Only state facts returned by your tools. Never invent tasks, blockers, names, dates, or numbers.
- If a tool returns no data for the requested team/person/period, say so plainly instead of guessing.
- When the manager uses a relative period ("last week", "this month"), convert it yourself to explicit YYYY-MM-DD dates before calling a tool. Weeks start on Monday.
- Always mention the date range - and project/team, if narrowed - that your answer covers.
- Keep answers concise and skimmable (short paragraphs or bullet points).
- You only have access to reports that have been submitted for review; a team member's private drafts are never available to you, the same as everywhere else in this app.
"""

SUMMARY_PROMPT_TEMPLATE = """Summarise the team's activity between {date_from} and {date_to}{project_clause} for a manager, using only the report data below (JSON). Produce three short sections:

1. Completed work highlights
2. Recurring blockers (only mention one if it appears for more than one person, or more than once)
3. Workload imbalance (compare time_spent_hours across people; only call it out if there's a clear gap)

If a section has nothing notable, say so briefly rather than inventing content. Keep the whole summary under 200 words.

Report data:
{reports_json}
"""


class ChatServiceError(Exception):
    """Base class for chat domain errors."""


class ChatDisabledError(ChatServiceError):
    """Raised when no OpenAI key is configured."""

    def __init__(self) -> None:
        super().__init__(
            "The AI chat assistant is not configured (OPENAI_API_KEY is unset)."
        )


class ChatSessionNotFoundError(ChatServiceError):
    """Raised when a chat session id does not resolve to a stored document."""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"Chat session '{session_id}' was not found")
        self.session_id = session_id


class ChatAccessDeniedError(ChatServiceError):
    """Raised when the caller does not own the chat session."""

    def __init__(self, session_id: str) -> None:
        super().__init__("You do not have access to this chat session")
        self.session_id = session_id


class ChatProviderError(ChatServiceError):
    """Raised when the upstream OpenAI API call fails."""


def _system_prompt() -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(today=date.today().isoformat())


def _safe_json_loads(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


class ChatService:
    """Coordinates conversation state, tool-calling, and OpenAI calls."""

    def __init__(
        self,
        chat_repo: ChatRepository | None = None,
        report_service: ReportService | None = None,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._repo = chat_repo or ChatRepository()
        self._reports = report_service or ReportService()
        self._client = client

    def _get_client(self) -> AsyncOpenAI:
        if not settings.ai_chat_enabled:
            raise ChatDisabledError()
        if self._client is None:
            self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        return self._client

    # -- Sessions -------------------------------------------------------------
    async def create_session(self, user: User, *, title: str | None = None) -> ChatSession:
        session = ChatSession(user_id=str(user.id), title=title or "New chat")
        return await self._repo.add_session(session)

    async def list_sessions(self, user: User) -> list[ChatSession]:
        return await self._repo.list_sessions(str(user.id))

    async def get_messages(self, user: User, session_id: str) -> list[ChatMessage]:
        session = await self._load_owned_session(user, session_id)
        return await self._repo.list_messages(str(session.id))

    # -- Conversational Q&A ---------------------------------------------------
    async def send_message(
        self, user: User, session_id: str, content: str
    ) -> ChatMessage:
        """Answer *content* within *session_id*, persisting both turns.

        Raises:
            ChatDisabledError: if no OpenAI key is configured.
            ChatSessionNotFoundError: if the session id is unknown.
            ChatAccessDeniedError: if the caller does not own the session.
            ChatProviderError: if the OpenAI API call fails.
        """
        client = self._get_client()
        session = await self._load_owned_session(user, session_id)

        history = await self._repo.list_messages(
            str(session.id), limit=MAX_HISTORY_MESSAGES
        )
        await self._repo.add_message(
            ChatMessage(session_id=str(session.id), role="user", content=content)
        )

        messages: list[dict] = [{"role": "system", "content": _system_prompt()}]
        messages += [{"role": m.role, "content": m.content} for m in history]
        messages.append({"role": "user", "content": content})

        reply_text = await self._run_tool_loop(client, messages)

        session.touch()
        await self._repo.save_session(session)
        return await self._repo.add_message(
            ChatMessage(session_id=str(session.id), role="assistant", content=reply_text)
        )

    async def _run_tool_loop(self, client: AsyncOpenAI, messages: list[dict]) -> str:
        for _ in range(MAX_TOOL_ITERATIONS):
            try:
                response = await client.chat.completions.create(
                    model=settings.openai_model,
                    messages=messages,
                    tools=TOOL_DEFINITIONS,
                    tool_choice="auto",
                    max_tokens=settings.openai_max_output_tokens,
                )
            except OpenAIError as exc:
                logger.warning("OpenAI chat completion failed: %s", exc)
                raise ChatProviderError(str(exc)) from exc

            choice = response.choices[0].message
            tool_calls = choice.tool_calls or []
            if not tool_calls:
                return choice.content or ""

            messages.append(
                {
                    "role": "assistant",
                    "content": choice.content,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.function.name,
                                "arguments": call.function.arguments,
                            },
                        }
                        for call in tool_calls
                    ],
                }
            )
            for call in tool_calls:
                args = _safe_json_loads(call.function.arguments)
                result = await run_tool(call.function.name, args, reports=self._reports)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result, default=str),
                    }
                )

        return (
            "I wasn't able to gather the data for that within the allowed number "
            "of steps - try narrowing the date range or team."
        )

    # -- AI-generated team summary ---------------------------------------------
    async def generate_team_summary(
        self,
        *,
        project_name_or_id: str | None,
        date_from: date,
        date_to: date,
    ) -> str:
        """One-shot summary: completed work, recurring blockers, workload imbalance.

        Raises:
            ChatDisabledError: if no OpenAI key is configured.
            ChatProviderError: if the OpenAI API call fails.
        """
        client = self._get_client()

        project_id = (
            await resolve_project_id(project_name_or_id) if project_name_or_id else None
        )

        reports = await self._reports.team_activity_details(
            project_id=project_id, date_from=date_from, date_to=date_to
        )
        if not reports:
            return "No submitted reports were found for that period."

        prompt = SUMMARY_PROMPT_TEMPLATE.format(
            date_from=date_from.isoformat(),
            date_to=date_to.isoformat(),
            project_clause=f" for {project_name_or_id}" if project_name_or_id else "",
            reports_json=json.dumps(reports, default=str),
        )
        try:
            response = await client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": _system_prompt()},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=settings.openai_max_output_tokens,
            )
        except OpenAIError as exc:
            logger.warning("OpenAI summary completion failed: %s", exc)
            raise ChatProviderError(str(exc)) from exc
        return response.choices[0].message.content or ""

    # -- Internals --------------------------------------------------------------
    async def _load_owned_session(self, user: User, session_id: str) -> ChatSession:
        session = await self._repo.get_session(session_id)
        if session is None:
            raise ChatSessionNotFoundError(session_id)
        if session.user_id != str(user.id):
            raise ChatAccessDeniedError(session_id)
        return session
