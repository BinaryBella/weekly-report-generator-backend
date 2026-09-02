"""Tests for the authentication endpoints."""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import auth_header, login, register


async def test_register_defaults_to_team_member(client: AsyncClient) -> None:
    body = await register(client, "member@example.com")
    assert body["role"] == "Team Member"
    assert body["status"] == "active"
    assert "hashed_password" not in body


async def test_register_bootstrap_email_becomes_admin(client: AsyncClient) -> None:
    body = await register(client, "boss@example.com")
    assert body["role"] == "Admin"


async def test_register_duplicate_email_returns_400(client: AsyncClient) -> None:
    await register(client, "dup@example.com")
    resp = await client.post(
        "/api/v1/auth/register",
        json={"name": "Dup", "email": "dup@example.com", "password": "password123"},
    )
    assert resp.status_code == 400


async def test_login_bad_password_returns_401(client: AsyncClient) -> None:
    await register(client, "user@example.com")
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": "user@example.com", "password": "wrong"},
    )
    assert resp.status_code == 401


async def test_me_returns_current_user(client: AsyncClient) -> None:
    await register(client, "user@example.com")
    tokens = await login(client, "user@example.com")
    resp = await client.get("/api/v1/auth/me", headers=auth_header(tokens["access_token"]))
    assert resp.status_code == 200
    assert resp.json()["email"] == "user@example.com"


async def test_me_without_token_returns_401(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


async def test_logout_revokes_access_token(client: AsyncClient) -> None:
    await register(client, "user@example.com")
    tokens = await login(client, "user@example.com")
    headers = auth_header(tokens["access_token"])

    assert (await client.post("/api/v1/auth/logout", headers=headers)).status_code == 200
    # The revoked token can no longer be used anywhere...
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401
    # ...including a second logout attempt with the same token.
    assert (await client.post("/api/v1/auth/logout", headers=headers)).status_code == 401


async def test_refresh_issues_new_access_token(client: AsyncClient) -> None:
    await register(client, "user@example.com")
    tokens = await login(client, "user@example.com")

    resp = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert resp.status_code == 200
    new_access = resp.json()["access_token"]
    assert (
        await client.get("/api/v1/auth/me", headers=auth_header(new_access))
    ).status_code == 200


async def test_refresh_with_access_token_is_rejected(client: AsyncClient) -> None:
    await register(client, "user@example.com")
    tokens = await login(client, "user@example.com")
    resp = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["access_token"]},
    )
    assert resp.status_code == 401
