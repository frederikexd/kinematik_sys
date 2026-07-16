# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
#
#  Module: workspace — multi-tenant workspace isolation
#
#  ARCHITECTURE RULES (the whole module exists to enforce these):
#    1. Every persisted row carries a workspace_id, injected server-side of the
#       Python API — application code can never write a row without one.
#    2. Every read is FILTERED by workspace_id in the query AND (in Supabase)
#       re-enforced by Row-Level Security (workspace_isolation.sql). Defense in
#       depth: a bug here still hits the RLS wall; an RLS misconfig still hits
#       the query filter.
#    3. The SERVICE-ROLE key is refused. service_role bypasses RLS entirely, so
#       accepting it would turn one leaked credential into every tenant's data.
#       Multi-tenant traffic must run on the anon/publishable key + a user JWT.
#    4. Cross-workspace references inside a payload are stripped/rejected
#       before write (assert_payload_scoped) so a ledger export from team A
#       pasted into team B's session cannot smuggle A's workspace id back in.
#
#  Stdlib only at import; supabase-py is imported lazily inside the backend.
# ============================================================================

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import datetime as _dt
from dataclasses import dataclass, field
from typing import Optional

_WS_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

TABLE_PROJECTS = "kinematik_workspace_projects"


class WorkspaceError(RuntimeError):
    pass


class CrossWorkspaceViolation(WorkspaceError):
    """A payload or query attempted to touch a workspace other than the bound one."""


def validate_workspace_id(workspace_id: str, *, require_uuid: bool = False) -> str:
    ws = str(workspace_id or "").strip()
    if require_uuid:
        if not _UUID_RE.match(ws):
            raise WorkspaceError(f"workspace_id must be a UUID, got {ws!r}")
    elif not _WS_ID_RE.match(ws):
        # Also blocks path traversal for the local backend ('..', '/', '\\').
        raise WorkspaceError(f"invalid workspace_id {ws!r}")
    return ws


def _jwt_role(key: str) -> Optional[str]:
    """Best-effort decode of a Supabase key's JWT payload role claim (no
    signature verification needed — we only use it to REFUSE service keys)."""
    try:
        seg = key.split(".")[1]
        pad = "=" * (-len(seg) % 4)
        return json.loads(base64.urlsafe_b64decode(seg + pad)).get("role")
    except (IndexError, ValueError, binascii.Error, AttributeError):
        return None


def refuse_service_role(key: str):
    if _jwt_role(key) == "service_role" or "service_role" in (key or ""):
        raise WorkspaceError(
            "service_role key refused: it BYPASSES Row-Level Security, which is "
            "the tenant boundary. Use the anon/publishable key plus a signed-in "
            "user session; keep service_role server-side only.")


def assert_payload_scoped(payload: dict, workspace_id: str) -> dict:
    """
    Recursively verify a payload carries no FOREIGN workspace_id. Any embedded
    'workspace_id' key must equal the bound workspace (imports/exports keep the
    key); a mismatch is a hard error, never a silent rewrite — silently
    re-homing another team's data is exactly the contamination we're blocking.
    """
    def walk(node, path="$"):
        if isinstance(node, dict):
            wid = node.get("workspace_id")
            if wid is not None and str(wid) != workspace_id:
                raise CrossWorkspaceViolation(
                    f"payload at {path} references foreign workspace {wid!r} "
                    f"(bound: {workspace_id!r})")
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
    walk(payload)
    return payload


# --------------------------------------------------------------------------- #
#  Records
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Workspace:
    id: str
    name: str
    kind: str = "team"        # "team" | "ev_startup" | "sandbox"


@dataclass
class WorkspaceContext:
    """Everything a request needs to be tenant-scoped. Immutable binding: one
    context = one workspace; switching tenants means building a new context."""
    workspace: Workspace
    user_id: str = ""
    access_token: str = ""    # the signed-in user's JWT (RLS identity)
    role: str = "member"      # "owner" | "lead" | "member" | "viewer"
    email: str = ""           # for human-readable audit stamps (saved_by)

    def __post_init__(self):
        validate_workspace_id(self.workspace.id)

    @property
    def workspace_id(self) -> str:
        return self.workspace.id

    def can_write(self) -> bool:
        return self.role in ("owner", "lead", "member")


# --------------------------------------------------------------------------- #
#  Backends — drop-in replacements for project.ProjectStore backends
# --------------------------------------------------------------------------- #
class LocalWorkspaceBackend:
    """Per-workspace JSON files: <root>/workspaces/<workspace_id>/project.json.
    validate_workspace_id makes the id filesystem-safe (no traversal)."""

    def __init__(self, workspace_id: str, root: str = ".", filename: str = "project.json"):
        self.workspace_id = validate_workspace_id(workspace_id)
        self.dir = os.path.join(root, "workspaces", self.workspace_id)
        self.path = os.path.join(self.dir, filename)
        self.degraded_reason = None

    def read(self) -> dict:
        if os.path.exists(self.path):
            with open(self.path) as f:
                data = json.load(f)
            return assert_payload_scoped(data, self.workspace_id)
        return {}

    def read_version(self):
        """The local blob's own `updated` stamp (None if absent/unreadable)."""
        try:
            if os.path.exists(self.path):
                with open(self.path) as f:
                    return (json.load(f) or {}).get("updated")
        except Exception:
            pass
        return None

    def write(self, payload: dict, expected_version=None):
        assert_payload_scoped(payload, self.workspace_id)
        # Same optimistic contract as the Supabase backend so behaviour doesn't
        # change between laptop and cloud: refuse to overwrite a blob that
        # moved since it was loaded. (Single-user local this rarely fires, but
        # two terminals or a shared drive make it real.)
        from .project import StaleWriteError   # lazy: keep module stdlib-only at import
        current = self.read_version()
        if current is not None and str(current) != str(expected_version):
            raise StaleWriteError(mine=expected_version, theirs=current)
        os.makedirs(self.dir, exist_ok=True)
        body = dict(payload)
        body["workspace_id"] = self.workspace_id
        body["_written_at"] = _dt.datetime.now().isoformat(timespec="seconds")
        with open(self.path, "w") as f:
            json.dump(body, f, indent=2)


class WorkspaceScopedSupabaseBackend:
    """
    Tenant-scoped Supabase persistence for the project ledger. Table
    kinematik_workspace_projects (see workspace_isolation.sql):
        workspace_id uuid, id text, data jsonb, updated_at — PK(workspace_id,id)
    Every query filters .eq('workspace_id', …); RLS enforces membership again
    on the server, keyed to the user JWT set via postgrest.auth(token).
    """

    TABLE = TABLE_PROJECTS

    def __init__(self, url: str, anon_key: str, ctx: WorkspaceContext,
                 project_id: str = "default"):
        refuse_service_role(anon_key)
        validate_workspace_id(ctx.workspace_id, require_uuid=True)
        from supabase import create_client
        self.client = create_client(url, anon_key)
        if ctx.access_token:
            # Bind PostgREST to the USER's identity so auth.uid() (and thus the
            # RLS membership check) is the human, not the anon role.
            self.client.postgrest.auth(ctx.access_token)
        self.ctx = ctx
        self.project_id = str(project_id)
        self.degraded_reason = None

    def read(self) -> dict:
        resp = (self.client.table(self.TABLE).select("data,workspace_id")
                .eq("workspace_id", self.ctx.workspace_id)
                .eq("id", self.project_id).execute())
        rows = resp.data or []
        if not rows:
            return {}
        row = rows[0]
        if str(row.get("workspace_id")) != self.ctx.workspace_id:  # paranoia gate
            raise CrossWorkspaceViolation("server returned a foreign-workspace row")
        return assert_payload_scoped(row["data"] or {}, self.ctx.workspace_id)

    def read_version(self):
        """Cheap change probe: pull ONLY data->>'updated' for this tenant's row.
        Returns None on any error or missing row so callers fall back to a full
        authoritative read rather than assuming 'no change'."""
        try:
            resp = (self.client.table(self.TABLE)
                    .select("data->>updated")
                    .eq("workspace_id", self.ctx.workspace_id)
                    .eq("id", self.project_id).execute())
            rows = resp.data or []
            if not rows:
                return None
            row = rows[0]
            return row.get("updated") or next(iter(row.values()), None)
        except Exception:
            return None

    def write(self, payload: dict, expected_version=None):
        """Tenant-scoped persist with optimistic concurrency.

        expected_version is the `updated` stamp the caller's edits are based
        on. A concurrent teammate's save changes that stamp, so the CAS update
        matches zero rows and we raise StaleWriteError instead of silently
        replacing their ledger (the old unconditional upsert was
        last-write-wins — with two subteam leads editing at once, whoever
        saved second erased the other's declarations without a trace).
        """
        if not self.ctx.can_write():
            raise WorkspaceError(f"role {self.ctx.role!r} cannot write this workspace")
        assert_payload_scoped(payload, self.ctx.workspace_id)
        from .project import StaleWriteError   # lazy: keep module stdlib-only at import
        now = _dt.datetime.utcnow().isoformat() + "Z"
        # Audit stamp: who saved this version (shown in the Project history
        # panel). Copy-then-stamp so the caller's dict isn't mutated.
        payload = dict(payload)
        payload["saved_by"] = self.ctx.email or (self.ctx.user_id or "")[:8] or "unknown"
        if expected_version is not None:
            # Atomic compare-and-swap: UPDATE ... WHERE workspace_id/id match
            # AND data->>'updated' still equals what we loaded. PostgREST
            # returns the updated rows; zero rows means the version moved.
            resp = (self.client.table(self.TABLE)
                    .update({"data": payload, "updated_at": now})
                    .eq("workspace_id", self.ctx.workspace_id)
                    .eq("id", self.project_id)
                    .eq("data->>updated", str(expected_version))
                    .execute())
            if not (resp.data or []):
                raise StaleWriteError(mine=expected_version,
                                      theirs=self.read_version())
            return
        # No baseline (fresh project or pre-versioning caller). Refuse to wipe
        # an existing VERSIONED row; allow first insert and the one-time
        # upgrade of a legacy unversioned blob.
        current = self.read_version()
        if current is not None:
            raise StaleWriteError(mine=None, theirs=current)
        (self.client.table(self.TABLE)
         .upsert({"workspace_id": self.ctx.workspace_id, "id": self.project_id,
                  "data": payload, "updated_at": now},
                 on_conflict="workspace_id,id")
         .execute())


# --------------------------------------------------------------------------- #
#  Factory — mirrors project._auto_backend but tenant-aware
# --------------------------------------------------------------------------- #
def workspace_backend(ctx: WorkspaceContext, *, root: str = "."):
    """Supabase-scoped if credentials exist (never silently on failure — the
    degraded_reason contract from project._auto_backend is preserved),
    else per-workspace local JSON."""
    from .project import _read_credential
    url, key = _read_credential("SUPABASE_URL"), _read_credential("SUPABASE_ANON_KEY") \
        or _read_credential("SUPABASE_KEY")
    if url and key:
        try:
            return WorkspaceScopedSupabaseBackend(url, key, ctx)
        except WorkspaceError:
            raise      # a refused service key is a config error, not a fallback case
        except Exception as e:
            fb = LocalWorkspaceBackend(ctx.workspace_id, root=root)
            fb.degraded_reason = (
                "Supabase credentials are set but the tenant backend failed "
                f"({type(e).__name__}: {e}). Falling back to LOCAL per-workspace "
                "storage — data will NOT sync until this is fixed.")
            return fb
    return LocalWorkspaceBackend(ctx.workspace_id, root=root)


def workspace_store(ctx: WorkspaceContext, *, root: str = "."):
    """A ProjectStore whose entire persistence surface is workspace-scoped."""
    from .project import ProjectStore
    return ProjectStore(backend=workspace_backend(ctx, root=root))


# --------------------------------------------------------------------------- #
#  In-memory registry for tests / single-process demos
# --------------------------------------------------------------------------- #
class MemoryWorkspaceRegistry:
    """Reference semantics of the SQL layer, in memory: memberships gate every
    read/write; unknown workspace or non-member ⇒ CrossWorkspaceViolation."""

    def __init__(self):
        self._workspaces: dict[str, Workspace] = {}
        self._members: dict[str, dict[str, str]] = {}      # ws -> {user: role}
        self._rows: dict[tuple, dict] = {}                 # (ws, table, id) -> data

    def create_workspace(self, ws: Workspace, owner_user_id: str) -> Workspace:
        validate_workspace_id(ws.id)
        if ws.id in self._workspaces:
            raise WorkspaceError(f"workspace {ws.id!r} exists")
        self._workspaces[ws.id] = ws
        self._members[ws.id] = {owner_user_id: "owner"}
        return ws

    def add_member(self, actor: str, ws_id: str, user_id: str, role: str = "member"):
        if self._members.get(ws_id, {}).get(actor) not in ("owner", "lead"):
            raise WorkspaceError("only owner/lead may add members")
        self._members[ws_id][user_id] = role

    def _gate(self, user_id: str, ws_id: str) -> str:
        role = self._members.get(ws_id, {}).get(user_id)
        if role is None:
            raise CrossWorkspaceViolation(
                f"user {user_id!r} is not a member of workspace {ws_id!r}")
        return role

    def put(self, user_id: str, ws_id: str, table: str, row_id: str, data: dict):
        if self._gate(user_id, ws_id) == "viewer":
            raise WorkspaceError("viewer role cannot write")
        assert_payload_scoped(data, ws_id)
        self._rows[(ws_id, table, row_id)] = json.loads(json.dumps(data))

    def get(self, user_id: str, ws_id: str, table: str, row_id: str) -> Optional[dict]:
        self._gate(user_id, ws_id)
        return self._rows.get((ws_id, table, row_id))

    def list_rows(self, user_id: str, ws_id: str, table: str) -> list[str]:
        self._gate(user_id, ws_id)
        return sorted(rid for (w, t, rid) in self._rows if w == ws_id and t == table)
