# Weekly Report Generator — Backend

**Requirement 1: User Authentication & Roles** — a self-contained FastAPI + MongoDB
module providing registration, JWT login/refresh/logout, bcrypt password hashing,
stateless bearer-token sessions, and role-based access control.

> Scope note: report-management endpoints and any frontend are intentionally **not**
> included here.

---

## Stack

| Concern            | Choice                                              |
|--------------------|----------------------------------------------------|
| Package manager    | [`uv`](https://docs.astral.sh/uv/)                  |
| Framework          | FastAPI + Pydantic v2                               |
| Database           | MongoDB via Motor + Beanie ODM                      |
| Password hashing   | `passlib` (bcrypt)                                  |
| Tokens             | `PyJWT` (HS256), access + refresh                   |
| Logout             | Server-side `jti` denylist with a MongoDB TTL index |

## Project layout

```
app/
├── core/
│   ├── config.py        # env-driven Settings (pydantic-settings)
│   └── security.py      # password hashing + JWT create/verify
├── models/
│   ├── __init__.py      # document_models registry (bound by init_beanie)
│   ├── user.py          # Beanie documents: User, RevokedToken
│   └── project.py       # Beanie document: Project
├── schemas/
│   ├── auth.py          # auth / user request/response Pydantic models
│   └── project.py       # project request/response Pydantic models
├── repositories/
│   └── project_repository.py  # project data-access layer
├── services/
│   └── project_service.py     # project business rules + domain errors
├── db/
│   └── session.py       # Motor client + init_beanie lifecycle
├── api/
│   ├── deps.py          # get_current_user, require_roles(...)
│   └── v1/
│       ├── auth.py      # /register /login /refresh /logout /me
│       ├── users.py     # /users/  /users/{id}  /users/{id}/role  /users/{id}/status
│       └── projects.py  # /projects/  /projects/{id}  /projects/{id}/members   (GET all roles; write ops Manager+Admin)
└── main.py              # app factory, lifespan, CORS, /health
tests/                   # pytest suite (in-memory MongoDB)
```

## Setup

```bash
uv sync                       # create .venv and install deps (incl. dev group)
cp .env.example .env          # then edit JWT_SECRET_KEY and MONGODB_URI
```

You need a reachable MongoDB.

### MongoDB Atlas (hosted)

1. Create a free **M0** cluster at <https://cloud.mongodb.com>.
2. **Database Access** → add a database user (username + password).
3. **Network Access** → add your current IP (or `0.0.0.0/0` for quick testing only).
4. **Connect → Drivers** → copy the `mongodb+srv://...` connection string.
5. Put it in `.env`, URL-encoding any special characters in the password
   (`@`→`%40`, `:`→`%3A`, `/`→`%2F`, `#`→`%23`):

   ```
   MONGODB_URI="mongodb+srv://appuser:My%40Pass@cluster0.abcde.mongodb.net/?retryWrites=true&w=majority&appName=cluster0"
   MONGODB_DB_NAME="weekly_report"
   ```

`mongodb+srv://` URIs resolve via DNS SRV records (`dnspython`) and always use
TLS; the app pins `certifi`'s CA bundle automatically so the handshake works on
stock Windows/macOS Python. On startup the app pings the cluster and, if it is
unreachable, exits with a one-line reason (bad URI, IP not allow-listed, wrong
credentials) instead of a driver traceback.

### Local MongoDB (alternative)

```bash
docker run -d --name mongo -p 27017:27017 mongo:7
# then in .env:  MONGODB_URI="mongodb://localhost:27017"
```

## Run

```bash
uv run uvicorn app.main:app --reload
```

- Interactive docs: <http://127.0.0.1:8000/docs>
- Health probe: <http://127.0.0.1:8000/health> → `{"status":"ok"}`

## Tests

```bash
uv run pytest
```

The suite runs the API against an in-memory MongoDB (`mongomock-motor`) — no
running database required.

---

## Configuration (`.env`)

| Variable                     | Default                       | Notes                                            |
|------------------------------|-------------------------------|--------------------------------------------------|
| `MONGODB_URI`                | `mongodb://localhost:27017`   | Local URI or Atlas `mongodb+srv://...` string    |
| `MONGODB_DB_NAME`            | `weekly_report`               | Database name (created on first write)           |
| `MONGODB_SERVER_SELECTION_TIMEOUT_MS` | `5000`              | How long to wait for a server before failing     |
| `JWT_SECRET_KEY`             | *(change me)*                 | HS256 signing key — use ≥ 64 random chars        |
| `JWT_ALGORITHM`              | `HS256`                       |                                                  |
| `ACCESS_TOKEN_EXPIRE_MINUTES`| `15`                          | Access-token lifetime                            |
| `REFRESH_TOKEN_EXPIRE_DAYS`  | `7`                           | Refresh-token lifetime                           |
| `BOOTSTRAP_ADMIN_EMAILS`     | *(empty)*                     | Comma-separated; see below                       |
| `CORS_ALLOW_ORIGINS`         | `*`                           | Comma-separated origins (`*` = all, dev only)    |

### Bootstrapping the first Admin

Self-registration **always** creates a `Team Member`; a client cannot request a
privileged role. To seed the first `Admin`, list their email in
`BOOTSTRAP_ADMIN_EMAILS`:

- on **registration** with that email → the account is created as `Admin`;
- on **application startup** → any existing account with that email is promoted.

From there, an Admin promotes others via `PATCH /users/{id}/role`.

---

## Endpoints

Base prefix: `/api/v1`

### Auth (`/auth`)

| Method & path        | Auth        | Description                                       | Errors            |
|----------------------|-------------|--------------------------------------------------|-------------------|
| `POST /auth/register`| public      | Register `{name, email, password}` → `201` user  | `400` dup email   |
| `POST /auth/login`   | public      | OAuth2 form (`username`=email) → access+refresh   | `401`, `403`      |
| `POST /auth/refresh` | public      | `{refresh_token}` → new access token              | `401`             |
| `POST /auth/logout`  | bearer      | Revoke current access token (+ optional refresh)  | `401`             |
| `GET  /auth/me`      | bearer      | Current user's profile                            | `401`, `403`      |

### Users (`/users`)

| Method & path                 | Auth                | Description                       | Errors                  |
|-------------------------------|---------------------|----------------------------------|-------------------------|
| `GET  /users/`                | Manager, Admin      | List users (`skip/limit/role`)   | `401`, `403`            |
| `GET  /users/{id}`            | self OR Mgr/Admin   | One user record                  | `401`, `403`, `404`     |
| `PATCH /users/{id}/role`      | Admin               | `{role}` — reassign role         | `400` self, `403`, `404`|
| `PATCH /users/{id}/status`    | Manager, Admin      | `{status}` — enable/disable      | `400` self, `403`, `404`|

### RBAC matrix

| Endpoint                  | Team Member    | Manager | Admin |
|---------------------------|----------------|---------|-------|
| `GET /users/`             | ❌ 403         | ✅      | ✅    |
| `GET /users/{id}`         | own id only    | ✅ any  | ✅ any|
| `PATCH /users/{id}/role`  | ❌ 403         | ❌ 403  | ✅    |
| `PATCH /users/{id}/status`| ❌ 403         | ✅      | ✅    |

---

## Quick smoke test (curl)

```bash
BASE=http://127.0.0.1:8000/api/v1

# 1. Admin (email must be in BOOTSTRAP_ADMIN_EMAILS)
curl -s -X POST $BASE/auth/register -H 'Content-Type: application/json' \
  -d '{"name":"Boss","email":"admin@example.com","password":"password123"}'

# 2. Regular member
curl -s -X POST $BASE/auth/register -H 'Content-Type: application/json' \
  -d '{"name":"Mia","email":"mia@example.com","password":"password123"}'

# 3. Login as admin (note: form-encoded, username = email)
ADMIN_TOKEN=$(curl -s -X POST $BASE/auth/login \
  -d 'username=admin@example.com&password=password123' | jq -r .access_token)

# 4. List all users (Admin/Manager only)
curl -s $BASE/users/ -H "Authorization: Bearer $ADMIN_TOKEN"

# 5. Promote Mia to Manager  (grab her id from step 4)
curl -s -X PATCH $BASE/users/<MIA_ID>/role \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' -d '{"role":"Manager"}'

# 6. Logout — the access token is now denylisted
curl -s -X POST $BASE/auth/logout -H "Authorization: Bearer $ADMIN_TOKEN"
curl -s -o /dev/null -w '%{http_code}\n' $BASE/auth/me \
  -H "Authorization: Bearer $ADMIN_TOKEN"   # -> 401
```
