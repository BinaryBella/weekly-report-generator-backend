"""Tests for user-management endpoints and role-based access control."""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import auth_header, login, register


async def _bootstrap_admin_tokens(client: AsyncClient) -> dict:
    await register(client, "boss@example.com", name="Boss")
    return await login(client, "boss@example.com")


async def test_team_member_cannot_list_users(client: AsyncClient) -> None:
    await register(client, "member@example.com")
    tokens = await login(client, "member@example.com")
    resp = await client.get("/api/v1/users/", headers=auth_header(tokens["access_token"]))
    assert resp.status_code == 403


async def test_admin_can_list_users(client: AsyncClient) -> None:
    admin = await _bootstrap_admin_tokens(client)
    await register(client, "member@example.com")

    resp = await client.get("/api/v1/users/", headers=auth_header(admin["access_token"]))
    assert resp.status_code == 200
    emails = {u["email"] for u in resp.json()}
    assert {"boss@example.com", "member@example.com"} <= emails


async def test_team_member_can_read_own_profile_only(client: AsyncClient) -> None:
    me = await register(client, "member@example.com")
    other = await register(client, "other@example.com")
    tokens = await login(client, "member@example.com")
    headers = auth_header(tokens["access_token"])

    assert (await client.get(f"/api/v1/users/{me['id']}", headers=headers)).status_code == 200
    assert (await client.get(f"/api/v1/users/{other['id']}", headers=headers)).status_code == 403


async def test_unknown_user_id_returns_404(client: AsyncClient) -> None:
    admin = await _bootstrap_admin_tokens(client)
    resp = await client.get(
        "/api/v1/users/000000000000000000000000",
        headers=auth_header(admin["access_token"]),
    )
    assert resp.status_code == 404


async def test_admin_updates_role_and_access_expands(client: AsyncClient) -> None:
    admin = await _bootstrap_admin_tokens(client)
    member = await register(client, "member@example.com")

    resp = await client.patch(
        f"/api/v1/users/{member['id']}/role",
        json={"role": "Manager"},
        headers=auth_header(admin["access_token"]),
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "Manager"

    # The promoted user can now list users.
    promoted = await login(client, "member@example.com")
    listed = await client.get(
        "/api/v1/users/", headers=auth_header(promoted["access_token"])
    )
    assert listed.status_code == 200


async def test_manager_cannot_update_roles(client: AsyncClient) -> None:
    admin = await _bootstrap_admin_tokens(client)
    manager = await register(client, "manager@example.com")
    await client.patch(
        f"/api/v1/users/{manager['id']}/role",
        json={"role": "Manager"},
        headers=auth_header(admin["access_token"]),
    )
    victim = await register(client, "victim@example.com")
    mgr_tokens = await login(client, "manager@example.com")

    resp = await client.patch(
        f"/api/v1/users/{victim['id']}/role",
        json={"role": "Admin"},
        headers=auth_header(mgr_tokens["access_token"]),
    )
    assert resp.status_code == 403


async def test_admin_cannot_change_own_role(client: AsyncClient) -> None:
    admin = await _bootstrap_admin_tokens(client)
    me = await client.get("/api/v1/auth/me", headers=auth_header(admin["access_token"]))
    admin_id = me.json()["id"]

    resp = await client.patch(
        f"/api/v1/users/{admin_id}/role",
        json={"role": "Team Member"},
        headers=auth_header(admin["access_token"]),
    )
    assert resp.status_code == 400


async def test_admin_can_create_invite_user_with_generated_password(
    client: AsyncClient,
) -> None:
    admin = await _bootstrap_admin_tokens(client)
    resp = await client.post(
        "/api/v1/users/",
        json={"name": "New Hire", "email": "newhire@example.com", "role": "Manager"},
        headers=auth_header(admin["access_token"]),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["user"]["email"] == "newhire@example.com"
    assert body["user"]["role"] == "Manager"
    assert body["user"]["status"] == "active"
    temp_password = body["temporary_password"]
    assert temp_password and len(temp_password) >= 8

    # The generated password actually logs the new user in.
    login_resp = await client.post(
        "/api/v1/auth/login",
        data={"username": "newhire@example.com", "password": temp_password},
    )
    assert login_resp.status_code == 200


async def test_admin_can_create_user_with_own_password_no_temp_returned(
    client: AsyncClient,
) -> None:
    admin = await _bootstrap_admin_tokens(client)
    resp = await client.post(
        "/api/v1/users/",
        json={
            "name": "Set Password",
            "email": "setpw@example.com",
            "password": "a-strong-password",
        },
        headers=auth_header(admin["access_token"]),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["temporary_password"] is None
    assert resp.json()["user"]["role"] == "Team Member"  # default role


async def test_create_user_rejects_duplicate_email(client: AsyncClient) -> None:
    admin = await _bootstrap_admin_tokens(client)
    await register(client, "dup@example.com")

    resp = await client.post(
        "/api/v1/users/",
        json={"name": "Dup", "email": "dup@example.com"},
        headers=auth_header(admin["access_token"]),
    )
    assert resp.status_code == 400


async def test_create_user_forbidden_for_manager_and_team_member(
    client: AsyncClient,
) -> None:
    admin = await _bootstrap_admin_tokens(client)
    manager = await register(client, "manager@example.com")
    await client.patch(
        f"/api/v1/users/{manager['id']}/role",
        json={"role": "Manager"},
        headers=auth_header(admin["access_token"]),
    )
    manager_tokens = await login(client, "manager@example.com")

    for tokens in (manager_tokens,):
        resp = await client.post(
            "/api/v1/users/",
            json={"name": "X", "email": "x@example.com"},
            headers=auth_header(tokens["access_token"]),
        )
        assert resp.status_code == 403

    member = await register(client, "member2@example.com")
    member_tokens = await login(client, "member2@example.com")
    resp = await client.post(
        "/api/v1/users/",
        json={"name": "Y", "email": "y@example.com"},
        headers=auth_header(member_tokens["access_token"]),
    )
    assert resp.status_code == 403


async def test_disabled_user_cannot_authenticate(client: AsyncClient) -> None:
    admin = await _bootstrap_admin_tokens(client)
    member = await register(client, "member@example.com")
    tokens = await login(client, "member@example.com")

    resp = await client.patch(
        f"/api/v1/users/{member['id']}/status",
        json={"status": "disabled"},
        headers=auth_header(admin["access_token"]),
    )
    assert resp.status_code == 200

    # Existing access token is now rejected, and re-login is blocked.
    assert (
        await client.get("/api/v1/auth/me", headers=auth_header(tokens["access_token"]))
    ).status_code == 403
    relogin = await client.post(
        "/api/v1/auth/login",
        data={"username": "member@example.com", "password": "password123"},
    )
    assert relogin.status_code == 403
