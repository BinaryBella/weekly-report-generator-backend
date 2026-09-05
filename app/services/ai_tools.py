"""Read-only data-lookup tools exposed to the OpenAI chat assistant.

Function-calling, not RAG: report data already lives in typed MongoDB
documents queryable by project/user/date range, so the model's job is to
translate a manager's natural-language question into one of the calls below
rather than to search unstructured text. Every tool wraps an existing
service/repository method, so a chat answer can never surface data the
manager couldn't already see through the regular dashboard endpoints (a
private DRAFT report, for instance, is never included).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Awaitable, Callable

from bson.errors import InvalidId

from app.models.user import User, UserStatus
from app.repositories.project_repository import ProjectRepository
from app.services.report_service import ReportService

_project_repo = ProjectRepository()


# ---------------------------------------------------------------------------
# Name -> id resolution (the model only ever sees human-readable names)
# ---------------------------------------------------------------------------
async def resolve_project_id(name_or_id: str | None) -> str | None:
    """Resolve a project name (or id) to its id; ``None`` if nothing matches.

    Public because :meth:`~app.services.chat_service.ChatService.generate_team_summary`
    needs the same name resolution outside of the tool-calling loop.
    """
    if not name_or_id:
        return None
    project = await _project_repo.get(name_or_id)
    if project is not None:
        return str(project.id)

    lowered = name_or_id.strip().lower()
    for candidate in await _project_repo.list():
        if lowered in candidate.name.lower():
            return str(candidate.id)
    return None


async def _resolve_user_id(name_or_id: str | None) -> str | None:
    """Resolve a team member's name (or id) to their id; ``None`` if no match."""
    if not name_or_id:
        return None
    try:
        user = await User.get(name_or_id)
    except (InvalidId, ValueError):
        user = None
    if user is not None:
        return str(user.id)

    lowered = name_or_id.strip().lower()
    for candidate in await User.find(User.status == UserStatus.ACTIVE).to_list():
        if lowered in candidate.name.lower():
            return str(candidate.id)
    return None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------
async def _tool_list_projects(_args: dict, *, reports: ReportService) -> dict:
    projects = await _project_repo.list(active_only=True)
    return {
        "projects": [
            {"project_name": p.name, "member_count": len(p.member_ids)}
            for p in projects
        ]
    }


async def _tool_get_team_activity(args: dict, *, reports: ReportService) -> dict:
    project_raw = args.get("project_name_or_id")
    member_raw = args.get("member_name_or_id")

    project_id = await resolve_project_id(project_raw)
    if project_raw and project_id is None:
        return {"error": f"No project matching '{project_raw}' was found."}

    user_id = await _resolve_user_id(member_raw)
    if member_raw and user_id is None:
        return {"error": f"No team member matching '{member_raw}' was found."}

    date_from = _parse_date(args.get("date_from"))
    date_to = _parse_date(args.get("date_to"))
    if date_from is None or date_to is None:
        return {"error": "date_from and date_to are required, as YYYY-MM-DD."}

    data = await reports.team_activity_details(
        project_id=project_id, user_id=user_id, date_from=date_from, date_to=date_to
    )
    return {"reports": data, "count": len(data)}


async def _tool_get_workload_and_hours(args: dict, *, reports: ReportService) -> dict:
    project_raw = args.get("project_name_or_id")
    project_id = await resolve_project_id(project_raw)
    if project_raw and project_id is None:
        return {"error": f"No project matching '{project_raw}' was found."}

    date_from = _parse_date(args.get("date_from"))
    date_to = _parse_date(args.get("date_to"))
    if date_from is None or date_to is None:
        return {"error": "date_from and date_to are required, as YYYY-MM-DD."}

    workload = await reports.workload_by_project(date_from=date_from, date_to=date_to)
    if project_id is not None:
        workload = {
            **workload,
            "rows": [r for r in workload["rows"] if r["project_id"] == project_id],
        }
    hours = await reports.hours_by_type(
        project_id=project_id, date_from=date_from, date_to=date_to
    )
    return {"workload_by_project": workload, "hours_by_type": hours}


async def _tool_get_submission_status(args: dict, *, reports: ReportService) -> dict:
    week_start_date = _parse_date(args.get("week_start_date"))
    if week_start_date is None:
        return {"error": "week_start_date (the Monday starting the week) is required, as YYYY-MM-DD."}

    project_raw = args.get("project_name_or_id")
    project_id = await resolve_project_id(project_raw)
    if project_raw and project_id is None:
        return {"error": f"No project matching '{project_raw}' was found."}

    return await reports.status_by_member(
        week_start_date=week_start_date, project_id=project_id
    )


async def _tool_get_member_profile(args: dict, *, reports: ReportService) -> dict:
    member_raw = args.get("member_name_or_id")
    user_id = await _resolve_user_id(member_raw)
    if user_id is None:
        return {"error": f"No team member matching '{member_raw}' was found."}

    profile = await reports.member_profile(user_id)
    return {"user_name": profile["user"].name, "stats": profile["stats"]}


_Handler = Callable[..., Awaitable[dict]]

_DISPATCH: dict[str, _Handler] = {
    "list_projects": _tool_list_projects,
    "get_team_activity": _tool_get_team_activity,
    "get_workload_and_hours": _tool_get_workload_and_hours,
    "get_submission_status": _tool_get_submission_status,
    "get_member_profile": _tool_get_member_profile,
}


async def run_tool(name: str, args: dict[str, Any], *, reports: ReportService) -> dict:
    """Execute one model-requested tool call and return a JSON-ready result.

    Never raises: a bad tool name or bad arguments come back as
    ``{"error": ...}`` so the model can see the problem and retry or explain
    it, instead of the whole chat turn failing.
    """
    handler = _DISPATCH.get(name)
    if handler is None:
        return {"error": f"Unknown tool '{name}'."}
    try:
        return await handler(args, reports=reports)
    except Exception as exc:  # boundary: args come from model-generated JSON
        return {"error": f"Tool '{name}' failed: {exc}"}


# ---------------------------------------------------------------------------
# OpenAI tool schema
# ---------------------------------------------------------------------------
TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "list_projects",
            "description": (
                "List active projects/teams and their member counts. Use this "
                "to check what a project or team is actually called before "
                "calling another tool with it."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_team_activity",
            "description": (
                "Get the detailed content of submitted weekly reports - "
                "completed tasks, blockers, achievements, and next-week plans "
                "- for a date range, optionally narrowed to one project/team "
                "or one team member. Use this for questions like 'what did "
                "the design team work on last week' or to gather material for "
                "a summary."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_name_or_id": {
                        "type": "string",
                        "description": "Project/team name. Omit for every project.",
                    },
                    "member_name_or_id": {
                        "type": "string",
                        "description": "Team member's name. Omit for the whole team.",
                    },
                    "date_from": {
                        "type": "string",
                        "description": "Start date (inclusive), format YYYY-MM-DD.",
                    },
                    "date_to": {
                        "type": "string",
                        "description": "End date (inclusive), format YYYY-MM-DD.",
                    },
                },
                "required": ["date_from", "date_to"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_workload_and_hours",
            "description": (
                "Get task/hours workload distribution across projects and "
                "hours-by-activity-type totals for a date range. Use this to "
                "spot workload imbalances between people or projects."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_name_or_id": {"type": "string"},
                    "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                    "date_to": {"type": "string", "description": "YYYY-MM-DD"},
                },
                "required": ["date_from", "date_to"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_submission_status",
            "description": (
                "Get per-member report submission status (not started, draft, "
                "submitted, needs correction, approved) for one specific week. "
                "Use this for questions like 'who hasn't submitted yet'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "week_start_date": {
                        "type": "string",
                        "description": "The Monday that starts the week, YYYY-MM-DD.",
                    },
                    "project_name_or_id": {"type": "string"},
                },
                "required": ["week_start_date"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_member_profile",
            "description": (
                "Get one team member's all-time stats: total reports, "
                "approval rate, tasks completed, hours logged."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "member_name_or_id": {"type": "string"},
                },
                "required": ["member_name_or_id"],
                "additionalProperties": False,
            },
        },
    },
]
