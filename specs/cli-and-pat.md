# CLI Support: Personal Access Tokens & Port-Forwarding

## Overview

Backend API changes to support a CLI utility for NoVPS. Includes:

1. **Personal Access Tokens (PAT)** — authentication mechanism for CLI
2. **Port-Forwarding via WebSocket** — TCP proxy for databases and resources
3. **Public API endpoints** — API endpoints optimized for CLI usage (apps, resources, secrets, databases, registry, S3 buckets)

CLI utility itself is a separate project/repository and is **out of scope** for this spec.

---

## Part 1: Personal Access Tokens (PAT)

### 1.1 Model: `PersonalAccessToken`

File: `app/models.py`

```python
class PersonalAccessToken(Base):
    __tablename__ = 'personal_access_token'

    id: Mapped[uuid.UUID]          # UUID PK, default=uuid4
    user_id: Mapped[int]           # FK -> users.id
    project_id: Mapped[uuid.UUID]  # FK -> project.id
    name: Mapped[str]              # Text, not null — human-readable name
    token_hash: Mapped[str]        # Text, not null — Argon2 hash of the token
    token_prefix: Mapped[str]      # Text, not null — first 8 chars (base58) for identification
    expires_at: Mapped[datetime | None]  # DateTime(timezone=True), nullable — null means never expires
    last_used_at: Mapped[datetime | None]  # DateTime(timezone=True), nullable
    created_at: Mapped[datetime]   # DateTime(timezone=True), default=func.now()
    revoked_at: Mapped[datetime | None]  # DateTime(timezone=True), nullable
```

**Indexes:**
- `token_prefix` — regular index (for lookup)
- `(user_id, project_id)` — composite index (for counting tokens per project)

**Token format:** `nvps_{prefix}_{secret}` — prefix is 8 chars base58, secret is random. Shown to user only once at creation time. Stored as Argon2 hash. `token_prefix` stores the 8-char base58 prefix for identification/listing.

### 1.2 Migration

File: `migrations/versions/20260216_1911-9589602eec81.py`

- Create `personal_access_token` table
- Add indexes

### 1.3 CRUD: `PersonalAccessTokensCrud`

File: `app/crud/personal_access_tokens.py`

Methods:
- `find_by_id(token_id: UUID) -> PersonalAccessToken | None`
- `find_by_project_and_user(project: Project, user: User) -> list[PersonalAccessToken]` — active (non-revoked) tokens
- `count_by_project_and_user(project: Project, user: User) -> int` — count of active tokens
- `find_by_prefix(prefix: str) -> list[PersonalAccessToken]` — find active (non-revoked) tokens matching prefix for auth lookup

Registered in `app/crud/__init__.py`.

### 1.4 Authentication Flow

The CLI sends the PAT in the `Authorization` header: `nvps_...` (no `Bearer` prefix).

**Auth functions** in `app/auth.py`:

```python
async def get_user_by_token(...) -> User:
    # If token starts with "nvps_", delegates to _authenticate_pat()
    # Otherwise uses existing AccessToken auth

async def get_pat_user_and_pat(...) -> tuple[User, PersonalAccessToken]:
    # PAT-only auth, requires token starting with "nvps_"

async def _authenticate_pat(raw_token, pat_crud, users_crud, redis) -> tuple[User, PersonalAccessToken]:
    # Internal helper for PAT authentication
```

Logic:
1. Extract prefix from token (8 chars between first and second `_`)
2. Find all active PATs with matching prefix via `find_by_prefix()`
3. For each candidate, verify token using `hashing.verify(candidate.token_hash, raw_token)`
4. Check `expires_at is None or expires_at > now`
5. Return `(user, pat)`
6. Update `last_used_at` in Redis: key `pat_last_used:{pat.id}`, value = ISO timestamp, TTL = 5 min (300s)
7. A background job flushes `pat_last_used:*` keys to DB periodically

**Integration with existing auth:** `get_user_by_token` checks if the token starts with `nvps_`. If yes, delegates to `_authenticate_pat()`. Otherwise, uses existing AccessToken auth.

**Important:** PAT auth does NOT require S2S token. PAT-authenticated endpoints are registered without S2S dependency.

### 1.5 PAT Permissions

PATs inherit the user's role in the project. No additional scopes. RBAC works by resolving `ProjectUser` from user + PAT's `project_id`.

### 1.6 API Endpoints: PAT Management

These endpoints are **S2S-protected** (called from the web frontend).

File: `app/routing/tokens.py`

#### `POST /projects/{project_id}/tokens`

Create a new PAT. Returns the raw token **only once**.

Request body:
```json
{
  "name": "My CLI token",
  "expires_at": "2026-06-01T00:00:00Z"   // optional, null = never expires
}
```

Validation:
- `name`: 1-100 chars
- Count active tokens for this user+project <= 10
- `expires_at` must be in the future (if provided)

Response:
```json
{
  "data": {
    "id": "uuid",
    "name": "My CLI token",
    "token": "nvps_abcd1234_...",
    "token_prefix": "abcd1234",
    "expires_at": "2026-06-01T00:00:00Z",
    "created_at": "2026-02-16T12:00:00Z"
  },
  "errors": null
}
```

#### `GET /projects/{project_id}/tokens`

List user's PATs for this project.

Response:
```json
{
  "data": [
    {
      "id": "uuid",
      "name": "My CLI token",
      "token_prefix": "abcd1234",
      "expires_at": "2026-06-01T00:00:00Z",
      "last_used_at": "2026-02-15T10:30:00Z",
      "created_at": "2026-02-16T12:00:00Z"
    }
  ],
  "errors": null
}
```

Note: `last_used_at` is read from Redis first (key `pat_last_used:{id}`), falling back to DB value.

#### `DELETE /projects/{project_id}/tokens/{token_id}`

Revoke a PAT. Sets `revoked_at = now()`.

Response:
```json
{
  "data": {},
  "errors": null
}
```

RBAC: token owner or project owner.

### 1.7 Pydantic Schemas

File: `app/schema.py`

```python
class CreatePersonalAccessTokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def validate_expires_at(cls, value):
        if value is not None and value <= utcnow():
            raise ValueError("expires_at must be in the future")
        return value
```

### 1.8 Background Job: Flush PAT Last Used

File: `app/jobs/flush_pat_last_used.py`

- Class `FlushPatLastUsed` inherits `Command`
- Scans Redis keys `pat_last_used:*`
- For each key, updates `PersonalAccessToken.last_used_at` in DB
- Deletes the Redis key after successful DB update

Celery task in `app/tasks.py`:
```python
@app.task(queue="audit_log")
def flush_pat_last_used():
    ...
```

Console command: `flush-pat-last-used` (registered in `console.py`).

---

## Part 2: Port-Forwarding via WebSocket

### 2.1 Architecture

```
CLI (localhost:XXXX) <-- TCP --> CLI WebSocket Client <-- WS --> Backend WebSocket Server <-- k8s port-forward --> Pod
```

1. CLI authenticates via PAT, creates a port-forward ticket (`POST /port-forward/ticket`)
2. CLI connects to WebSocket with the ticket
3. Backend proxies binary data bidirectionally between WebSocket and k8s pod
4. Session auto-closes after **1 hour**
5. Max **5 concurrent** port-forward sessions per project

### 2.2 Ticket Creation Endpoint

File: `app/routing/websocket.py` (served via `websocket_server.py`, separate service)

#### `POST /port-forward/ticket`

Auth: PAT (`get_pat_user_and_pat` from `app/auth.py`).

Request body:
```json
{
  "target_type": "resource",      // "resource" | "database"
  "target_id": "uuid",
  "port": 8080                     // required for resources, ignored for databases
}
```

Validation:
- User must be an accepted member of the project (from PAT's `project_id`)
- For resources: user must have `Permission.RESOURCES_PORT_FORWARD` (app-level RBAC)
- For resources: `port` must be provided and match `config_port` or `config_internal_ports`
- For databases: user must have `Permission.DATABASES_SHOW_SECRETS`
- For databases: status must be `"created"`
- Concurrent sessions: `SCARD port_forward_sessions:{project_id}` must be < 5

Response:
```json
{
  "data": {
    "ticket": "random-64-char-string",
    "websocket_path": "/port-forward/resources/{id}?ticket=...&port=8080",
    "expires_in": 60
  },
  "errors": null
}
```

The ticket is stored in Redis: `port_forward_ticket:{ticket}` -> JSON `{"user_id", "project_id", "target_type", "target_id", "port"}`, TTL = 60 seconds. One-time use (deleted on consumption).

Schema (`app/schema.py`):
```python
class CreatePortForwardTicketRequest(BaseModel):
    target_type: Literal["resource", "database"]
    target_id: UUID
    port: int | None = None  # required for resources, ignored for databases
```

### 2.3 WebSocket Endpoints

File: `app/routing/websocket.py`

#### `WS /port-forward/resources/{resource_id}`

Query params:
- `ticket` — one-time ticket
- `port` — the target port to forward (validated against ticket)

#### `WS /port-forward/databases/{database_id}`

Query params:
- `ticket` — one-time ticket

Port is determined from the ticket payload:
- `postgres` engine -> `5432`
- `redis` engine -> `6379`

### 2.4 Port Validation for Resources

For resources, the `port` in the ticket request must be one of:
- `resource.config_port` (the HTTP port)
- Any port from `resource.config_internal_ports` list

If the port is not in the allowed list, the ticket creation returns HTTP 400.

During WebSocket connection, the port is verified against the ticket payload. Mismatch results in close code `4003`.

### 2.5 WebSocket Handler: `PortForwardHandler`

File: `app/services/kubernetes/port_forward.py`

```python
from kubernetes.stream import portforward

class PortForwardHandler:
    def __init__(self, kube_client, pod_name: str, namespace: str, port: int, websocket: WebSocket):
        ...

    def start(self):
        """Start bidirectional proxy using daemon threads"""
        self.resp = portforward(
            self.kube_client.connect_get_namespaced_pod_portforward,
            name=self.pod_name, namespace=self.namespace, ports=str(self.port),
        )
        self.pf_socket = self.resp.socket(self.port)
        # Start read_thread and write_thread (daemon=True)

    def stop(self):
        """Close port-forward connection and socket"""

    def send_data(self, data: bytes):
        """Queue binary data to be written to pod"""
```

Key differences from `PodExecHandler`:
- Binary data (`websocket.send_bytes` / `websocket.receive_bytes`), not text
- Uses `portforward()` + `resp.socket(port)` instead of `stream()`
- No stdin/stdout/stderr channels — raw TCP socket
- Read thread: `pf_socket.recv(4096)` -> `websocket.send_bytes()`
- Write thread: `data_queue.get()` -> `pf_socket.sendall()`

Exported from `app/services/kubernetes/__init__.py`.

### 2.6 Session Timeout

- Max session duration: **1 hour** (3600 seconds)
- Implementation: `asyncio.wait_for()` wrapping `websocket.receive_bytes()` with full timeout
- On timeout: send WebSocket close frame with code `4008` and reason `"Session timeout"`

### 2.7 Pod Selection

**For resources:**
- List pods with label `novps/resource_id={resource_id}`
- Pick the first pod with `status.phase == "Running"`
- If no running pods, close WebSocket with code `4004`

**For databases:**
- Postgres: list pods with label `cnpg.io/cluster={database.name}`, pick the one where `labels["cnpg.io/instanceRole"] == "primary"` and `status.phase == "Running"`
- Redis: list pods with label `novps/database_id={database_id}`, exclude pods with label `novps/type=proxy`, pick first with `status.phase == "Running"`

### 2.8 RBAC

Permission: `Permission.RESOURCES_PORT_FORWARD = "resources.port-forward"`

Granted to: `owner` (implicit), `maintainer`, `modifier`.

For databases: reuses `Permission.DATABASES_SHOW_SECRETS` (already available to owner, maintainer, modifier).

### 2.9 Concurrent Session Tracking

- Redis set: `port_forward_sessions:{project_id}`
- On connect: `SADD` session_id (format: `{target_id}:{uuid4()}`), check `SCARD` <= 5
- On disconnect: `SREM` session_id (in `finally` block)
- Also checked during ticket creation to provide early feedback

### 2.10 WebSocket Protocol

Binary framing for port-forward (unlike exec which uses text):

- **Client -> Server:** raw TCP data as binary WebSocket frames
- **Server -> Client:** raw TCP data as binary WebSocket frames
- **Control messages:** WebSocket close frames with specific codes

Close codes:
| Code | Meaning |
|------|---------|
| 4003 | Port not allowed / port mismatch |
| 4004 | No running pods found |
| 4005 | Database not ready |
| 4008 | Session timeout |
| 4010 | Authentication failed / invalid ticket |

---

## Part 3: Public API Router Group

### 3.1 Router Group

File: `app/routing/public_api/`

The public API router is **NOT** S2S-protected. Uses PAT authentication via `get_pat_user_and_pat()`.

Registered in `main.py` without S2S dependency.

### 3.2 Auth Dependencies

File: `app/routing/public_api/_deps.py`

- `get_current_user` — extracts `User` from PAT auth tuple
- `get_current_pat` — extracts `PersonalAccessToken` from PAT auth tuple
- `get_current_project` — resolves project from PAT's `project_id`
- `get_current_project_user` — resolves `ProjectUser` (must be accepted)
- `require_permission(action)` — RBAC check via `RBACService` directly

### 3.3 Endpoints

All endpoints return data in `{"data": ..., "errors": null}` format. All require PAT auth. Project is determined from PAT's `project_id` (no `{project_id}` in URL).

#### `GET /public-api/apps`

List applications in the project. RBAC: `APPS_READ`

#### `GET /public-api/apps/{app_id}/resources`

List resources for an application. RBAC: `APPS_READ`

#### `GET /public-api/apps/{app_id}/secrets?include_values=false`

List environment variable keys for an application. RBAC: `APPS_SHOW_SECRETS` (per-app)

#### `GET /public-api/apps/{app_id}/secrets/{secret_id}`

Get single secret with value. RBAC: `APPS_SHOW_SECRETS` (per-app)

#### `GET /public-api/apps/{app_id}/resources/{resource_id}/secrets?include_values=false`

List resource-level secrets. RBAC: `APPS_SHOW_SECRETS` (per-app)

#### `GET /public-api/databases`

List databases in the project. RBAC: `PROJECT_READ`

#### `GET /public-api/registry`

List registry namespaces. RBAC: `PROJECT_READ`

#### `GET /public-api/storage`

List S3 buckets. RBAC: `STORAGE_READ_BUCKET`

---

## Part 4: Implementation Status

### Phase 1: Personal Access Tokens — DONE

1. Model `PersonalAccessToken` in `app/models.py`
2. Migration: `migrations/versions/20260216_1911-9589602eec81.py`
3. CRUD: `app/crud/personal_access_tokens.py`
4. Schema: `CreatePersonalAccessTokenRequest` in `app/schema.py`
5. Auth: `_authenticate_pat()`, `get_pat_user_and_pat()` in `app/auth.py`; `get_user_by_token()` extended
6. PAT management endpoints: `app/routing/tokens.py` (S2S-protected)
7. Background job: `app/jobs/flush_pat_last_used.py`, Celery task on `audit_log` queue
8. Console command: `flush-pat-last-used`

### Phase 2: Public API & List Endpoints — DONE

9. Public API router: `app/routing/public_api/` with `_deps.py` for auth/RBAC
10. All list endpoints implemented (apps, resources, secrets, databases, registry, storage)

### Phase 3: Port-Forwarding — DONE

11. RBAC: `Permission.RESOURCES_PORT_FORWARD` added, assigned to maintainer + modifier
12. Schema: `CreatePortForwardTicketRequest` in `app/schema.py`
13. `PortForwardHandler` in `app/services/kubernetes/port_forward.py`
14. Ticket endpoint: `POST /port-forward/ticket` in `app/routing/websocket.py`
15. WebSocket endpoints: `WS /port-forward/resources/{resource_id}`, `WS /port-forward/databases/{database_id}`
16. Concurrent session tracking via Redis set, max 5 per project
17. 1-hour auto-close timeout

---

## Decisions Made

1. **Token format:** `nvps_{prefix}_{secret}` — prefix is 8 chars base58 (for lookup), secret is random. Full token hashed with Argon2.

2. **Concurrent port-forward sessions:** Max 5 concurrent sessions per project, tracked via Redis set `port_forward_sessions:{project_id}`.

3. **PAT in CLI auth flow:** The CLI stores PAT locally. This is CLI-side implementation, out of scope here.

4. **Port-forward URLs:** No `{project_id}` in WebSocket paths — project is resolved from the ticket payload. Ticket is created via `POST /port-forward/ticket` (PAT auth determines the project).

5. **Database port-forward permission:** Reuses `DATABASES_SHOW_SECRETS` rather than a separate `DATABASES_PORT_FORWARD` permission, since port-forwarding to a database inherently exposes its data.

6. **Ticket generation:** Uses `generate_random_string(64)` for ticket values, stored in Redis with 60s TTL, consumed on first use.
