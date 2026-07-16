# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
#
#  Module: auth — sign a user in against Supabase Auth and resolve the
#  WorkspaceContext (user_id + JWT + workspace + role) that workspace.py needs.
#
#  This is the missing link for the "Option B" tenant-isolation path:
#    workspace.py  already knows how to persist a project scoped to a
#                  WorkspaceContext (JWT-bound PostgREST + RLS).
#    auth.py       (here) turns an email/password login into that context by
#                  (a) getting a user JWT from Supabase Auth, and
#                  (b) reading the caller's workspace memberships THROUGH RLS
#                      (so the same wall that guards project rows guards this).
#
#  SECURITY NOTES
#    * We use the anon/publishable key ONLY. The user's JWT is what gives
#      auth.uid() an identity; RLS does the rest. The service_role key is
#      never accepted here (workspace.refuse_service_role guards the backend).
#    * Membership is read via the user-bound client, so a user can only ever
#      see workspaces they actually belong to — no server-side admin listing.
#
#  supabase-py is imported lazily so this module stays importable in tests
#  and plain scripts that never sign in.
# ============================================================================

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .workspace import (
    Workspace,
    WorkspaceContext,
    WorkspaceError,
    refuse_service_role,
    validate_workspace_id,
)


class AuthError(RuntimeError):
    """Sign-in failed, or the signed-in user has no usable workspace."""


@dataclass
class Session:
    """A signed-in user's identity, independent of any single workspace.
    One sign-in yields one Session; the user may belong to several workspaces,
    each of which becomes its own WorkspaceContext via context_for()."""
    user_id: str
    email: str
    access_token: str
    refresh_token: str = ""

    def is_valid(self) -> bool:
        return bool(self.user_id and self.access_token)


class SupabaseAuth:
    """
    Thin wrapper over supabase-py's auth + a user-bound PostgREST client.

    Lifecycle:
        auth = SupabaseAuth(url, anon_key)
        session = auth.sign_in(email, password)      # or sign_up(...)
        workspaces = auth.list_workspaces(session)   # [(Workspace, role), ...]
        ctx = auth.context_for(session, workspace_id)
        store = workspace_store(ctx)                 # from suspension.workspace
    """

    def __init__(self, url: str, anon_key: str):
        if not url or not anon_key:
            raise AuthError("Supabase URL and anon key are required for sign-in.")
        # Never let a service_role key reach the auth path either: it would let
        # the app impersonate anyone and sidestep the tenant wall entirely.
        refuse_service_role(anon_key)
        self._url = url
        self._anon_key = anon_key
        from supabase import create_client
        self._client = create_client(url, anon_key)

    # ------------------------------------------------------------------ #
    #  Identity
    # ------------------------------------------------------------------ #
    def sign_in(self, email: str, password: str) -> Session:
        try:
            resp = self._client.auth.sign_in_with_password(
                {"email": email, "password": password})
        except Exception as e:
            raise AuthError(f"Sign-in failed: {e}") from e
        return self._session_from_resp(resp)

    def sign_up(self, email: str, password: str) -> Session:
        """Create an account. On projects with email-confirmation enabled this
        returns a session only after confirmation; we surface that clearly
        rather than pretending the user is signed in."""
        try:
            resp = self._client.auth.sign_up(
                {"email": email, "password": password})
        except Exception as e:
            raise AuthError(f"Sign-up failed: {e}") from e
        if getattr(resp, "session", None) is None:
            raise AuthError(
                "Account created — check your email to confirm it, then sign in.")
        return self._session_from_resp(resp)

    def restore(self, access_token: str, refresh_token: str = "") -> Session:
        """Rebuild a Session from cached tokens (e.g. Streamlit session_state)
        without a fresh password round-trip. Verifies the token is still good
        by asking Supabase who it belongs to."""
        try:
            self._client.auth.set_session(access_token, refresh_token or access_token)
            user = self._client.auth.get_user(access_token)
        except Exception as e:
            raise AuthError(f"Session expired, please sign in again: {e}") from e
        u = getattr(user, "user", None) or getattr(user, "data", None)
        if u is None:
            raise AuthError("Session expired, please sign in again.")
        return Session(
            user_id=str(getattr(u, "id", "")),
            email=str(getattr(u, "email", "") or ""),
            access_token=access_token,
            refresh_token=refresh_token,
        )

    def sign_out(self):
        try:
            self._client.auth.sign_out()
        except Exception:
            pass

    def _session_from_resp(self, resp) -> Session:
        sess = getattr(resp, "session", None)
        user = getattr(resp, "user", None) or getattr(sess, "user", None)
        if sess is None or user is None:
            raise AuthError("Sign-in returned no session. Check your credentials.")
        s = Session(
            user_id=str(getattr(user, "id", "")),
            email=str(getattr(user, "email", "") or ""),
            access_token=str(getattr(sess, "access_token", "") or ""),
            refresh_token=str(getattr(sess, "refresh_token", "") or ""),
        )
        if not s.is_valid():
            raise AuthError("Sign-in returned an incomplete session.")
        return s

    # ------------------------------------------------------------------ #
    #  Workspace resolution (all reads pass through RLS as the user)
    # ------------------------------------------------------------------ #
    def _user_client(self, session: Session):
        """A PostgREST client bound to the user's JWT, so auth.uid() inside the
        database is this human and RLS returns only their rows."""
        from supabase import create_client
        client = create_client(self._url, self._anon_key)
        client.postgrest.auth(session.access_token)
        return client

    def list_workspaces(self, session: Session) -> list[tuple[Workspace, str]]:
        """Return [(Workspace, role), ...] the user belongs to. RLS on
        workspace_members guarantees this is exactly their memberships — a
        non-member's rows are invisible, not merely filtered client-side."""
        if not session.is_valid():
            raise AuthError("Not signed in.")
        client = self._user_client(session)
        try:
            memb = (client.table("workspace_members")
                    .select("workspace_id, role")
                    .eq("user_id", session.user_id).execute())
            member_rows = memb.data or []
            if not member_rows:
                return []
            ids = [r["workspace_id"] for r in member_rows]
            role_by_id = {str(r["workspace_id"]): r.get("role", "member")
                          for r in member_rows}
            ws = (client.table("workspaces")
                  .select("id, name, kind")
                  .in_("id", ids).execute())
            out: list[tuple[Workspace, str]] = []
            for row in (ws.data or []):
                wid = str(row["id"])
                out.append((
                    Workspace(id=wid, name=row.get("name", wid),
                              kind=row.get("kind", "team")),
                    role_by_id.get(wid, "member"),
                ))
            out.sort(key=lambda t: t[0].name.lower())
            return out
        except Exception as e:
            raise AuthError(f"Could not read your workspaces: {e}") from e

    def create_workspace(self, session: Session, name: str,
                         kind: str = "team") -> Workspace:
        """Create a workspace owned by the signed-in user. The DB trigger
        (_ws_owner_bootstrap in workspace_isolation.sql) self-enrolls the
        creator as 'owner', so no second call is needed."""
        if not session.is_valid():
            raise AuthError("Not signed in.")
        if not (name or "").strip():
            raise AuthError("Workspace name cannot be empty.")
        client = self._user_client(session)
        try:
            resp = (client.table("workspaces")
                    .insert({"name": name.strip(), "kind": kind,
                             "created_by": session.user_id})
                    .execute())
            row = (resp.data or [None])[0]
            if not row:
                raise AuthError("Workspace insert returned no row.")
            return Workspace(id=str(row["id"]), name=row["name"],
                             kind=row.get("kind", kind))
        except AuthError:
            raise
        except Exception as e:
            raise AuthError(f"Could not create workspace: {e}") from e

    # ------------------------------------------------------------------ #
    #  Member administration (via SECURITY DEFINER RPCs, see
    #  workspace_members_rpc.sql). Permission is enforced inside each RPC
    #  with the same owner/lead rules as RLS; we surface failures as AuthError.
    # ------------------------------------------------------------------ #
    def list_members(self, session: Session, workspace_id: str
                     ) -> list[dict]:
        """Roster for a workspace: [{user_id, email, role, added_at}, ...].
        Any member may read it."""
        client = self._user_client(session)
        try:
            resp = client.rpc("list_workspace_members",
                              {"ws": str(workspace_id)}).execute()
            return list(resp.data or [])
        except Exception as e:
            raise AuthError(f"Could not list members: {self._rpc_msg(e)}") from e

    def add_member(self, session: Session, workspace_id: str, email: str,
                   role: str = "member") -> str:
        """Add (or re-role) a member by email. Caller must be owner/lead; the
        target must already have an account. Returns the target's user id."""
        if not (email or "").strip():
            raise AuthError("Enter an email address.")
        client = self._user_client(session)
        try:
            resp = client.rpc("add_workspace_member",
                              {"ws": str(workspace_id),
                               "member_email": email.strip(),
                               "member_role": role}).execute()
            return str(resp.data) if resp.data is not None else ""
        except Exception as e:
            raise AuthError(self._rpc_msg(e)) from e

    def set_member_role(self, session: Session, workspace_id: str,
                        target_user_id: str, role: str) -> None:
        client = self._user_client(session)
        try:
            client.rpc("set_workspace_member_role",
                       {"ws": str(workspace_id),
                        "target_user": str(target_user_id),
                        "new_role": role}).execute()
        except Exception as e:
            raise AuthError(self._rpc_msg(e)) from e

    def remove_member(self, session: Session, workspace_id: str,
                      target_user_id: str) -> None:
        client = self._user_client(session)
        try:
            client.rpc("remove_workspace_member",
                       {"ws": str(workspace_id),
                        "target_user": str(target_user_id)}).execute()
        except Exception as e:
            raise AuthError(self._rpc_msg(e)) from e

    # ------------------------------------------------------------------ #
    #  Invite links (self-serve team onboarding — workspace_invites.sql).
    #  A lead mints one link, pastes it in the team chat, and teammates
    #  join with the right role. Links can only grant member/viewer, always
    #  expire, have a use cap, and are revocable — see the SQL for the
    #  trust properties; this layer only routes and surfaces errors.
    # ------------------------------------------------------------------ #
    def create_invite(self, session: Session, workspace_id: str,
                      role: str = "member", ttl_hours: int = 168,
                      max_uses: int = 30) -> str:
        """Mint an invite token for the workspace (caller must be owner/lead).
        Returns the token string; build the shareable URL with
        auth_ui.build_join_url()."""
        if role not in ("member", "viewer"):
            raise AuthError("Invite links can only grant member or viewer — "
                            "promote people explicitly in the Members panel.")
        client = self._user_client(session)
        try:
            resp = client.rpc("create_workspace_invite",
                              {"ws": str(workspace_id), "invite_role": role,
                               "ttl_hours": int(ttl_hours),
                               "uses": int(max_uses)}).execute()
            tok = resp.data
            if not tok:
                raise AuthError("Invite RPC returned no token.")
            return str(tok)
        except AuthError:
            raise
        except Exception as e:
            raise AuthError(self._rpc_msg(e)) from e

    def redeem_invite(self, session: Session, token: str
                      ) -> tuple[Workspace, str]:
        """Join the workspace behind `token`. Idempotent: an existing member
        keeps their (possibly higher) role. Returns (workspace, role)."""
        client = self._user_client(session)
        try:
            resp = client.rpc("redeem_workspace_invite",
                              {"invite_token": str(token).strip()}).execute()
            rows = resp.data or []
            if not rows:
                raise AuthError("Invite redemption returned nothing — "
                                "the link may be invalid.")
            row = rows[0]
            ws = Workspace(id=str(row["workspace_id"]),
                           name=str(row.get("workspace_name") or "workspace"))
            return ws, str(row.get("granted_role") or "member")
        except AuthError:
            raise
        except Exception as e:
            raise AuthError(self._rpc_msg(e)) from e

    def list_invites(self, session: Session, workspace_id: str) -> list[dict]:
        """Live (unexpired, unrevoked) invite links for the workspace —
        owner/lead only. For the revoke UI."""
        client = self._user_client(session)
        try:
            resp = client.rpc("list_workspace_invites",
                              {"ws": str(workspace_id)}).execute()
            return list(resp.data or [])
        except Exception as e:
            raise AuthError(self._rpc_msg(e)) from e

    def revoke_invite(self, session: Session, token: str) -> None:
        client = self._user_client(session)
        try:
            client.rpc("revoke_workspace_invite",
                       {"invite_token": str(token).strip()}).execute()
        except Exception as e:
            raise AuthError(self._rpc_msg(e)) from e

    @staticmethod
    def _rpc_msg(e: Exception) -> str:
        """Pull the human-readable message out of a PostgREST/RPC error so the
        UI shows 'no user with email …' rather than a raw exception repr."""
        for attr in ("message", "details", "hint"):
            v = getattr(e, attr, None)
            if v:
                return str(v)
        # supabase-py often wraps the Postgres error as a dict in args[0].
        arg = e.args[0] if getattr(e, "args", None) else None
        if isinstance(arg, dict):
            return str(arg.get("message") or arg.get("details") or arg)
        return str(e)

    def context_for(self, session: Session, workspace_id: str,
                    *, role: Optional[str] = None) -> WorkspaceContext:
        """Build the WorkspaceContext workspace_store() consumes. If role is
        not supplied we resolve it from the user's memberships so writer/viewer
        gating is correct."""
        validate_workspace_id(workspace_id, require_uuid=True)
        resolved_role = role
        ws_obj: Optional[Workspace] = None
        for ws, r in self.list_workspaces(session):
            if ws.id == str(workspace_id):
                ws_obj = ws
                resolved_role = resolved_role or r
                break
        if ws_obj is None:
            raise AuthError(
                "You are not a member of that workspace (or it does not exist).")
        return WorkspaceContext(
            workspace=ws_obj,
            user_id=session.user_id,
            access_token=session.access_token,
            role=resolved_role or "member",
            email=getattr(session, "email", "") or "",
        )


def build_auth() -> Optional["SupabaseAuth"]:
    """Construct a SupabaseAuth from configured credentials, or return None if
    Supabase isn't configured (local single-user / test mode). Mirrors the
    credential resolution used elsewhere: SUPABASE_ANON_KEY preferred, with a
    fallback to the legacy SUPABASE_KEY name."""
    from .project import _read_credential
    url = _read_credential("SUPABASE_URL")
    key = _read_credential("SUPABASE_ANON_KEY") or _read_credential("SUPABASE_KEY")
    if not (url and key):
        return None
    return SupabaseAuth(url, key)
