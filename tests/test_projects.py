"""Tests for the project (category) endpoints and their role-based access control."""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import auth_header, login, register


async def _admin_tokens(client: AsyncClient) -> dict:
    """Register the bootstrap admin (``boss@example.com``) and log in."""
    await register(client, "boss@example.com", name="Boss")
    return await login(client, "boss@example.com")


async def _manager_tokens(client: AsyncClient) -> dict:
    """Create a user, have the admin promote them to Manager, then log in."""
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
    await register(client, "member@example.com")
    return await login(client, "member@example.com")


async def _create_project(client: AsyncClient, token: str, **body) -> dict:
    payload = {"name": "Apollo", "description": "Billing rebuild"}
    payload.update(body)
    resp = await client.post(
        "/api/v1/projects/", json=payload, headers=auth_header(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Read access - every authenticated role
# ---------------------------------------------------------------------------
async def test_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/projects/")).status_code == 401


async def test_team_member_can_list_and_get_projects(client: AsyncClient) -> None:
    admin = await _admin_tokens(client)
    created = await _create_project(client, admin["access_token"])
    member = await _member_tokens(client)
    headers = auth_header(member["access_token"])

    listed = await client.get("/api/v1/projects/", headers=headers)
    assert listed.status_code == 200
    assert [p["name"] for p in listed.json()] == ["Apollo"]

    single = await client.get(f"/api/v1/projects/{created['id']}", headers=headers)
    assert single.status_code == 200
    assert single.json()["id"] == created["id"]


async def test_active_only_filter(client: AsyncClient) -> None:
    admin = await _admin_tokens(client)
    token = admin["access_token"]
    active = await _create_project(client, token, name="Active")
    archived = await _create_project(client, token, name="Archived")
    await client.put(
        f"/api/v1/projects/{archived['id']}",
        json={"is_active": False},
        headers=auth_header(token),
    )

    resp = await client.get(
        "/api/v1/projects/", params={"active_only": "true"}, headers=auth_header(token)
    )
    assert resp.status_code == 200
    names = {p["name"] for p in resp.json()}
    assert names == {"Active"}
    assert active  # created successfully


# ---------------------------------------------------------------------------
# Write access - Manager only
# ---------------------------------------------------------------------------
async def test_manager_can_create_project(client: AsyncClient) -> None:
    manager = await _manager_tokens(client)
    body = await _create_project(client, manager["access_token"], name="Zephyr")
    assert body["name"] == "Zephyr"
    assert body["is_active"] is True
    assert body["member_ids"] == []
    assert body["created_at"] and body["updated_at"]


async def test_team_member_forbidden_from_writes(client: AsyncClient) -> None:
    admin = await _admin_tokens(client)
    created = await _create_project(client, admin["access_token"])
    member = await _member_tokens(client)
    headers = auth_header(member["access_token"])

    post = await client.post(
        "/api/v1/projects/", json={"name": "Nope"}, headers=headers
    )
    assert post.status_code == 403

    put = await client.put(
        f"/api/v1/projects/{created['id']}",
        json={"name": "Renamed"},
        headers=headers,
    )
    assert put.status_code == 403

    delete = await client.delete(
        f"/api/v1/projects/{created['id']}", headers=headers
    )
    assert delete.status_code == 403


# ---------------------------------------------------------------------------
# Validation & error handling
# ---------------------------------------------------------------------------
async def test_duplicate_name_returns_400(client: AsyncClient) -> None:
    admin = await _admin_tokens(client)
    token = admin["access_token"]
    await _create_project(client, token, name="Orion")

    resp = await client.post(
        "/api/v1/projects/", json={"name": "Orion"}, headers=auth_header(token)
    )
    assert resp.status_code == 400
    assert "already exists" in resp.json()["detail"]


async def test_validation_rejects_bad_payloads(client: AsyncClient) -> None:
    admin = await _admin_tokens(client)
    headers = auth_header(admin["access_token"])

    for body in (
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 101},
        {"name": "ok", "description": "d" * 501},
    ):
        resp = await client.post("/api/v1/projects/", json=body, headers=headers)
        assert resp.status_code == 422, body


async def test_unknown_and_malformed_ids_return_404(client: AsyncClient) -> None:
    admin = await _admin_tokens(client)
    headers = auth_header(admin["access_token"])

    for pid in ("000000000000000000000000", "not-an-object-id"):
        assert (
            await client.get(f"/api/v1/projects/{pid}", headers=headers)
        ).status_code == 404
        assert (
            await client.put(
                f"/api/v1/projects/{pid}", json={"name": "x"}, headers=headers
            )
        ).status_code == 404
        assert (
            await client.delete(f"/api/v1/projects/{pid}", headers=headers)
        ).status_code == 404


# ---------------------------------------------------------------------------
# Update & delete behaviour
# ---------------------------------------------------------------------------
async def test_update_changes_fields_and_bumps_timestamp(client: AsyncClient) -> None:
    admin = await _admin_tokens(client)
    token = admin["access_token"]
    created = await _create_project(client, token, name="Draft", description="old")

    resp = await client.put(
        f"/api/v1/projects/{created['id']}",
        json={"name": "Final", "description": "new"},
        headers=auth_header(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Final"
    assert body["description"] == "new"
    assert body["updated_at"] >= created["updated_at"]


async def test_update_to_existing_name_returns_400(client: AsyncClient) -> None:
    admin = await _admin_tokens(client)
    token = admin["access_token"]
    await _create_project(client, token, name="Alpha")
    beta = await _create_project(client, token, name="Beta")

    resp = await client.put(
        f"/api/v1/projects/{beta['id']}",
        json={"name": "Alpha"},
        headers=auth_header(token),
    )
    assert resp.status_code == 400


async def test_delete_hard_removes_unreferenced_project(client: AsyncClient) -> None:
    admin = await _admin_tokens(client)
    token = admin["access_token"]
    created = await _create_project(client, token, name="Temp")

    resp = await client.delete(
        f"/api/v1/projects/{created['id']}", headers=auth_header(token)
    )
    assert resp.status_code == 200
    assert resp.json()["soft_deleted"] is False

    gone = await client.get(
        f"/api/v1/projects/{created['id']}", headers=auth_header(token)
    )
    assert gone.status_code == 404


# ---------------------------------------------------------------------------
# Team member assignment
# ---------------------------------------------------------------------------
async def test_manager_can_assign_members(client: AsyncClient) -> None:
    admin = await _admin_tokens(client)
    token = admin["access_token"]
    project = await _create_project(client, token)
    alice = await register(client, "alice@example.com")
    bob = await register(client, "bob@example.com")

    resp = await client.put(
        f"/api/v1/projects/{project['id']}/members",
        json={"member_ids": [alice["id"], bob["id"]]},
        headers=auth_header(token),
    )
    assert resp.status_code == 200, resp.text
    assert set(resp.json()["member_ids"]) == {alice["id"], bob["id"]}


async def test_assign_members_replaces_full_list(client: AsyncClient) -> None:
    admin = await _admin_tokens(client)
    token = admin["access_token"]
    project = await _create_project(client, token)
    alice = await register(client, "alice@example.com")
    bob = await register(client, "bob@example.com")

    await client.put(
        f"/api/v1/projects/{project['id']}/members",
        json={"member_ids": [alice["id"]]},
        headers=auth_header(token),
    )
    resp = await client.put(
        f"/api/v1/projects/{project['id']}/members",
        json={"member_ids": [bob["id"]]},
        headers=auth_header(token),
    )
    assert resp.status_code == 200
    assert resp.json()["member_ids"] == [bob["id"]]


async def test_assign_members_dedupes(client: AsyncClient) -> None:
    admin = await _admin_tokens(client)
    token = admin["access_token"]
    project = await _create_project(client, token)
    alice = await register(client, "alice@example.com")

    resp = await client.put(
        f"/api/v1/projects/{project['id']}/members",
        json={"member_ids": [alice["id"], alice["id"]]},
        headers=auth_header(token),
    )
    assert resp.status_code == 200
    assert resp.json()["member_ids"] == [alice["id"]]


async def test_assign_unknown_user_id_returns_400(client: AsyncClient) -> None:
    admin = await _admin_tokens(client)
    token = admin["access_token"]
    project = await _create_project(client, token)

    resp = await client.put(
        f"/api/v1/projects/{project['id']}/members",
        json={"member_ids": ["000000000000000000000000"]},
        headers=auth_header(token),
    )
    assert resp.status_code == 400


async def test_team_member_cannot_assign_members(client: AsyncClient) -> None:
    admin = await _admin_tokens(client)
    token = admin["access_token"]
    project = await _create_project(client, token)
    member = await _member_tokens(client)

    resp = await client.put(
        f"/api/v1/projects/{project['id']}/members",
        json={"member_ids": []},
        headers=auth_header(member["access_token"]),
    )
    assert resp.status_code == 403


async def test_assign_members_on_unknown_project_returns_404(client: AsyncClient) -> None:
    admin = await _admin_tokens(client)

    resp = await client.put(
        "/api/v1/projects/000000000000000000000000/members",
        json={"member_ids": []},
        headers=auth_header(admin["access_token"]),
    )
    assert resp.status_code == 404
