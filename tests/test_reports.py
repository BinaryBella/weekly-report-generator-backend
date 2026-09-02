"""Tests for the personal weekly report endpoints, validation and RBAC."""

from __future__ import annotations

from httpx import AsyncClient

from app.models.report import Report, ReportStatus
from tests.conftest import auth_header, login, register


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
async def _admin_tokens(client: AsyncClient) -> dict:
    # Idempotent: the bootstrap admin may already have been created by another
    # helper (e.g. ``_manager``) earlier in the same test.
    resp = await client.post(
        "/api/v1/auth/register",
        json={"name": "Boss", "email": "boss@example.com", "password": "password123"},
    )
    assert resp.status_code in (201, 400), resp.text
    return await login(client, "boss@example.com")


async def _member(client: AsyncClient, email: str = "member@example.com") -> dict:
    user = await register(client, email)
    tokens = await login(client, email)
    return {"id": user["id"], "token": tokens["access_token"]}


async def _manager(client: AsyncClient, email: str = "manager@example.com") -> dict:
    admin = await _admin_tokens(client)
    user = await register(client, email)
    resp = await client.patch(
        f"/api/v1/users/{user['id']}/role",
        json={"role": "Manager"},
        headers=auth_header(admin["access_token"]),
    )
    assert resp.status_code == 200, resp.text
    tokens = await login(client, email)
    return {"id": user["id"], "token": tokens["access_token"]}


async def _project(client: AsyncClient) -> str:
    """Create a project as the bootstrap admin and return its id."""
    admin = await _admin_tokens(client)
    resp = await client.post(
        "/api/v1/projects/",
        json={"name": "Apollo", "description": "Billing rebuild"},
        headers=auth_header(admin["access_token"]),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _body(project_id: str, **overrides) -> dict:
    body = {
        "project_id": project_id,
        "week_start_date": "2026-01-05",
        "week_end_date": "2026-01-09",
        "tasks_planned_next_week": "Finish the reports API",
        "tasks_completed": [
            {
                "task_name": "Build POST /reports",
                "priority": "HIGH",
                "planned_percentage": 100,
                "actual_percentage": 80,
                "status": "IN_PROGRESS",
                "time_planned_hours": 8,
                "time_spent_hours": 6,
                "output_deliverable": "PR #42",
            }
        ],
        "blockers": [{"text": "Waiting on DB creds", "is_key_issue": True}],
        "achievements": [{"text": "Shipped auth", "is_key_achievement": True}],
        "hours_worked_breakdown": {
            "development": 20,
            "testing": 5,
            "meetings": 3,
            "documentation": 2,
            "other": 1,
        },
        "notes_or_links": "https://example.com/notes",
    }
    body.update(overrides)
    return body


async def _create_report(client: AsyncClient, token: str, project_id: str, **over) -> dict:
    resp = await client.post(
        "/api/v1/reports/", json=_body(project_id, **over), headers=auth_header(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
async def test_create_requires_authentication(client: AsyncClient) -> None:
    assert (await client.post("/api/v1/reports/", json={})).status_code == 401


async def test_create_report_defaults_to_draft(client: AsyncClient) -> None:
    project_id = await _project(client)
    member = await _member(client)

    report = await _create_report(client, member["token"], project_id)
    assert report["status"] == "DRAFT"
    assert report["user_id"] == member["id"]
    assert report["project_id"] == project_id
    assert len(report["tasks_completed"]) == 1
    assert report["tasks_completed"][0]["task_name"] == "Build POST /reports"
    assert report["blockers"][0]["is_key_issue"] is True
    assert report["hours_worked_breakdown"]["development"] == 20


async def test_create_rejects_dynamic_custom_field(client: AsyncClient) -> None:
    project_id = await _project(client)
    member = await _member(client)

    resp = await client.post(
        "/api/v1/reports/",
        json=_body(project_id, mood="great"),
        headers=auth_header(member["token"]),
    )
    assert resp.status_code == 422


async def test_create_rejects_client_supplied_status(client: AsyncClient) -> None:
    project_id = await _project(client)
    member = await _member(client)

    resp = await client.post(
        "/api/v1/reports/",
        json=_body(project_id, status="APPROVED"),
        headers=auth_header(member["token"]),
    )
    assert resp.status_code == 422


async def test_create_rejects_reversed_week_range(client: AsyncClient) -> None:
    project_id = await _project(client)
    member = await _member(client)

    resp = await client.post(
        "/api/v1/reports/",
        json=_body(project_id, week_start_date="2026-01-09", week_end_date="2026-01-05"),
        headers=auth_header(member["token"]),
    )
    assert resp.status_code == 422


async def test_create_enforces_single_key_issue(client: AsyncClient) -> None:
    project_id = await _project(client)
    member = await _member(client)
    headers = auth_header(member["token"])

    two_keys = _body(
        project_id,
        blockers=[
            {"text": "a", "is_key_issue": True},
            {"text": "b", "is_key_issue": True},
        ],
    )
    assert (
        await client.post("/api/v1/reports/", json=two_keys, headers=headers)
    ).status_code == 422

    no_key = _body(project_id, blockers=[{"text": "a", "is_key_issue": False}])
    assert (
        await client.post("/api/v1/reports/", json=no_key, headers=headers)
    ).status_code == 422


async def test_create_enforces_single_key_achievement(client: AsyncClient) -> None:
    project_id = await _project(client)
    member = await _member(client)

    bad = _body(
        project_id,
        achievements=[
            {"text": "a", "is_key_achievement": True},
            {"text": "b", "is_key_achievement": True},
        ],
    )
    resp = await client.post(
        "/api/v1/reports/", json=bad, headers=auth_header(member["token"])
    )
    assert resp.status_code == 422


async def test_create_allows_empty_blocker_and_achievement_lists(client: AsyncClient) -> None:
    project_id = await _project(client)
    member = await _member(client)

    report = await _create_report(
        client, member["token"], project_id, blockers=[], achievements=[]
    )
    assert report["blockers"] == []
    assert report["achievements"] == []


async def test_create_rejects_invalid_task_entries(client: AsyncClient) -> None:
    project_id = await _project(client)
    member = await _member(client)
    headers = auth_header(member["token"])

    base_task = {
        "task_name": "x",
        "priority": "LOW",
        "planned_percentage": 10,
        "actual_percentage": 10,
        "status": "NOT_STARTED",
        "time_planned_hours": 1,
        "time_spent_hours": 1,
    }
    for bad_task in (
        {**base_task, "task_name": ""},
        {**base_task, "planned_percentage": 150},
        {**base_task, "actual_percentage": -1},
        {**base_task, "time_spent_hours": -3},
        {**base_task, "priority": "SOMEDAY"},
    ):
        resp = await client.post(
            "/api/v1/reports/",
            json=_body(project_id, tasks_completed=[bad_task]),
            headers=headers,
        )
        assert resp.status_code == 422, bad_task


async def test_create_with_unknown_project_returns_400(client: AsyncClient) -> None:
    await _project(client)  # ensures at least the admin exists
    member = await _member(client)

    resp = await client.post(
        "/api/v1/reports/",
        json=_body("000000000000000000000000"),
        headers=auth_header(member["token"]),
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Retrieve - ownership & role rules
# ---------------------------------------------------------------------------
async def test_owner_gets_full_detail(client: AsyncClient) -> None:
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(client, member["token"], project_id)

    resp = await client.get(
        f"/api/v1/reports/{created['id']}", headers=auth_header(member["token"])
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == created["id"]
    assert body["tasks_completed"][0]["output_deliverable"] == "PR #42"


async def test_team_member_cannot_view_another_users_report(client: AsyncClient) -> None:
    project_id = await _project(client)
    owner = await _member(client, "owner@example.com")
    created = await _create_report(client, owner["token"], project_id)
    intruder = await _member(client, "intruder@example.com")

    resp = await client.get(
        f"/api/v1/reports/{created['id']}", headers=auth_header(intruder["token"])
    )
    assert resp.status_code == 403


async def test_manager_can_view_another_users_report(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    owner = await _member(client, "owner@example.com")
    created = await _create_report(client, owner["token"], project_id)

    resp = await client.get(
        f"/api/v1/reports/{created['id']}", headers=auth_header(manager["token"])
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


async def test_unknown_and_malformed_report_ids_return_404(client: AsyncClient) -> None:
    member = await _member(client)
    headers = auth_header(member["token"])
    for rid in ("000000000000000000000000", "not-an-object-id"):
        assert (await client.get(f"/api/v1/reports/{rid}", headers=headers)).status_code == 404


# ---------------------------------------------------------------------------
# Update - editability & ownership
# ---------------------------------------------------------------------------
async def test_owner_can_update_draft(client: AsyncClient) -> None:
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(client, member["token"], project_id)

    resp = await client.put(
        f"/api/v1/reports/{created['id']}",
        json={"notes_or_links": "updated", "tasks_planned_next_week": "New plan"},
        headers=auth_header(member["token"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["notes_or_links"] == "updated"
    assert body["tasks_planned_next_week"] == "New plan"
    assert body["updated_at"] >= created["updated_at"]


async def test_owner_can_update_needs_correction(client: AsyncClient) -> None:
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(client, member["token"], project_id)

    doc = await Report.get(created["id"])
    doc.status = ReportStatus.NEEDS_CORRECTION
    await doc.save()

    resp = await client.put(
        f"/api/v1/reports/{created['id']}",
        json={"notes_or_links": "reworked"},
        headers=auth_header(member["token"]),
    )
    assert resp.status_code == 200
    assert resp.json()["notes_or_links"] == "reworked"


async def test_update_rejected_when_submitted_or_approved(client: AsyncClient) -> None:
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(client, member["token"], project_id)

    for locked in (ReportStatus.SUBMITTED, ReportStatus.APPROVED):
        doc = await Report.get(created["id"])
        doc.status = locked
        await doc.save()

        resp = await client.put(
            f"/api/v1/reports/{created['id']}",
            json={"notes_or_links": "nope"},
            headers=auth_header(member["token"]),
        )
        assert resp.status_code == 400, locked


async def test_update_cannot_change_status(client: AsyncClient) -> None:
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(client, member["token"], project_id)

    resp = await client.put(
        f"/api/v1/reports/{created['id']}",
        json={"status": "APPROVED"},
        headers=auth_header(member["token"]),
    )
    assert resp.status_code == 422


async def test_update_forbidden_for_non_owner(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    owner = await _member(client, "owner@example.com")
    created = await _create_report(client, owner["token"], project_id)

    # Even a Manager may not edit someone else's report via this route.
    resp = await client.put(
        f"/api/v1/reports/{created['id']}",
        json={"notes_or_links": "hijack"},
        headers=auth_header(manager["token"]),
    )
    assert resp.status_code == 403


async def test_update_rejects_invariant_breaking_partial(client: AsyncClient) -> None:
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(client, member["token"], project_id)

    # Only the start date is sent, pushing it past the stored end date.
    resp = await client.put(
        f"/api/v1/reports/{created['id']}",
        json={"week_start_date": "2026-02-01"},
        headers=auth_header(member["token"]),
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# List - GET /reports/me
# ---------------------------------------------------------------------------
async def test_list_me_returns_only_own_reports(client: AsyncClient) -> None:
    project_id = await _project(client)
    me = await _member(client, "me@example.com")
    other = await _member(client, "other@example.com")

    await _create_report(client, me["token"], project_id)
    await _create_report(client, me["token"], project_id, week_start_date="2026-01-12",
                         week_end_date="2026-01-16")
    await _create_report(client, other["token"], project_id)

    resp = await client.get("/api/v1/reports/me", headers=auth_header(me["token"]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    assert {item["status"] for item in body["items"]} == {"DRAFT"}
    assert all("project_id" in item and "updated_at" in item for item in body["items"])


async def test_list_me_is_paginated(client: AsyncClient) -> None:
    project_id = await _project(client)
    me = await _member(client)
    for week in ("2026-01-05", "2026-01-12", "2026-01-19"):
        await _create_report(
            client, me["token"], project_id, week_start_date=week,
            week_end_date="2026-02-01",
        )

    page1 = await client.get(
        "/api/v1/reports/me", params={"page": 1, "page_size": 2},
        headers=auth_header(me["token"]),
    )
    assert page1.status_code == 200
    assert page1.json()["total"] == 3
    assert len(page1.json()["items"]) == 2

    page2 = await client.get(
        "/api/v1/reports/me", params={"page": 2, "page_size": 2},
        headers=auth_header(me["token"]),
    )
    assert len(page2.json()["items"]) == 1


async def test_list_me_status_filter(client: AsyncClient) -> None:
    project_id = await _project(client)
    me = await _member(client)
    a = await _create_report(client, me["token"], project_id)
    await _create_report(client, me["token"], project_id, week_start_date="2026-01-12",
                         week_end_date="2026-01-16")

    doc = await Report.get(a["id"])
    doc.status = ReportStatus.SUBMITTED
    await doc.save()

    submitted = await client.get(
        "/api/v1/reports/me", params={"status": "SUBMITTED"},
        headers=auth_header(me["token"]),
    )
    assert submitted.status_code == 200
    assert submitted.json()["total"] == 1
    assert submitted.json()["items"][0]["id"] == a["id"]

    drafts = await client.get(
        "/api/v1/reports/me", params={"status": "DRAFT"},
        headers=auth_header(me["token"]),
    )
    assert drafts.json()["total"] == 1


async def test_list_me_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/reports/me")).status_code == 401
