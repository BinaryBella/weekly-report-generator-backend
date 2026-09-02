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


async def _project(client: AsyncClient, name: str = "Apollo") -> str:
    """Create a project as the bootstrap admin and return its id."""
    admin = await _admin_tokens(client)
    resp = await client.post(
        "/api/v1/projects/",
        json={"name": name, "description": "Billing rebuild"},
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


async def test_manager_can_view_another_users_submitted_report(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    owner = await _member(client, "owner@example.com")
    created = await _create_report(client, owner["token"], project_id)
    await client.post(
        f"/api/v1/reports/{created['id']}/submit", headers=auth_header(owner["token"])
    )

    resp = await client.get(
        f"/api/v1/reports/{created['id']}", headers=auth_header(manager["token"])
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


async def test_manager_cannot_view_another_users_draft(client: AsyncClient) -> None:
    # A DRAFT is "only visible to them" until it is submitted for review.
    manager = await _manager(client)
    project_id = await _project(client)
    owner = await _member(client, "owner@example.com")
    created = await _create_report(client, owner["token"], project_id)

    resp = await client.get(
        f"/api/v1/reports/{created['id']}", headers=auth_header(manager["token"])
    )
    assert resp.status_code == 403


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


async def test_list_me_is_organised_by_week_newest_first(client: AsyncClient) -> None:
    project_id = await _project(client)
    me = await _member(client)
    for week in ("2026-01-05", "2026-03-02", "2026-02-02"):
        await _create_report(
            client, me["token"], project_id,
            week_start_date=week, week_end_date="2026-04-01",
        )

    body = (
        await client.get("/api/v1/reports/me", headers=auth_header(me["token"]))
    ).json()
    assert [item["week_start_date"] for item in body["items"]] == [
        "2026-03-02",
        "2026-02-02",
        "2026-01-05",
    ]


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
<<<<<<< Updated upstream
=======


# ---------------------------------------------------------------------------
# Section 3 - Review & correction workflow
# ---------------------------------------------------------------------------
async def _submit(client: AsyncClient, token: str, report_id: str):
    return await client.post(
        f"/api/v1/reports/{report_id}/submit", headers=auth_header(token)
    )


async def _approve(client: AsyncClient, token: str, report_id: str):
    return await client.post(
        f"/api/v1/reports/{report_id}/approve", headers=auth_header(token)
    )


async def _request_changes(client: AsyncClient, token: str, report_id: str, comment: str):
    return await client.post(
        f"/api/v1/reports/{report_id}/request-changes",
        json={"comment": comment},
        headers=auth_header(token),
    )


async def test_submit_moves_draft_to_submitted(client: AsyncClient) -> None:
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(client, member["token"], project_id)

    resp = await _submit(client, member["token"], created["id"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "SUBMITTED"
    assert body["submitted_at"] is not None


async def test_submit_forbidden_for_non_owner(client: AsyncClient) -> None:
    project_id = await _project(client)
    owner = await _member(client, "owner@example.com")
    other = await _member(client, "other@example.com")
    created = await _create_report(client, owner["token"], project_id)

    assert (await _submit(client, other["token"], created["id"])).status_code == 403


async def test_submit_rejected_when_already_submitted(client: AsyncClient) -> None:
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(client, member["token"], project_id)

    assert (await _submit(client, member["token"], created["id"])).status_code == 200
    assert (await _submit(client, member["token"], created["id"])).status_code == 400


async def test_manager_approves_submitted_report(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(client, member["token"], project_id)
    await _submit(client, member["token"], created["id"])

    resp = await _approve(client, manager["token"], created["id"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "APPROVED"
    assert resp.json()["reviewed_at"] is not None


async def test_team_member_cannot_approve(client: AsyncClient) -> None:
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(client, member["token"], project_id)
    await _submit(client, member["token"], created["id"])

    assert (await _approve(client, member["token"], created["id"])).status_code == 403


async def test_approve_rejected_when_not_submitted(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(client, member["token"], project_id)

    # Still a DRAFT - cannot be approved.
    assert (await _approve(client, manager["token"], created["id"])).status_code == 400


async def test_request_changes_sends_report_back_with_comment(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(client, member["token"], project_id)
    await _submit(client, member["token"], created["id"])

    resp = await _request_changes(
        client, manager["token"], created["id"], "Add more detail to the blockers"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "NEEDS_CORRECTION"
    assert body["latest_review_comment"]["comment"] == "Add more detail to the blockers"
    assert body["latest_review_comment"]["manager_id"] == manager["id"]
    assert len(body["review_comments"]) == 1

    # The team member sees the comment on their own report page.
    owner_view = await client.get(
        f"/api/v1/reports/{created['id']}", headers=auth_header(member["token"])
    )
    assert owner_view.json()["latest_review_comment"]["comment"] == (
        "Add more detail to the blockers"
    )


async def test_request_changes_requires_a_comment(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(client, member["token"], project_id)
    await _submit(client, member["token"], created["id"])

    for bad in ({}, {"comment": ""}, {"comment": "   "}):
        resp = await client.post(
            f"/api/v1/reports/{created['id']}/request-changes",
            json=bad,
            headers=auth_header(manager["token"]),
        )
        assert resp.status_code == 422, bad


async def test_team_member_cannot_request_changes(client: AsyncClient) -> None:
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(client, member["token"], project_id)
    await _submit(client, member["token"], created["id"])

    assert (
        await _request_changes(client, member["token"], created["id"], "self review")
    ).status_code == 403


async def test_needs_correction_is_editable_then_resubmittable(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(client, member["token"], project_id)
    await _submit(client, member["token"], created["id"])
    await _request_changes(client, manager["token"], created["id"], "Fix the plan")

    # Editable again by its owner while in NEEDS_CORRECTION.
    edit = await client.put(
        f"/api/v1/reports/{created['id']}",
        json={"tasks_planned_next_week": "Corrected plan"},
        headers=auth_header(member["token"]),
    )
    assert edit.status_code == 200, edit.text
    assert edit.json()["tasks_planned_next_week"] == "Corrected plan"

    # Resubmit moves it back to SUBMITTED for another review.
    resubmit = await _submit(client, member["token"], created["id"])
    assert resubmit.status_code == 200
    assert resubmit.json()["status"] == "SUBMITTED"


async def test_correction_cycle_keeps_previous_version_visible(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(
        client, member["token"], project_id, tasks_planned_next_week="Original plan"
    )
    await _submit(client, member["token"], created["id"])
    await _request_changes(client, manager["token"], created["id"], "Rework the plan")

    await client.put(
        f"/api/v1/reports/{created['id']}",
        json={"tasks_planned_next_week": "Reworked plan"},
        headers=auth_header(member["token"]),
    )
    await _submit(client, member["token"], created["id"])

    # A manager reviewing the resubmission still sees the original version.
    detail = await client.get(
        f"/api/v1/reports/{created['id']}", headers=auth_header(manager["token"])
    )
    body = detail.json()
    assert body["tasks_planned_next_week"] == "Reworked plan"
    assert len(body["version_history"]) == 1
    old = body["version_history"][0]
    assert old["version"] == 1
    assert old["tasks_planned_next_week"] == "Original plan"
    assert old["status_at_snapshot"] == "SUBMITTED"
    assert old["submitted_at"] is not None  # when that version was submitted

    # The comment records which version it was made against.
    assert body["review_comments"][0]["against_version"] == 1

    # The past versions are also reachable on demand via the dedicated route.
    versions = await client.get(
        f"/api/v1/reports/{created['id']}/versions",
        headers=auth_header(manager["token"]),
    )
    assert versions.status_code == 200
    assert [v["version"] for v in versions.json()] == [1]
    assert versions.json()[0]["tasks_planned_next_week"] == "Original plan"


async def test_versions_endpoint_hidden_from_other_team_members(client: AsyncClient) -> None:
    project_id = await _project(client)
    owner = await _member(client, "owner@example.com")
    intruder = await _member(client, "intruder@example.com")
    created = await _create_report(client, owner["token"], project_id)

    resp = await client.get(
        f"/api/v1/reports/{created['id']}/versions",
        headers=auth_header(intruder["token"]),
    )
    assert resp.status_code == 403


async def test_review_comment_history_accumulates(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(client, member["token"], project_id)

    for note in ("First round of fixes", "Second round of fixes"):
        await _submit(client, member["token"], created["id"])
        await _request_changes(client, manager["token"], created["id"], note)

    detail = await client.get(
        f"/api/v1/reports/{created['id']}", headers=auth_header(member["token"])
    )
    comments = detail.json()["review_comments"]
    assert [c["comment"] for c in comments] == [
        "First round of fixes",
        "Second round of fixes",
    ]
    assert detail.json()["latest_review_comment"]["comment"] == "Second round of fixes"
    assert len(detail.json()["version_history"]) == 2
    # Each note points at the successive version it was raised against.
    assert [c["against_version"] for c in comments] == [1, 2]


async def test_approved_report_cannot_be_edited_or_resubmitted(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(client, member["token"], project_id)
    await _submit(client, member["token"], created["id"])
    await _approve(client, manager["token"], created["id"])

    edit = await client.put(
        f"/api/v1/reports/{created['id']}",
        json={"notes_or_links": "late edit"},
        headers=auth_header(member["token"]),
    )
    assert edit.status_code == 400
    assert (await _submit(client, member["token"], created["id"])).status_code == 400


async def test_manager_dashboard_lists_every_members_reports(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    a = await _member(client, "a@example.com")
    b = await _member(client, "b@example.com")
    r_a = await _create_report(client, a["token"], project_id)
    r_b = await _create_report(client, b["token"], project_id)
    await _submit(client, a["token"], r_a["id"])
    await _submit(client, b["token"], r_b["id"])

    resp = await client.get("/api/v1/reports/", headers=auth_header(manager["token"]))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert {item["user_id"] for item in body["items"]} == {a["id"], b["id"]}

    submitted = await client.get(
        "/api/v1/reports/", params={"status": "SUBMITTED"},
        headers=auth_header(manager["token"]),
    )
    assert submitted.json()["total"] == 2

    by_user = await client.get(
        "/api/v1/reports/", params={"user_id": b["id"]},
        headers=auth_header(manager["token"]),
    )
    assert by_user.json()["total"] == 1
    assert by_user.json()["items"][0]["user_id"] == b["id"]


async def test_manager_dashboard_excludes_private_drafts(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    a = await _member(client, "a@example.com")
    b = await _member(client, "b@example.com")
    r_a = await _create_report(client, a["token"], project_id)
    await _create_report(client, b["token"], project_id)  # stays a DRAFT
    await _submit(client, a["token"], r_a["id"])

    resp = await client.get("/api/v1/reports/", headers=auth_header(manager["token"]))
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == r_a["id"]

    # Explicitly asking for DRAFTs on the dashboard yields nothing.
    drafts = await client.get(
        "/api/v1/reports/", params={"status": "DRAFT"},
        headers=auth_header(manager["token"]),
    )
    assert drafts.json()["total"] == 0


async def test_manager_dashboard_forbidden_for_team_member(client: AsyncClient) -> None:
    project_id = await _project(client)
    member = await _member(client)
    await _create_report(client, member["token"], project_id)

    assert (
        await client.get("/api/v1/reports/", headers=auth_header(member["token"]))
    ).status_code == 403


async def test_workflow_actions_404_on_unknown_report(client: AsyncClient) -> None:
    manager = await _manager(client)
    member = await _member(client)
    missing = "000000000000000000000000"

    assert (await _submit(client, member["token"], missing)).status_code == 404
    assert (await _approve(client, manager["token"], missing)).status_code == 404
    assert (
        await _request_changes(client, manager["token"], missing, "x")
    ).status_code == 404


# ---------------------------------------------------------------------------
# Section 4 - Team dashboard (manager view)
# ---------------------------------------------------------------------------
async def _assign_members(client: AsyncClient, project_id: str, member_ids: list[str]):
    admin = await _admin_tokens(client)
    resp = await client.put(
        f"/api/v1/projects/{project_id}/members",
        json={"member_ids": member_ids},
        headers=auth_header(admin["access_token"]),
    )
    assert resp.status_code == 200, resp.text


async def test_dashboard_filters_by_selected_week_and_date_range(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    member = await _member(client)

    weeks = ("2026-01-05", "2026-01-12", "2026-01-19")
    ids = {}
    for wk in weeks:
        r = await _create_report(
            client, member["token"], project_id,
            week_start_date=wk, week_end_date="2026-02-02",
        )
        await _submit(client, member["token"], r["id"])
        ids[wk] = r["id"]

    # Exact selected week.
    one = await client.get(
        "/api/v1/reports/", params={"week_start_date": "2026-01-12"},
        headers=auth_header(manager["token"]),
    )
    assert one.status_code == 200
    assert [i["id"] for i in one.json()["items"]] == [ids["2026-01-12"]]

    # Date range covering the first two weeks.
    rng = await client.get(
        "/api/v1/reports/",
        params={"date_from": "2026-01-01", "date_to": "2026-01-13"},
        headers=auth_header(manager["token"]),
    )
    assert rng.json()["total"] == 2
    assert {i["id"] for i in rng.json()["items"]} == {ids["2026-01-05"], ids["2026-01-12"]}


async def test_team_status_overview_tracks_every_member(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    a = await _member(client, "a@example.com")
    b = await _member(client, "b@example.com")
    await _member(client, "c@example.com")  # never starts a report

    week = {"week_start_date": "2026-01-05", "week_end_date": "2026-01-09"}
    r_a = await _create_report(client, a["token"], project_id, **week)
    await _submit(client, a["token"], r_a["id"])
    await _create_report(client, b["token"], project_id, **week)  # stays DRAFT

    resp = await client.get(
        "/api/v1/reports/dashboard/status",
        params={"week_start_date": "2026-01-05"},
        headers=auth_header(manager["token"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_members"] == 3
    by_user = {row["user_id"]: row["status"] for row in body["rows"]}
    assert by_user[a["id"]] == "SUBMITTED"
    assert by_user[b["id"]] == "DRAFT"
    assert body["status_counts"] == {
        "NOT_STARTED": 1,
        "DRAFT": 1,
        "SUBMITTED": 1,
        "NEEDS_CORRECTION": 0,
        "APPROVED": 0,
    }
    # Every roster row carries identifying info; the not-started member has no report.
    not_started = [r for r in body["rows"] if r["status"] == "NOT_STARTED"][0]
    assert not_started["report_id"] is None
    assert not_started["user_email"] == "c@example.com"


async def test_team_status_overview_is_week_scoped(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    a = await _member(client, "a@example.com")

    r = await _create_report(
        client, a["token"], project_id,
        week_start_date="2026-02-02", week_end_date="2026-02-06",
    )
    await _submit(client, a["token"], r["id"])

    # A different week: the member reads as NOT_STARTED there.
    resp = await client.get(
        "/api/v1/reports/dashboard/status",
        params={"week_start_date": "2026-01-05"},
        headers=auth_header(manager["token"]),
    )
    assert {row["status"] for row in resp.json()["rows"]} == {"NOT_STARTED"}


async def test_team_status_overview_scoped_to_project_roster(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    a = await _member(client, "a@example.com")
    b = await _member(client, "b@example.com")
    await _member(client, "c@example.com")
    await _assign_members(client, project_id, [a["id"], b["id"]])

    resp = await client.get(
        "/api/v1/reports/dashboard/status",
        params={"week_start_date": "2026-01-05", "project_id": project_id},
        headers=auth_header(manager["token"]),
    )
    assert resp.status_code == 200
    assert {row["user_id"] for row in resp.json()["rows"]} == {a["id"], b["id"]}


async def test_team_status_overview_unknown_project_returns_400(client: AsyncClient) -> None:
    manager = await _manager(client)
    resp = await client.get(
        "/api/v1/reports/dashboard/status",
        params={"week_start_date": "2026-01-05", "project_id": "000000000000000000000000"},
        headers=auth_header(manager["token"]),
    )
    assert resp.status_code == 400


async def test_team_status_overview_forbidden_for_team_member(client: AsyncClient) -> None:
    member = await _member(client)
    resp = await client.get(
        "/api/v1/reports/dashboard/status",
        params={"week_start_date": "2026-01-05"},
        headers=auth_header(member["token"]),
    )
    assert resp.status_code == 403


async def test_team_section_overview_blockers_side_by_side(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    a = await _member(client, "a@example.com")
    b = await _member(client, "b@example.com")
    await _member(client, "c@example.com")  # not started

    week = {"week_start_date": "2026-01-05", "week_end_date": "2026-01-09"}
    r_a = await _create_report(
        client, a["token"], project_id,
        blockers=[{"text": "DB creds missing", "is_key_issue": True}], **week,
    )
    await _submit(client, a["token"], r_a["id"])
    # b keeps a DRAFT with its own blockers - content must stay private.
    await _create_report(
        client, b["token"], project_id,
        blockers=[{"text": "secret draft blocker", "is_key_issue": True}], **week,
    )

    resp = await client.get(
        "/api/v1/reports/dashboard/section/blockers",
        params={"week_start_date": "2026-01-05"},
        headers=auth_header(manager["token"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["section"] == "blockers"
    by_user = {e["user_id"]: e for e in body["entries"]}
    assert by_user[a["id"]]["content"][0]["text"] == "DB creds missing"
    assert by_user[a["id"]]["status"] == "SUBMITTED"
    # Draft content is withheld; not-started member has none.
    assert by_user[b["id"]]["status"] == "DRAFT"
    assert by_user[b["id"]]["content"] is None


async def test_team_section_overview_scalar_and_hours_sections(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    a = await _member(client, "a@example.com")

    week = {"week_start_date": "2026-01-05", "week_end_date": "2026-01-09"}
    r_a = await _create_report(
        client, a["token"], project_id,
        tasks_planned_next_week="Ship the dashboard", **week,
    )
    await _submit(client, a["token"], r_a["id"])

    planned = await client.get(
        "/api/v1/reports/dashboard/section/tasks_planned_next_week",
        params={"week_start_date": "2026-01-05"},
        headers=auth_header(manager["token"]),
    )
    assert planned.json()["entries"][0]["content"] == "Ship the dashboard"

    hours = await client.get(
        "/api/v1/reports/dashboard/section/hours_worked_breakdown",
        params={"week_start_date": "2026-01-05"},
        headers=auth_header(manager["token"]),
    )
    assert hours.json()["entries"][0]["content"]["development"] == 20


async def test_team_section_overview_rejects_unknown_section(client: AsyncClient) -> None:
    manager = await _manager(client)
    resp = await client.get(
        "/api/v1/reports/dashboard/section/mood",
        params={"week_start_date": "2026-01-05"},
        headers=auth_header(manager["token"]),
    )
    assert resp.status_code == 422


async def test_team_section_overview_forbidden_for_team_member(client: AsyncClient) -> None:
    member = await _member(client)
    resp = await client.get(
        "/api/v1/reports/dashboard/section/achievements",
        params={"week_start_date": "2026-01-05"},
        headers=auth_header(member["token"]),
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Section 6 - Dashboard & visual insights
# ---------------------------------------------------------------------------
_COMPLETED_TASK = {
    "task_name": "Wrote the report API",
    "priority": "HIGH",
    "planned_percentage": 100,
    "actual_percentage": 100,
    "status": "COMPLETED",
    "time_planned_hours": 6,
    "time_spent_hours": 7,
}


async def test_dashboard_summary_metrics(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    a = await _member(client, "a@example.com")
    b = await _member(client, "b@example.com")
    await _member(client, "c@example.com")  # never submits -> pending

    # A submits for a week that already ended -> late.
    r_a = await _create_report(
        client, a["token"], project_id,
        week_start_date="2026-01-05", week_end_date="2026-01-09",
    )
    await _submit(client, a["token"], r_a["id"])
    # B submits for the same selected week but a far-future end date -> on time.
    r_b = await _create_report(
        client, b["token"], project_id,
        week_start_date="2026-01-05", week_end_date="2099-01-09",
    )
    await _submit(client, b["token"], r_b["id"])
    await _request_changes(client, manager["token"], r_b["id"], "tidy it up")

    resp = await client.get(
        "/api/v1/reports/dashboard/summary",
        params={"week_start_date": "2026-01-05"},
        headers=auth_header(manager["token"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_members"] == 3
    assert body["total_submitted_this_week"] == 2
    comp = body["submission_compliance"]
    assert comp["submitted"] == 2
    assert comp["pending"] == 1
    assert comp["late"] == 1
    assert comp["on_time"] == 1
    assert round(comp["compliance_rate"], 2) == 0.67
    assert body["needs_correction_count"] == 1
    assert body["open_blockers"] == 2       # one key blocker per report (default body)
    assert body["open_key_issues"] == 2


async def test_dashboard_summary_forbidden_for_team_member(client: AsyncClient) -> None:
    member = await _member(client)
    resp = await client.get(
        "/api/v1/reports/dashboard/summary",
        params={"week_start_date": "2026-01-05"},
        headers=auth_header(member["token"]),
    )
    assert resp.status_code == 403


async def test_tasks_completed_trend_team_and_per_user(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    a = await _member(client, "a@example.com")
    b = await _member(client, "b@example.com")

    for wk in ("2026-01-05", "2026-01-12"):
        for who in (a, b):
            r = await _create_report(
                client, who["token"], project_id,
                week_start_date=wk, week_end_date="2026-02-02",
                tasks_completed=[_COMPLETED_TASK],
            )
            await _submit(client, who["token"], r["id"])

    team = await client.get(
        "/api/v1/reports/dashboard/charts/tasks-completed-trend",
        headers=auth_header(manager["token"]),
    )
    assert team.status_code == 200, team.text
    body = team.json()
    assert body["group_by"] == "team"
    assert len(body["series"]) == 1
    pts = {p["week_start_date"]: p for p in body["series"][0]["points"]}
    assert pts["2026-01-05"]["completed_tasks"] == 2
    assert pts["2026-01-12"]["completed_tasks"] == 2

    per_user = await client.get(
        "/api/v1/reports/dashboard/charts/tasks-completed-trend",
        params={"group_by": "user"},
        headers=auth_header(manager["token"]),
    )
    keys = {s["key"] for s in per_user.json()["series"]}
    assert keys == {a["id"], b["id"]}
    for s in per_user.json()["series"]:
        assert sum(p["completed_tasks"] for p in s["points"]) == 2


async def test_status_by_member_marks_not_started_for_a_week(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    a = await _member(client, "a@example.com")
    await _member(client, "b@example.com")  # no report

    r_a = await _create_report(
        client, a["token"], project_id,
        week_start_date="2026-01-05", week_end_date="2026-01-09",
    )
    await _submit(client, a["token"], r_a["id"])

    resp = await client.get(
        "/api/v1/reports/dashboard/charts/status-by-member",
        params={"week_start_date": "2026-01-05"},
        headers=auth_header(manager["token"]),
    )
    assert resp.status_code == 200
    rows = {row["user_id"]: row for row in resp.json()["rows"]}
    assert rows[a["id"]]["submitted"] == 1
    assert rows[a["id"]]["not_started"] == 0
    assert [r for r in resp.json()["rows"] if r["not_started"] == 1][0]["user_name"]


async def test_workload_by_project_distribution(client: AsyncClient) -> None:
    manager = await _manager(client)
    apollo = await _project(client, "Apollo")
    gemini = await _project(client, "Gemini")
    a = await _member(client, "a@example.com")

    week = {"week_start_date": "2026-01-05", "week_end_date": "2026-01-09"}
    for pid in (apollo, apollo, gemini):
        r = await _create_report(
            client, a["token"], pid, tasks_completed=[_COMPLETED_TASK], **week,
        )
        await _submit(client, a["token"], r["id"])

    resp = await client.get(
        "/api/v1/reports/dashboard/charts/workload-by-project",
        params={"week_start_date": "2026-01-05"},
        headers=auth_header(manager["token"]),
    )
    assert resp.status_code == 200, resp.text
    rows = {row["project_id"]: row for row in resp.json()["rows"]}
    assert rows[apollo]["reports"] == 2
    assert rows[apollo]["project_name"] == "Apollo"
    assert rows[apollo]["spent_hours"] == 14.0   # 2 reports x 7h
    assert rows[gemini]["reports"] == 1


async def test_hours_by_type_team_wide(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    a = await _member(client, "a@example.com")
    b = await _member(client, "b@example.com")

    week = {"week_start_date": "2026-01-05", "week_end_date": "2026-01-09"}
    for who in (a, b):
        r = await _create_report(client, who["token"], project_id, **week)
        await _submit(client, who["token"], r["id"])

    resp = await client.get(
        "/api/v1/reports/dashboard/charts/hours-by-type",
        params={"week_start_date": "2026-01-05"},
        headers=auth_header(manager["token"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reports_counted"] == 2
    assert body["development"] == 40.0   # 2 x 20 (default body)
    assert body["meetings"] == 6.0       # 2 x 3
    assert body["total"] == 62.0         # 2 x 31


async def test_activity_feed_orders_review_actions_newest_first(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    a = await _member(client, "a@example.com")

    r = await _create_report(client, a["token"], project_id)
    await _submit(client, a["token"], r["id"])
    await _request_changes(client, manager["token"], r["id"], "add the blockers section")
    await _submit(client, a["token"], r["id"])
    await _approve(client, manager["token"], r["id"])

    resp = await client.get(
        "/api/v1/reports/dashboard/activity", headers=auth_header(manager["token"])
    )
    assert resp.status_code == 200, resp.text
    events = resp.json()["events"]
    assert [e["type"] for e in events] == [
        "APPROVED",
        "SUBMITTED",
        "CHANGES_REQUESTED",
    ]
    changed = next(e for e in events if e["type"] == "CHANGES_REQUESTED")
    assert changed["comment"] == "add the blockers section"
    assert changed["actor_id"] == manager["id"]
    approved = events[0]
    assert approved["actor_id"] == manager["id"]
    assert approved["author_id"] == a["id"]


async def test_insights_endpoints_all_forbidden_for_team_member(client: AsyncClient) -> None:
    member = await _member(client)
    h = auth_header(member["token"])
    wk = {"week_start_date": "2026-01-05"}
    assert (await client.get("/api/v1/reports/dashboard/summary", params=wk, headers=h)).status_code == 403
    assert (await client.get("/api/v1/reports/dashboard/charts/tasks-completed-trend", headers=h)).status_code == 403
    assert (await client.get("/api/v1/reports/dashboard/charts/status-by-member", headers=h)).status_code == 403
    assert (await client.get("/api/v1/reports/dashboard/charts/workload-by-project", headers=h)).status_code == 403
    assert (await client.get("/api/v1/reports/dashboard/charts/hours-by-type", headers=h)).status_code == 403
    assert (await client.get("/api/v1/reports/dashboard/activity", headers=h)).status_code == 403


# ---------------------------------------------------------------------------
# Team member profile (manager view)
# ---------------------------------------------------------------------------
async def test_member_profile_basic_stats_and_history(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    a = await _member(client, "a@example.com")

    # 1: approved
    r1 = await _create_report(
        client, a["token"], project_id,
        week_start_date="2026-01-05", week_end_date="2026-01-09",
        tasks_completed=[_COMPLETED_TASK],
    )
    await _submit(client, a["token"], r1["id"])
    await _approve(client, manager["token"], r1["id"])

    # 2: sent back for correction, then a fresh DRAFT stays private (not counted)
    r2 = await _create_report(
        client, a["token"], project_id,
        week_start_date="2026-01-12", week_end_date="2026-01-16",
    )
    await _submit(client, a["token"], r2["id"])
    await _request_changes(client, manager["token"], r2["id"], "polish it")

    await _create_report(
        client, a["token"], project_id,
        week_start_date="2026-01-19", week_end_date="2026-01-23",
    )  # left as DRAFT - must not be counted or listed

    resp = await client.get(
        f"/api/v1/reports/dashboard/member/{a['id']}",
        headers=auth_header(manager["token"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user"]["id"] == a["id"]
    assert body["user"]["email"] == "a@example.com"

    stats = body["stats"]
    assert stats["total_reports"] == 2          # the DRAFT is excluded
    assert stats["approved_count"] == 1
    assert stats["needs_correction_count"] == 1
    assert stats["submitted_count"] == 0
    assert round(stats["approval_rate"], 2) == 0.5   # 1 approved / 2 reviewed
    assert stats["total_tasks_completed"] == 1
    assert stats["last_submitted_at"] is not None

    recent_ids = {r["id"] for r in body["recent_reports"]}
    assert recent_ids == {r1["id"], r2["id"]}


async def test_member_profile_unknown_user_returns_404(client: AsyncClient) -> None:
    manager = await _manager(client)
    resp = await client.get(
        "/api/v1/reports/dashboard/member/000000000000000000000000",
        headers=auth_header(manager["token"]),
    )
    assert resp.status_code == 404


async def test_member_profile_forbidden_for_team_member(client: AsyncClient) -> None:
    member = await _member(client)
    other = await _member(client, "other@example.com")
    resp = await client.get(
        f"/api/v1/reports/dashboard/member/{other['id']}",
        headers=auth_header(member["token"]),
    )
    assert resp.status_code == 403
>>>>>>> Stashed changes


# ---------------------------------------------------------------------------
# Section 3 - Review & correction workflow
# ---------------------------------------------------------------------------
async def _submit(client: AsyncClient, token: str, report_id: str):
    return await client.post(
        f"/api/v1/reports/{report_id}/submit", headers=auth_header(token)
    )


async def _approve(client: AsyncClient, token: str, report_id: str):
    return await client.post(
        f"/api/v1/reports/{report_id}/approve", headers=auth_header(token)
    )


async def _request_changes(client: AsyncClient, token: str, report_id: str, comment: str):
    return await client.post(
        f"/api/v1/reports/{report_id}/request-changes",
        json={"comment": comment},
        headers=auth_header(token),
    )


async def test_submit_moves_draft_to_submitted(client: AsyncClient) -> None:
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(client, member["token"], project_id)

    resp = await _submit(client, member["token"], created["id"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "SUBMITTED"
    assert body["submitted_at"] is not None


async def test_submit_forbidden_for_non_owner(client: AsyncClient) -> None:
    project_id = await _project(client)
    owner = await _member(client, "owner@example.com")
    other = await _member(client, "other@example.com")
    created = await _create_report(client, owner["token"], project_id)

    assert (await _submit(client, other["token"], created["id"])).status_code == 403


async def test_submit_rejected_when_already_submitted(client: AsyncClient) -> None:
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(client, member["token"], project_id)

    assert (await _submit(client, member["token"], created["id"])).status_code == 200
    assert (await _submit(client, member["token"], created["id"])).status_code == 400


async def test_manager_approves_submitted_report(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(client, member["token"], project_id)
    await _submit(client, member["token"], created["id"])

    resp = await _approve(client, manager["token"], created["id"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "APPROVED"
    assert resp.json()["reviewed_at"] is not None


async def test_team_member_cannot_approve(client: AsyncClient) -> None:
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(client, member["token"], project_id)
    await _submit(client, member["token"], created["id"])

    assert (await _approve(client, member["token"], created["id"])).status_code == 403


async def test_approve_rejected_when_not_submitted(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(client, member["token"], project_id)

    # Still a DRAFT - cannot be approved.
    assert (await _approve(client, manager["token"], created["id"])).status_code == 400


async def test_request_changes_sends_report_back_with_comment(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(client, member["token"], project_id)
    await _submit(client, member["token"], created["id"])

    resp = await _request_changes(
        client, manager["token"], created["id"], "Add more detail to the blockers"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "NEEDS_CORRECTION"
    assert body["latest_review_comment"]["comment"] == "Add more detail to the blockers"
    assert body["latest_review_comment"]["manager_id"] == manager["id"]
    assert len(body["review_comments"]) == 1

    # The team member sees the comment on their own report page.
    owner_view = await client.get(
        f"/api/v1/reports/{created['id']}", headers=auth_header(member["token"])
    )
    assert owner_view.json()["latest_review_comment"]["comment"] == (
        "Add more detail to the blockers"
    )


async def test_request_changes_requires_a_comment(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(client, member["token"], project_id)
    await _submit(client, member["token"], created["id"])

    for bad in ({}, {"comment": ""}, {"comment": "   "}):
        resp = await client.post(
            f"/api/v1/reports/{created['id']}/request-changes",
            json=bad,
            headers=auth_header(manager["token"]),
        )
        assert resp.status_code == 422, bad


async def test_team_member_cannot_request_changes(client: AsyncClient) -> None:
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(client, member["token"], project_id)
    await _submit(client, member["token"], created["id"])

    assert (
        await _request_changes(client, member["token"], created["id"], "self review")
    ).status_code == 403


async def test_needs_correction_is_editable_then_resubmittable(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(client, member["token"], project_id)
    await _submit(client, member["token"], created["id"])
    await _request_changes(client, manager["token"], created["id"], "Fix the plan")

    # Editable again by its owner while in NEEDS_CORRECTION.
    edit = await client.put(
        f"/api/v1/reports/{created['id']}",
        json={"tasks_planned_next_week": "Corrected plan"},
        headers=auth_header(member["token"]),
    )
    assert edit.status_code == 200, edit.text
    assert edit.json()["tasks_planned_next_week"] == "Corrected plan"

    # Resubmit moves it back to SUBMITTED for another review.
    resubmit = await _submit(client, member["token"], created["id"])
    assert resubmit.status_code == 200
    assert resubmit.json()["status"] == "SUBMITTED"


async def test_correction_cycle_keeps_previous_version_visible(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(
        client, member["token"], project_id, tasks_planned_next_week="Original plan"
    )
    await _submit(client, member["token"], created["id"])
    await _request_changes(client, manager["token"], created["id"], "Rework the plan")

    await client.put(
        f"/api/v1/reports/{created['id']}",
        json={"tasks_planned_next_week": "Reworked plan"},
        headers=auth_header(member["token"]),
    )
    await _submit(client, member["token"], created["id"])

    # A manager reviewing the resubmission still sees the original version.
    detail = await client.get(
        f"/api/v1/reports/{created['id']}", headers=auth_header(manager["token"])
    )
    body = detail.json()
    assert body["tasks_planned_next_week"] == "Reworked plan"
    assert len(body["version_history"]) == 1
    old = body["version_history"][0]
    assert old["version"] == 1
    assert old["tasks_planned_next_week"] == "Original plan"
    assert old["status_at_snapshot"] == "SUBMITTED"
    assert old["submitted_at"] is not None  # when that version was submitted

    # The comment records which version it was made against.
    assert body["review_comments"][0]["against_version"] == 1

    # The past versions are also reachable on demand via the dedicated route.
    versions = await client.get(
        f"/api/v1/reports/{created['id']}/versions",
        headers=auth_header(manager["token"]),
    )
    assert versions.status_code == 200
    assert [v["version"] for v in versions.json()] == [1]
    assert versions.json()[0]["tasks_planned_next_week"] == "Original plan"


async def test_versions_endpoint_hidden_from_other_team_members(client: AsyncClient) -> None:
    project_id = await _project(client)
    owner = await _member(client, "owner@example.com")
    intruder = await _member(client, "intruder@example.com")
    created = await _create_report(client, owner["token"], project_id)

    resp = await client.get(
        f"/api/v1/reports/{created['id']}/versions",
        headers=auth_header(intruder["token"]),
    )
    assert resp.status_code == 403


async def test_review_comment_history_accumulates(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(client, member["token"], project_id)

    for note in ("First round of fixes", "Second round of fixes"):
        await _submit(client, member["token"], created["id"])
        await _request_changes(client, manager["token"], created["id"], note)

    detail = await client.get(
        f"/api/v1/reports/{created['id']}", headers=auth_header(member["token"])
    )
    comments = detail.json()["review_comments"]
    assert [c["comment"] for c in comments] == [
        "First round of fixes",
        "Second round of fixes",
    ]
    assert detail.json()["latest_review_comment"]["comment"] == "Second round of fixes"
    assert len(detail.json()["version_history"]) == 2
    # Each note points at the successive version it was raised against.
    assert [c["against_version"] for c in comments] == [1, 2]


async def test_approved_report_cannot_be_edited_or_resubmitted(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    member = await _member(client)
    created = await _create_report(client, member["token"], project_id)
    await _submit(client, member["token"], created["id"])
    await _approve(client, manager["token"], created["id"])

    edit = await client.put(
        f"/api/v1/reports/{created['id']}",
        json={"notes_or_links": "late edit"},
        headers=auth_header(member["token"]),
    )
    assert edit.status_code == 400
    assert (await _submit(client, member["token"], created["id"])).status_code == 400


async def test_manager_dashboard_lists_every_members_reports(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    a = await _member(client, "a@example.com")
    b = await _member(client, "b@example.com")
    r_a = await _create_report(client, a["token"], project_id)
    r_b = await _create_report(client, b["token"], project_id)
    await _submit(client, a["token"], r_a["id"])
    await _submit(client, b["token"], r_b["id"])

    resp = await client.get("/api/v1/reports/", headers=auth_header(manager["token"]))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert {item["user_id"] for item in body["items"]} == {a["id"], b["id"]}

    submitted = await client.get(
        "/api/v1/reports/", params={"status": "SUBMITTED"},
        headers=auth_header(manager["token"]),
    )
    assert submitted.json()["total"] == 2

    by_user = await client.get(
        "/api/v1/reports/", params={"user_id": b["id"]},
        headers=auth_header(manager["token"]),
    )
    assert by_user.json()["total"] == 1
    assert by_user.json()["items"][0]["user_id"] == b["id"]


async def test_manager_dashboard_excludes_private_drafts(client: AsyncClient) -> None:
    manager = await _manager(client)
    project_id = await _project(client)
    a = await _member(client, "a@example.com")
    b = await _member(client, "b@example.com")
    r_a = await _create_report(client, a["token"], project_id)
    await _create_report(client, b["token"], project_id)  # stays a DRAFT
    await _submit(client, a["token"], r_a["id"])

    resp = await client.get("/api/v1/reports/", headers=auth_header(manager["token"]))
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == r_a["id"]

    # Explicitly asking for DRAFTs on the dashboard yields nothing.
    drafts = await client.get(
        "/api/v1/reports/", params={"status": "DRAFT"},
        headers=auth_header(manager["token"]),
    )
    assert drafts.json()["total"] == 0


async def test_manager_dashboard_forbidden_for_team_member(client: AsyncClient) -> None:
    project_id = await _project(client)
    member = await _member(client)
    await _create_report(client, member["token"], project_id)

    assert (
        await client.get("/api/v1/reports/", headers=auth_header(member["token"]))
    ).status_code == 403


async def test_workflow_actions_404_on_unknown_report(client: AsyncClient) -> None:
    manager = await _manager(client)
    member = await _member(client)
    missing = "000000000000000000000000"

    assert (await _submit(client, member["token"], missing)).status_code == 404
    assert (await _approve(client, manager["token"], missing)).status_code == 404
    assert (
        await _request_changes(client, manager["token"], missing, "x")
    ).status_code == 404
