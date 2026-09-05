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


async def test_manager_can_update_roles(client: AsyncClient) -> None:
    # Manager is the privileged ("admin") role, so any Manager may reassign roles.
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
        json={"role": "Manager"},
        headers=auth_header(mgr_tokens["access_token"]),
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "Manager"


async def test_team_member_cannot_update_roles(client: AsyncClient) -> None:
    admin = await _bootstrap_admin_tokens(client)
    await register(client, "member@example.com")
    victim = await register(client, "victim@example.com")
    member_tokens = await login(client, "member@example.com")

    resp = await client.patch(
        f"/api/v1/users/{victim['id']}/role",
        json={"role": "Manager"},
        headers=auth_header(member_tokens["access_token"]),
    )
    assert resp.status_code == 403


async def test_manager_cannot_change_own_role(client: AsyncClient) -> None:
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


async def test_create_user_allowed_for_manager_forbidden_for_team_member(
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

    # Manager is the privileged role, so it may invite users.
    resp = await client.post(
        "/api/v1/users/",
        json={"name": "X", "email": "x@example.com"},
        headers=auth_header(manager_tokens["access_token"]),
    )
    assert resp.status_code == 201, resp.text

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


# ---------------------------------------------------------------------------
# DELETE /users/{id} - "remove" a team member
# ---------------------------------------------------------------------------
async def test_team_member_cannot_remove_users(client: AsyncClient) -> None:
    await _bootstrap_admin_tokens(client)
    victim = await register(client, "victim@example.com")
    await register(client, "member@example.com")
    member_tokens = await login(client, "member@example.com")

    resp = await client.delete(
        f"/api/v1/users/{victim['id']}", headers=auth_header(member_tokens["access_token"])
    )
    assert resp.status_code == 403


async def test_manager_hard_deletes_user_with_no_history(client: AsyncClient) -> None:
    admin = await _bootstrap_admin_tokens(client)
    victim = await register(client, "victim@example.com")

    resp = await client.delete(
        f"/api/v1/users/{victim['id']}", headers=auth_header(admin["access_token"])
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["soft_deleted"] is False
    assert body["user"] is None

    # Really gone, and the email is free to re-register.
    assert (
        await client.get(f"/api/v1/users/{victim['id']}", headers=auth_header(admin["access_token"]))
    ).status_code == 404
    again = await client.post(
        "/api/v1/auth/register",
        json={"name": "Victim", "email": "victim@example.com", "password": "password123"},
    )
    assert again.status_code == 201


async def test_manager_soft_deletes_user_with_project_membership(client: AsyncClient) -> None:
    admin = await _bootstrap_admin_tokens(client)
    member = await register(client, "member@example.com")

    project_resp = await client.post(
        "/api/v1/projects/",
        json={"name": "Apollo", "description": "Test project"},
        headers=auth_header(admin["access_token"]),
    )
    assert project_resp.status_code == 201, project_resp.text
    project_id = project_resp.json()["id"]

    assign_resp = await client.put(
        f"/api/v1/projects/{project_id}/members",
        json={"member_ids": [member["id"]]},
        headers=auth_header(admin["access_token"]),
    )
    assert assign_resp.status_code == 200, assign_resp.text

    resp = await client.delete(
        f"/api/v1/users/{member['id']}", headers=auth_header(admin["access_token"])
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["soft_deleted"] is True
    assert body["user"]["status"] == "disabled"

    # The record survives, just disabled - the project assignment is untouched.
    get_resp = await client.get(
        f"/api/v1/users/{member['id']}", headers=auth_header(admin["access_token"])
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["status"] == "disabled"


async def test_manager_cannot_remove_own_account(client: AsyncClient) -> None:
    admin = await _bootstrap_admin_tokens(client)
    me = await client.get("/api/v1/auth/me", headers=auth_header(admin["access_token"]))
    admin_id = me.json()["id"]

    resp = await client.delete(
        f"/api/v1/users/{admin_id}", headers=auth_header(admin["access_token"])
    )
    assert resp.status_code == 400


async def test_manager_can_remove_another_manager(client: AsyncClient) -> None:
    # Manager is the privileged role, so any Manager may remove any other user,
    # including another Manager - the caller themselves stays active, so the
    # system is never left without an active Manager.
    admin = await _bootstrap_admin_tokens(client)
    other = await register(client, "manager2@example.com")
    await client.patch(
        f"/api/v1/users/{other['id']}/role",
        json={"role": "Manager"},
        headers=auth_header(admin["access_token"]),
    )

    resp = await client.delete(
        f"/api/v1/users/{other['id']}", headers=auth_header(admin["access_token"])
    )
    assert resp.status_code == 200
    assert resp.json()["soft_deleted"] is False


async def test_removing_unknown_user_returns_404(client: AsyncClient) -> None:
    admin = await _bootstrap_admin_tokens(client)
    resp = await client.delete(
        "/api/v1/users/000000000000000000000000",
        headers=auth_header(admin["access_token"]),
    )
    assert resp.status_code == 404
