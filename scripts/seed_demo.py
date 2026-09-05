"""Wipe every collection and reseed the database with realistic demo data.

Usage:
    uv run python scripts/seed_demo.py            # prompts before wiping
    uv run python scripts/seed_demo.py --yes      # no prompt (CI / scripted)

What it does:
    * Clears ``users``, ``token_denylist``, ``projects`` and ``reports`` first,
      so the script is safe to run repeatedly - every run leaves the database in
      the same shape, with no duplicate or orphaned documents.
    * Inserts **well over 20** documents into every collection (31 users,
      26 projects, 70+ reports, 28 denylisted tokens).
    * Wires every reference by object id *after* the parent rows exist, so all
      ``member_ids`` / ``user_id`` / ``project_id`` / ``reviewed_by_id`` /
      ``manager_id`` values point at a real document.
    * Spreads reports across 12 recent weeks and all four workflow statuses, with
      one "focus week" (the current week) populated across many members so the
      team dashboard, insights charts, activity feed and member-profile views all
      have something meaningful to show. The volume and spread are enough to
      demonstrate pagination, search, filtering and every dashboard.
    * Creates two fixed demo logins that work with authentication and RBAC:
          Manager      manager@example.com / Manager@123
          Team Member  member@example.com  / Member@123

It talks to whatever ``MONGODB_URI`` in ``.env`` points at - make sure that is a
database you are happy to wipe.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.session import close_db, init_db  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.models.report import (  # noqa: E402
    Achievement,
    Blocker,
    HoursWorkedBreakdown,
    Report,
    ReportStatus,
    ReportTask,
    ReportVersion,
    ReviewComment,
    TaskPriority,
    TaskStatus,
)
from app.models.user import RevokedToken, Role, User, UserStatus  # noqa: E402

SEED = 20250903
NUM_REVOKED_TOKENS = 28
NUM_WEEKS = 12

# The system has only two roles: Team Member and Manager. Manager is the fully
# privileged ("admin") role.
DEMO_PASSWORD = "Password@123"          # every non-fixed seeded account
MANAGER_EMAIL = "manager@example.com"   # fixed demo Manager login
MANAGER_PASSWORD = "Manager@123"
MEMBER_EMAIL = "member@example.com"     # fixed demo Team Member login
MEMBER_PASSWORD = "Member@123"
EMAIL_DOMAIN = "weeklyreport.dev"

rng = random.Random(SEED)


# ---------------------------------------------------------------------------
# Static content pools - curated so the data reads as a real engineering team
# ---------------------------------------------------------------------------
# Index 0 is the fixed demo Manager (manager@example.com); the rest sign in with
# ``<first.last>@weeklyreport.dev`` / DEMO_PASSWORD.
MANAGER_NAMES = [
    "Priya Menon",
    "Grace Hopper",
    "Alan Turing",
    "Katherine Johnson",
    "Linus Torvalds",
    "Barbara Liskov",
    "Vint Cerf",
]

# Index 0 is the fixed demo Team Member (member@example.com). The names in
# ``DISABLED_MEMBER_NAMES`` are seeded with a ``disabled`` status so the user
# list's status filter has something to show.
MEMBER_NAMES = [
    "Sam Carter",
    "Ada Lovelace",
    "Margaret Hamilton",
    "Dennis Ritchie",
    "Radia Perlman",
    "Ken Thompson",
    "Guido van Rossum",
    "Shafi Goldwasser",
    "Frances Allen",
    "Tim Berners-Lee",
    "Karen Sparck Jones",
    "Bjarne Stroustrup",
    "Donald Knuth",
    "Leslie Lamport",
    "Edsger Dijkstra",
    "John McCarthy",
    "Marvin Minsky",
    "Claude Shannon",
    "Douglas Engelbart",
    "Alan Kay",
    "Niklaus Wirth",
    "Peter Naur",
    "Sophie Wilson",
    "Hedy Lamarr",
]
DISABLED_MEMBER_NAMES = {"Peter Naur", "Sophie Wilson", "Hedy Lamarr"}

PROJECTS = [
    ("Apollo Billing Platform", "Rebuild of the subscription billing and invoicing service."),
    ("Helios Analytics", "Self-serve product analytics and funnel reporting."),
    ("Orion Mobile App", "Cross-platform mobile client for iOS and Android."),
    ("Atlas Data Warehouse", "Consolidated warehouse and nightly ETL pipelines."),
    ("Nimbus Cloud Migration", "Lift-and-reshape of on-prem workloads onto managed cloud."),
    ("Phoenix CRM Revamp", "Modernisation of the sales and support CRM."),
    ("Titan Payments Gateway", "PCI-scoped card and wallet payment processing."),
    ("Aurora Design System", "Shared component library and design tokens."),
    ("Pegasus Search Service", "Full-text and vector search across the catalogue."),
    ("Kraken Message Queue", "Internal event bus and async job infrastructure."),
    ("Vulcan CI/CD Pipeline", "Build, test and progressive-delivery tooling."),
    ("Mercury Notifications", "Email, push and in-app notification delivery."),
    ("Neptune Reporting Engine", "Scheduled and ad-hoc report generation."),
    ("Zephyr Marketing Site", "Public marketing website and CMS."),
    ("Cerberus Auth Service", "Central identity, SSO and session management."),
    ("Hydra API Gateway", "Edge routing, rate limiting and request auth."),
    ("Lyra Recommendation Engine", "Personalised recommendations and ranking."),
    ("Draco Fraud Detection", "Real-time transaction risk scoring."),
    ("Sol Customer Portal", "Authenticated self-service customer dashboard."),
    ("Gaia Sustainability Tracker", "Carbon and resource-usage reporting for customers."),
    ("Chronos Scheduler", "Cron and workflow orchestration for batch jobs."),
    ("Boreas Log Pipeline", "Centralised log ingestion, parsing and retention."),
    ("Selene Feature Flags", "Runtime flagging and staged rollouts."),
    ("Tethys Backup Service", "Automated backups and point-in-time restore."),
    ("Iris Localization", "Translation management and locale delivery."),
    ("Rhea Billing Reconciliation", "Nightly ledger and payout reconciliation."),
]
# Projects that are archived / wound down (still referenced by historic reports).
INACTIVE_PROJECT_INDEXES = {9, 13, 19, 22, 25}

TASK_NAMES = [
    "Implement JWT refresh-token rotation",
    "Fix N+1 query in the report-listing endpoint",
    "Add pagination to the team-dashboard API",
    "Write integration tests for the review workflow",
    "Migrate the users collection to the new schema",
    "Set up the GitHub Actions deploy workflow",
    "Refactor the project service onto the repository pattern",
    "Add rate limiting to the auth endpoints",
    "Build the weekly-report PDF export",
    "Investigate MongoDB connection-pool exhaustion",
    "Design the insights-dashboard wireframes",
    "Implement soft-delete for projects",
    "Harden email validation on signup",
    "Optimise the hours-by-type aggregation pipeline",
    "Wire up the recent-activity feed component",
    "Patch the stored-XSS risk in the notes field",
    "Upgrade Beanie and Motor to the latest minor",
    "Add role-based access-control test coverage",
    "Add OpenAPI examples to every endpoint",
    "Cache the manager dashboard summary response",
    "Add a status filter to the user-list endpoint",
    "Instrument request latency with structured logs",
    "Write the on-call runbook for the auth service",
    "Add a full-text search index to reports",
    "Backfill week-start dates on legacy reports",
    "Split the monolith settings module by concern",
    "Add a health check for the Mongo connection",
    "Document the review-workflow state machine",
]

DELIVERABLES = [
    "PR #{n} merged to main",
    "Deployed to staging, behind the `insights` flag",
    "Design doc circulated for review",
    "Runbook updated in the team wiki",
    "Dashboard screenshot shared in #eng-updates",
    "Load-test results attached to WRG-{n}",
    "Feature flag enabled for 10% of traffic",
    None,
    None,
]

BLOCKERS = [
    "Waiting on design sign-off for the dashboard layout.",
    "Atlas IP allow-list is blocking the CI test runs.",
    "Blocked on the infra team to provision the staging queue.",
    "A flaky test in the review-workflow suite needs triage.",
    "Requirements for the late-submission rule are still unclear.",
    "The Beanie upgrade breaks the mongomock test double.",
    "Need a production data sample to reproduce the aggregation bug.",
    "Pending a security review before the auth change can ship.",
    "Waiting on a code review that has been open for four days.",
    "The shared staging database keeps getting wiped by another team.",
    "Blocked on a licence decision for the PDF rendering library.",
]

ACHIEVEMENTS = [
    "Shipped the review workflow end to end.",
    "Cut report-list latency from 800ms to 120ms.",
    "Reached 90% test coverage on the service layer.",
    "Completed the Atlas migration with zero downtime.",
    "Delivered the insights-dashboard MVP.",
    "Automated the release pipeline.",
    "Closed out every P1 bug for the sprint.",
    "Onboarded two new engineers to the codebase.",
    "Reduced the nightly ETL run from 3h to 40min.",
    "Rolled out RBAC tests across every endpoint.",
    "Removed 1,200 lines of dead code from the report module.",
    "Got the mean review turnaround under 24 hours.",
]

PLANS_NEXT_WEEK = [
    "Finish the PDF export, start the notifications service, and pair with QA on regression coverage.",
    "Roll out the RBAC tests, review the aggregation refactor PR, and draft the Q3 tech-debt plan.",
    "Break down the migration epic, spike the search re-index, and update the on-call runbook.",
    "Land the rate-limiting change, triage the flaky suite, and demo the dashboard at sprint review.",
    "Wrap the API-gateway routing work and begin load-testing the payments path.",
    "Address review feedback, re-submit the report, and start on the caching layer.",
    "Pick up the notifications retries, close the XSS ticket, and groom the backlog.",
    "Finish the search index rollout and start on the export-to-CSV feature.",
    "Cut the release, monitor error rates, and write the post-mortem for last week's incident.",
]

REVIEW_COMMENTS = [
    "Please expand the blockers section - the infra dependency needs a clear owner and a date.",
    "Your hours breakdown doesn't match the total logged against tasks; please reconcile.",
    "Split the 'migration' task into the sub-tasks you actually shipped this week.",
    "Add links to the merged PRs under output/deliverable for each completed task.",
    "The key achievement should be the customer-facing win, not the internal refactor.",
    "Mark the API-gateway task as BLOCKED rather than IN_PROGRESS - it's waiting on infra.",
    "Next week's plan is too vague - break it into concrete, checkable items.",
    "Two tasks are at 100% actual but still marked IN_PROGRESS; flip them to COMPLETED.",
]

NOTES = [
    "See PR https://github.com/acme/weekly-report/pull/{n} for the full diff.",
    "Design doc: https://wiki.acme.internal/x/WRG-{n}",
    "Follow-up items captured on the sprint retro board.",
    "Metrics before/after in the #eng-updates thread.",
    "Incident timeline: https://wiki.acme.internal/x/INC-{n}",
    None,
    None,
]


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _slug_email(name: str) -> str:
    handle = name.lower().replace(" ", ".").replace("'", "")
    return f"{handle}@{EMAIL_DOMAIN}"


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _at(d: date, *, days: int = 0, hours: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(
        days=days, hours=hours
    )


CURRENT_WEEK = _monday(date.today())
# newest first; index 0 is the "focus" week used across the dashboards
WEEKS = [CURRENT_WEEK - timedelta(weeks=i) for i in range(NUM_WEEKS)]


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------
def build_users() -> list[User]:
    """31 users: 7 Managers (index 0 = demo Manager) + 24 Team Members
    (index 0 = demo Member; three seeded disabled)."""
    demo_hash = hash_password(DEMO_PASSWORD)
    manager_hash = hash_password(MANAGER_PASSWORD)
    member_hash = hash_password(MEMBER_PASSWORD)
    base = _utcnow() - timedelta(days=210)

    def mk(name: str, email: str, role: Role, pw_hash: str, status: UserStatus, order: int) -> User:
        created = base + timedelta(days=order * 5, hours=rng.randint(0, 20))
        return User(
            name=name,
            email=email,
            hashed_password=pw_hash,
            role=role,
            status=status,
            created_at=created,
            updated_at=created,
        )

    users: list[User] = []
    order = 0
    for i, name in enumerate(MANAGER_NAMES):
        email = MANAGER_EMAIL if i == 0 else _slug_email(name)
        pw_hash = manager_hash if i == 0 else demo_hash
        users.append(mk(name, email, Role.MANAGER, pw_hash, UserStatus.ACTIVE, order))
        order += 1
    for i, name in enumerate(MEMBER_NAMES):
        email = MEMBER_EMAIL if i == 0 else _slug_email(name)
        pw_hash = member_hash if i == 0 else demo_hash
        status = (
            UserStatus.DISABLED
            if (i != 0 and name in DISABLED_MEMBER_NAMES)
            else UserStatus.ACTIVE
        )
        users.append(mk(name, email, Role.TEAM_MEMBER, pw_hash, status, order))
        order += 1
    return users


def build_projects(active_member_ids: list[str], demo_member_id: str) -> list[Project]:
    projects: list[Project] = []
    assigned: set[str] = set()
    for i, (name, description) in enumerate(PROJECTS):
        k = min(rng.randint(3, 7), len(active_member_ids))
        members = rng.sample(active_member_ids, k=k)
        assigned.update(members)
        created = _utcnow() - timedelta(days=170 - i * 5, hours=rng.randint(0, 20))
        projects.append(
            Project(
                name=name,
                description=description,
                is_active=i not in INACTIVE_PROJECT_INDEXES,
                member_ids=members,
                created_at=created,
                updated_at=created,
            )
        )

    # Guarantee every active team member sits on at least one project.
    for uid in active_member_ids:
        if uid not in assigned:
            projects[rng.randrange(len(projects))].member_ids.append(uid)

    # Put the demo Team Member on a healthy handful of *active* projects so their
    # own report history and project view are well populated.
    active_positions = [i for i in range(len(PROJECTS)) if i not in INACTIVE_PROJECT_INDEXES]
    for i in rng.sample(active_positions, k=6):
        if demo_member_id not in projects[i].member_ids:
            projects[i].member_ids.append(demo_member_id)
    return projects


def _make_tasks(report_status: ReportStatus) -> list[ReportTask]:
    if report_status is ReportStatus.APPROVED:
        weights = {TaskStatus.COMPLETED: 6, TaskStatus.IN_PROGRESS: 3, TaskStatus.BLOCKED: 1}
    elif report_status is ReportStatus.SUBMITTED:
        weights = {TaskStatus.COMPLETED: 4, TaskStatus.IN_PROGRESS: 4, TaskStatus.BLOCKED: 1}
    elif report_status is ReportStatus.NEEDS_CORRECTION:
        weights = {
            TaskStatus.COMPLETED: 3,
            TaskStatus.IN_PROGRESS: 3,
            TaskStatus.BLOCKED: 2,
            TaskStatus.NOT_STARTED: 1,
        }
    else:  # DRAFT
        weights = {
            TaskStatus.IN_PROGRESS: 4,
            TaskStatus.NOT_STARTED: 3,
            TaskStatus.COMPLETED: 1,
        }
    statuses = list(weights)
    weight_values = list(weights.values())

    tasks: list[ReportTask] = []
    for task_name in rng.sample(TASK_NAMES, k=rng.randint(2, 5)):
        status = rng.choices(statuses, weights=weight_values, k=1)[0]
        if status is TaskStatus.COMPLETED:
            actual = 100
        elif status is TaskStatus.IN_PROGRESS:
            actual = rng.choice([30, 40, 50, 60, 70, 80])
        elif status is TaskStatus.BLOCKED:
            actual = rng.choice([10, 20, 30, 40])
        else:
            actual = 0
        planned = rng.choice([80, 100, 100, 100])
        planned_hours = round(rng.uniform(3, 12), 1)
        spent_hours = round(planned_hours * rng.uniform(0.6, 1.4), 1)
        deliverable = rng.choice(DELIVERABLES)
        if deliverable is not None:
            deliverable = deliverable.format(n=rng.randint(120, 480))
        tasks.append(
            ReportTask(
                task_name=task_name,
                priority=rng.choice(list(TaskPriority)),
                planned_percentage=planned,
                actual_percentage=actual,
                status=status,
                time_planned_hours=planned_hours,
                time_spent_hours=spent_hours,
                output_deliverable=deliverable,
            )
        )
    return tasks


def _make_blockers(force: bool) -> list[Blocker]:
    if not force and rng.random() < 0.45:
        return []
    picked = rng.sample(BLOCKERS, k=rng.randint(1, 3))
    key_index = rng.randrange(len(picked))
    return [
        Blocker(text=text, is_key_issue=(idx == key_index))
        for idx, text in enumerate(picked)
    ]


def _make_achievements() -> list[Achievement]:
    picked = rng.sample(ACHIEVEMENTS, k=rng.randint(1, 3))
    key_index = rng.randrange(len(picked))
    return [
        Achievement(text=text, is_key_achievement=(idx == key_index))
        for idx, text in enumerate(picked)
    ]


def _make_hours() -> HoursWorkedBreakdown:
    return HoursWorkedBreakdown(
        development=round(rng.uniform(15, 28), 1),
        testing=round(rng.uniform(3, 10), 1),
        meetings=round(rng.uniform(3, 8), 1),
        documentation=round(rng.uniform(1, 5), 1),
        other=round(rng.uniform(0, 4), 1),
    )


def _note() -> str | None:
    note = rng.choice(NOTES)
    return note.format(n=rng.randint(120, 480)) if note else None


def _snapshot(
    report: Report, version: int, submitted_at: datetime, snapshot_at: datetime
) -> ReportVersion:
    return ReportVersion(
        version=version,
        snapshot_at=snapshot_at,
        submitted_at=submitted_at,
        status_at_snapshot=ReportStatus.SUBMITTED,
        week_start_date=report.week_start_date,
        week_end_date=report.week_end_date,
        tasks_planned_next_week=report.tasks_planned_next_week,
        tasks_completed=[t.model_copy(deep=True) for t in report.tasks_completed],
        blockers=[b.model_copy(deep=True) for b in report.blockers],
        achievements=[a.model_copy(deep=True) for a in report.achievements],
        hours_worked_breakdown=(
            report.hours_worked_breakdown.model_copy(deep=True)
            if report.hours_worked_breakdown is not None
            else None
        ),
        notes_or_links=report.notes_or_links,
    )


# Plan keys understood by :func:`_build_one_report`.
_PLAN_STATUS = {
    "approved": ReportStatus.APPROVED,
    "approved_with_history": ReportStatus.APPROVED,
    "submitted": ReportStatus.SUBMITTED,
    "submitted_late": ReportStatus.SUBMITTED,
    "needs_correction": ReportStatus.NEEDS_CORRECTION,
    "draft": ReportStatus.DRAFT,
}


def _weighted_plan(week_idx: int) -> str:
    """Pick a report plan for *week_idx* - recent weeks skew unfinished, older
    weeks skew resolved - so every status is well represented for filtering."""
    if week_idx == 0:
        table = {"draft": 3, "submitted": 5, "submitted_late": 1, "needs_correction": 2, "approved": 1}
    elif week_idx <= 2:
        table = {"submitted": 3, "needs_correction": 3, "approved": 4, "approved_with_history": 2, "draft": 1}
    else:
        table = {"approved": 6, "approved_with_history": 2, "needs_correction": 1, "draft": 1}
    return rng.choices(list(table), weights=list(table.values()), k=1)[0]


def _pick_project(
    uid: str, member_to_projects: dict[str, list[str]], active_project_ids: list[str]
) -> str:
    mine = member_to_projects.get(uid, [])
    active_mine = [p for p in mine if p in active_project_ids]
    return rng.choice(active_mine or mine or active_project_ids)


def _build_one_report(
    user: User,
    week_idx: int,
    plan: str,
    managers: list[User],
    reviewer: User | None,
    member_to_projects: dict[str, list[str]],
    active_project_ids: list[str],
    project_id: str | None = None,
) -> Report:
    uid = str(user.id)
    if project_id is None:
        project_id = _pick_project(uid, member_to_projects, active_project_ids)
    reviewer = reviewer or rng.choice(managers)
    base_status = _PLAN_STATUS[plan]

    week_start = WEEKS[week_idx]
    week_end = week_start + timedelta(days=6)

    created = _at(week_start, days=rng.randint(1, 3), hours=rng.randint(8, 18))
    report = Report(
        user_id=uid,
        project_id=project_id,
        week_start_date=week_start,
        week_end_date=week_end,
        status=ReportStatus.DRAFT,
        tasks_planned_next_week=rng.choice(PLANS_NEXT_WEEK),
        tasks_completed=_make_tasks(base_status),
        blockers=_make_blockers(force=plan in {"needs_correction", "submitted_late"}),
        achievements=_make_achievements(),
        hours_worked_breakdown=_make_hours(),
        notes_or_links=_note(),
        created_at=created,
        updated_at=created,
    )

    if plan == "draft":
        report.updated_at = created + timedelta(hours=rng.randint(2, 40))
        return report

    late_days = rng.randint(2, 4) if plan == "submitted_late" else rng.randint(-1, 0)
    submitted_at = _at(week_end, days=late_days, hours=rng.randint(9, 19))

    if plan in {"needs_correction", "approved_with_history"}:
        first_submitted = _at(week_end, days=rng.randint(-2, -1), hours=rng.randint(9, 17))
        reviewed_v1 = first_submitted + timedelta(days=rng.randint(1, 2), hours=rng.randint(1, 6))
        report.status = ReportStatus.SUBMITTED
        report.submitted_at = first_submitted
        report.version_history.append(_snapshot(report, 1, first_submitted, reviewed_v1))
        report.review_comments.append(
            ReviewComment(
                comment=rng.choice(REVIEW_COMMENTS),
                manager_id=str(reviewer.id),
                manager_name=reviewer.name,
                against_version=1,
                created_at=reviewed_v1,
            )
        )
        report.reviewed_at = reviewed_v1
        report.reviewed_by_id = str(reviewer.id)
        report.reviewed_by_name = reviewer.name

        if plan == "needs_correction":
            report.status = ReportStatus.NEEDS_CORRECTION
            report.updated_at = reviewed_v1 + timedelta(hours=rng.randint(2, 30))
            return report

        # approved_with_history: author fixed it and re-submitted, then approved -
        # sometimes after a second correction cycle.
        anchor = reviewed_v1
        if rng.random() < 0.4:
            resub_v2 = reviewed_v1 + timedelta(days=rng.randint(1, 2), hours=rng.randint(1, 6))
            reviewed_v2 = resub_v2 + timedelta(days=1, hours=rng.randint(1, 6))
            report.submitted_at = resub_v2
            report.version_history.append(_snapshot(report, 2, resub_v2, reviewed_v2))
            report.review_comments.append(
                ReviewComment(
                    comment=rng.choice(REVIEW_COMMENTS),
                    manager_id=str(reviewer.id),
                    manager_name=reviewer.name,
                    against_version=2,
                    created_at=reviewed_v2,
                )
            )
            anchor = reviewed_v2

        resubmitted = anchor + timedelta(days=rng.randint(1, 3), hours=rng.randint(1, 8))
        approved_at = resubmitted + timedelta(days=rng.randint(1, 2), hours=rng.randint(1, 6))
        report.status = ReportStatus.APPROVED
        report.submitted_at = resubmitted
        report.reviewed_at = approved_at
        report.reviewed_by_id = str(reviewer.id)
        report.reviewed_by_name = reviewer.name
        report.updated_at = approved_at
        return report

    report.status = base_status
    report.submitted_at = submitted_at
    report.updated_at = submitted_at

    if base_status is ReportStatus.APPROVED:
        approved_at = submitted_at + timedelta(days=rng.randint(1, 2), hours=rng.randint(1, 6))
        report.reviewed_at = approved_at
        report.reviewed_by_id = str(reviewer.id)
        report.reviewed_by_name = reviewer.name
        report.updated_at = approved_at

    return report


def build_reports(
    active_team_members: list[User],
    managers: list[User],
    demo_member: User,
    demo_manager: User,
    member_to_projects: dict[str, list[str]],
    active_project_ids: list[str],
) -> list[Report]:
    reports: list[Report] = []
    other_managers = [m for m in managers if m.id != demo_manager.id]

    def add(user: User, week_idx: int, plan: str, *, reviewer: User | None = None,
            project_id: str | None = None) -> None:
        reports.append(
            _build_one_report(
                user, week_idx, plan, managers, reviewer,
                member_to_projects, active_project_ids, project_id=project_id,
            )
        )

    # --- Fixed demo Team Member: a rich, varied history --------------------
    # Exercises "my reports" history + status filter, version history and the
    # review panel; the demo Manager is the reviewer throughout.
    dm_projects = [
        p for p in member_to_projects.get(str(demo_member.id), []) if p in active_project_ids
    ] or active_project_ids
    p_a, p_b = dm_projects[0], dm_projects[1 % len(dm_projects)]
    add(demo_member, 0, "draft", project_id=p_a)
    add(demo_member, 1, "needs_correction", reviewer=demo_manager, project_id=p_a)
    add(demo_member, 1, "approved", reviewer=demo_manager, project_id=p_b)  # 2nd project, same week
    add(demo_member, 2, "submitted", project_id=p_a)
    add(demo_member, 3, "approved_with_history", reviewer=demo_manager, project_id=p_a)
    add(demo_member, 6, "needs_correction", reviewer=demo_manager, project_id=p_b)
    add(demo_member, 8, "approved_with_history", reviewer=demo_manager, project_id=p_b)
    for wk in (4, 5, 7, 9, 10, 11):
        add(demo_member, wk, "approved", reviewer=demo_manager, project_id=p_a)

    # --- Fixed demo Manager also authors a couple of their own reports -----
    add(demo_manager, 0, "submitted")
    add(demo_manager, 3, "approved",
        reviewer=(rng.choice(other_managers) if other_managers else demo_manager))

    # --- The rest of the team, spread across every one of the 12 weeks ----
    others = [m for m in active_team_members if m.id != demo_member.id]
    for week_idx in range(len(WEEKS)):
        filed = rng.randint(6, 11) if week_idx <= 4 else rng.randint(2, 6)
        for user in rng.sample(others, k=min(filed, len(others))):
            reviewer = demo_manager if rng.random() < 0.55 else rng.choice(managers)
            add(user, week_idx, _weighted_plan(week_idx), reviewer=reviewer)

    return reports


def build_revoked_tokens(users: list[User]) -> list[RevokedToken]:
    tokens: list[RevokedToken] = []
    for i in range(NUM_REVOKED_TOKENS):
        user = users[i % len(users)]
        token_type = rng.choice(["access", "refresh"])
        revoked_at = _utcnow() - timedelta(
            days=rng.randint(0, 20), hours=rng.randint(0, 23)
        )
        # Future-dated so the collection's TTL index does not immediately reap
        # the demo rows (a denylist entry only matters until the token expires).
        expires_at = _utcnow() + timedelta(
            hours=rng.randint(2, 168) if token_type == "refresh" else rng.randint(2, 12)
        )
        tokens.append(
            RevokedToken(
                jti=f"{rng.getrandbits(128):032x}",
                user_id=str(user.id),
                token_type=token_type,
                expires_at=expires_at,
                revoked_at=revoked_at,
            )
        )
    return tokens


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
async def _seed() -> None:
    await init_db()
    try:
        for model in (Report, RevokedToken, Project, User):
            await model.delete_all()
        print("Cleared users, token_denylist, projects and reports.")

        users = build_users()
        await User.insert_many(users)
        users = await User.find_all().sort("+created_at").to_list()
        by_email = {u.email: u for u in users}
        demo_manager = by_email[MANAGER_EMAIL]
        demo_member = by_email[MEMBER_EMAIL]

        managers = [u for u in users if u.role is Role.MANAGER]
        active_team_members = [
            u
            for u in users
            if u.role is Role.TEAM_MEMBER and u.status is UserStatus.ACTIVE
        ]
        active_member_ids = [str(u.id) for u in active_team_members]

        projects = build_projects(active_member_ids, str(demo_member.id))
        await Project.insert_many(projects)
        projects = await Project.find_all().sort("+created_at").to_list()

        active_project_ids = [str(p.id) for p in projects if p.is_active]
        member_to_projects: dict[str, list[str]] = {}
        for project in projects:
            for uid in project.member_ids:
                member_to_projects.setdefault(uid, []).append(str(project.id))

        reports = build_reports(
            active_team_members, managers, demo_member, demo_manager,
            member_to_projects, active_project_ids,
        )
        await Report.insert_many(reports)

        tokens = build_revoked_tokens(users)
        await RevokedToken.insert_many(tokens)

        # Every collection must comfortably clear 20 documents.
        assert min(len(users), len(projects), len(reports), len(tokens)) > 20, (
            "a collection was seeded with 20 or fewer documents"
        )

        _print_summary(users, projects, reports, tokens)
    finally:
        await close_db()


def _print_summary(
    users: list[User],
    projects: list[Project],
    reports: list[Report],
    tokens: list[RevokedToken],
) -> None:
    by_status: dict[str, int] = {}
    for report in reports:
        by_status[report.status.value] = by_status.get(report.status.value, 0) + 1

    demo_member_reports = sum(
        1
        for r in reports
        for u in users
        if u.email == MEMBER_EMAIL and r.user_id == str(u.id)
    )

    print()
    print(f"Seeded database '{settings.mongodb_db_name}':")
    print(f"  users .............. {len(users)}  "
          f"({sum(u.role is Role.MANAGER for u in users)} manager, "
          f"{sum(u.role is Role.TEAM_MEMBER for u in users)} team member; "
          f"{sum(u.status is UserStatus.DISABLED for u in users)} disabled)")
    print(f"  projects ........... {len(projects)}  "
          f"({sum(not p.is_active for p in projects)} archived)")
    print(f"  reports ............ {len(reports)}  "
          + ", ".join(f"{k}:{v}" for k, v in sorted(by_status.items())))
    print(f"  token_denylist ..... {len(tokens)}")
    print()
    print(f"Focus week (dashboards default here): {CURRENT_WEEK.isoformat()}  "
          f"({NUM_WEEKS} weeks of history)")
    print(f"Demo Team Member has {demo_member_reports} reports across the history.")
    print()
    print("Demo logins (both work with authentication + RBAC):")
    print(f"  Manager      {MANAGER_EMAIL} / {MANAGER_PASSWORD}")
    print(f"  Team Member  {MEMBER_EMAIL} / {MEMBER_PASSWORD}")
    print(f"  Others       <first.last>@{EMAIL_DOMAIN} / {DEMO_PASSWORD}"
          f"  (e.g. {_slug_email('Ada Lovelace')})")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Wipe every collection and reseed with demo data."
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt (for scripted / CI use).",
    )
    args = parser.parse_args()

    if not args.yes:
        print(
            f"This will DELETE ALL DATA in database '{settings.mongodb_db_name}' "
            "and replace it with demo data."
        )
        if input("Type 'seed' to continue: ").strip() != "seed":
            sys.exit("Aborted - nothing was changed.")

    asyncio.run(_seed())


if __name__ == "__main__":
    main()
