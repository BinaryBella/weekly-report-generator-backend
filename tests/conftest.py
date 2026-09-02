"""Shared pytest fixtures - runs the API against an in-memory MongoDB."""

from __future__ import annotations

from typing import AsyncIterator

import pytest
import pytest_asyncio
from beanie import init_beanie
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from app.core.config import settings
from app.main import create_app
from app.models.user import __beanie_models__

app = create_app()


@pytest_asyncio.fixture(autouse=True)
async def _init_db() -> AsyncIterator[None]:
    """Fresh in-memory database bound to Beanie for every test."""
    client = AsyncMongoMockClient()
    await init_beanie(database=client["test_db"], document_models=list(__beanie_models__))
    yield


@pytest.fixture(autouse=True)
def _bootstrap_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``boss@example.com`` the bootstrap Admin for the duration of a test."""
    monkeypatch.setattr(settings, "bootstrap_admin_emails", ["boss@example.com"])


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client


async def register(client: AsyncClient, email: str, password: str = "password123", name: str = "Test User") -> dict:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"name": name, "email": email, "password": password},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def login(client: AsyncClient, email: str, password: str = "password123") -> dict:
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
