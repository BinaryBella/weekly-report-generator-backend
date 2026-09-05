"""Tests for the AI chat assistant endpoints and their role-based access control.

The real OpenAI API is never called - a scripted :class:`FakeOpenAIClient`
stands in for it (via ``app.dependency_overrides``), so these tests exercise
the tool-calling loop, session ownership, and RBAC without any network access
or API key.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.api.v1.chat import get_chat_service
from app.core.config import settings
from app.services.chat_service import ChatService
from tests.conftest import app, auth_header, login, register


# ---------------------------------------------------------------------------
# Fake OpenAI client - scripted, no network access
# ---------------------------------------------------------------------------
class _FakeFunction:
    def __init__(self, name: str, arguments: dict) -> None:
        self.name = name
        self.arguments = json.dumps(arguments)


class _FakeToolCall:
    def __init__(self, call_id: str, name: str, arguments: dict) -> None:
        self.id = call_id
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content: str | None = None, tool_calls: list | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeResponse:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]


def tool_call_response(name: str, arguments: dict, *, call_id: str = "call_1") -> _FakeResponse:
    """Build a fake completion that asks the assistant to call one tool."""
    return _FakeResponse(_FakeMessage(tool_calls=[_FakeToolCall(call_id, name, arguments)]))


def text_response(content: str) -> _FakeResponse:
    """Build a fake completion that answers directly with plain text."""
    return _FakeResponse(_FakeMessage(content=content))


class _FakeCompletions:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def create(self, **kwargs) -> _FakeResponse:
        self.calls.append(kwargs)
        return self._responses.pop(0)


class FakeOpenAIClient:
    """Scripted stand-in for ``openai.AsyncOpenAI``."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.chat = type("_Chat", (), {})()
        self.chat.completions = _FakeCompletions(responses)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _openai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The assistant is only "enabled" when a key is configured."""
    monkeypatch.setattr(settings, "openai_api_key", "test-key")


@pytest_asyncio.fixture
async def _clear_chat_override() -> AsyncIterator[None]:
    yield
    app.dependency_overrides.pop(get_chat_service, None)


def _use_fake_client(responses: list[_FakeResponse]) -> FakeOpenAIClient:
    fake_client = FakeOpenAIClient(responses)
    app.dependency_overrides[get_chat_service] = lambda: ChatService(client=fake_client)
    return fake_client


async def _admin_tokens(client: AsyncClient) -> dict:
    await register(client, "boss@example.com", name="Boss")
    return await login(client, "boss@example.com")


async def _manager_tokens(client: AsyncClient) -> dict:
    admin = await _admin_tokens(client)
    manager = await register(client, "manager@example.com")
    resp = await client.patch(
        f"/api/v1/users/{manager['id']}/role",
        json={"role": "Manager"},
        headers=auth_header(admin["access_token"]),
    )
    assert resp.status_code == 200, resp.text
    return await login(client, "manager@example.com")


async def _member_tokens(client: AsyncClient) -> dict:
    await register(client, "member@example.com", name="Alice")
    return await login(client, "member@example.com")


async def _create_project(client: AsyncClient, manager_token: str, name: str = "Design") -> dict:
    resp = await client.post(
        "/api/v1/projects/",
        json={"name": name, "description": "UI/UX work"},
        headers=auth_header(manager_token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _submit_report(
    client: AsyncClient, member_token: str, project_id: str
) -> dict:
    resp = await client.post(
        "/api/v1/reports/",
        json={
            "project_id": project_id,
            "week_start_date": "2026-08-24",
            "week_end_date": "2026-08-30",
            "tasks_planned_next_week": "Polish the onboarding flow",
            "tasks_completed": [
                {
                    "task_name": "Redesign login screen",
                    "status": "COMPLETED",
                    "time_spent_hours": 12,
                }
            ],
            "blockers": [{"text": "Waiting on brand assets", "is_key_issue": True}],
            "achievements": [{"text": "Shipped new nav", "is_key_achievement": True}],
        },
        headers=auth_header(member_token),
    )
    assert resp.status_code == 201, resp.text
    report = resp.json()
    submit = await client.post(
        f"/api/v1/reports/{report['id']}/submit", headers=auth_header(member_token)
    )
    assert submit.status_code == 200, submit.text
    return submit.json()


async def _create_session(client: AsyncClient, manager_token: str) -> dict:
    resp = await client.post(
        "/api/v1/chat/sessions", json={}, headers=auth_header(manager_token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------
async def test_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/chat/sessions")).status_code == 401


async def test_team_member_cannot_use_chat(client: AsyncClient) -> None:
    member = await _member_tokens(client)
    resp = await client.post(
        "/api/v1/chat/sessions", json={}, headers=auth_header(member["access_token"])
    )
    assert resp.status_code == 403


async def test_manager_cannot_read_another_managers_session(
    client: AsyncClient, _clear_chat_override: None
) -> None:
    manager_a = await _manager_tokens(client)  # registers + logs in "boss" too
    session = await _create_session(client, manager_a["access_token"])

    admin = await login(client, "boss@example.com")  # boss is already a Manager (bootstrap)
    resp = await client.get(
        f"/api/v1/chat/sessions/{session['id']}/messages",
        headers=auth_header(admin["access_token"]),
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Disabled state
# ---------------------------------------------------------------------------
async def test_send_message_503_when_no_api_key(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "openai_api_key", None)
    manager = await _manager_tokens(client)
    session = await _create_session(client, manager["access_token"])

    resp = await client.post(
        f"/api/v1/chat/sessions/{session['id']}/messages",
        json={"content": "What did the team do last week?"},
        headers=auth_header(manager["access_token"]),
    )
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Conversational Q&A (tool-calling loop)
# ---------------------------------------------------------------------------
async def test_send_message_uses_tool_call_and_persists_history(
    client: AsyncClient, _clear_chat_override: None
) -> None:
    manager = await _manager_tokens(client)
    project = await _create_project(client, manager["access_token"])
    member = await _member_tokens(client)
    await _submit_report(client, member["access_token"], project["id"])

    fake_client = _use_fake_client(
        [
            tool_call_response(
                "get_team_activity",
                {
                    "project_name_or_id": "Design",
                    "date_from": "2026-08-24",
                    "date_to": "2026-08-30",
                },
            ),
            text_response(
                "Design shipped a new nav and redesigned the login screen "
                "(week of 2026-08-24 - 2026-08-30). One blocker: waiting on brand assets."
            ),
        ]
    )

    session = await _create_session(client, manager["access_token"])
    resp = await client.post(
        f"/api/v1/chat/sessions/{session['id']}/messages",
        json={"content": "What did the design team work on last week?"},
        headers=auth_header(manager["access_token"]),
    )
    assert resp.status_code == 200, resp.text
    reply = resp.json()
    assert reply["role"] == "assistant"
    assert "brand assets" in reply["content"]

    # Two model calls: one that requested the tool, one with the final answer.
    assert len(fake_client.chat.completions.calls) == 2

    history = await client.get(
        f"/api/v1/chat/sessions/{session['id']}/messages",
        headers=auth_header(manager["access_token"]),
    )
    assert history.status_code == 200
    roles = [m["role"] for m in history.json()["items"]]
    assert roles == ["user", "assistant"]


async def test_send_message_reports_unknown_project(
    client: AsyncClient, _clear_chat_override: None
) -> None:
    manager = await _manager_tokens(client)
    _use_fake_client(
        [
            tool_call_response(
                "get_team_activity",
                {
                    "project_name_or_id": "Nonexistent",
                    "date_from": "2026-08-24",
                    "date_to": "2026-08-30",
                },
            ),
            text_response("I couldn't find a project called Nonexistent."),
        ]
    )

    session = await _create_session(client, manager["access_token"])
    resp = await client.post(
        f"/api/v1/chat/sessions/{session['id']}/messages",
        json={"content": "What did Nonexistent work on?"},
        headers=auth_header(manager["access_token"]),
    )
    assert resp.status_code == 200
    assert "Nonexistent" in resp.json()["content"]


# ---------------------------------------------------------------------------
# AI-generated team summary
# ---------------------------------------------------------------------------
async def test_generate_summary_with_no_reports(
    client: AsyncClient, _clear_chat_override: None
) -> None:
    manager = await _manager_tokens(client)
    _use_fake_client([])  # the model should never be called when there's no data

    resp = await client.post(
        "/api/v1/chat/summary",
        json={"date_from": "2026-01-01", "date_to": "2026-01-07"},
        headers=auth_header(manager["access_token"]),
    )
    assert resp.status_code == 200, resp.text
    assert "No submitted reports" in resp.json()["summary"]


async def test_generate_summary_from_reports(
    client: AsyncClient, _clear_chat_override: None
) -> None:
    manager = await _manager_tokens(client)
    project = await _create_project(client, manager["access_token"])
    member = await _member_tokens(client)
    await _submit_report(client, member["access_token"], project["id"])

    _use_fake_client([text_response("Highlights: shipped a new nav. Blockers: none recurring.")])

    resp = await client.post(
        "/api/v1/chat/summary",
        json={
            "project_name_or_id": "Design",
            "date_from": "2026-08-24",
            "date_to": "2026-08-30",
        },
        headers=auth_header(manager["access_token"]),
    )
    assert resp.status_code == 200, resp.text
    assert "Highlights" in resp.json()["summary"]
