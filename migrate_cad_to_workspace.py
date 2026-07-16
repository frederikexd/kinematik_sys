# ============================================================================
#  KinematiK — one-time migration: legacy single-tenant CAD library
#  ->  a workspace-scoped project store (post-accounts world).
#
#  WHY THIS EXISTS
#  ---------------
#  Before invite-links/accounts, everything (including the Team CAD library)
#  lived in ONE project blob:
#     * local dev  : ./project.json
#     * Supabase   : table `kinematik_project`, row id "elbee"
#
#  After accounts are switched on, the app reads/writes a PER-WORKSPACE store:
#     * local dev  : ./workspaces/<workspace_id>/project.json
#     * Supabase   : table `kinematik_workspace_projects`, keyed (workspace_id,id)
#
#  Those are different locations, so files published before the cutover won't
#  appear once a user signs in — until they're copied into a workspace. This
#  script does that copy for the CAD library (and, optionally, the whole
#  ledger) exactly once.
#
#  DESIGN NOTES
#  ------------
#  * We go through the real ProjectStore / workspace_store, never raw SQL, so
#    every workspace-isolation invariant (workspace_id stamping, RLS, payload
#    scoping) is enforced for us.
#  * De-duped by CADFile.id, so re-running is safe (idempotent): a file already
#    present in the destination is skipped, not duplicated.
#  * --dry-run prints the plan and writes nothing.
#  * Default copies ONLY the CAD library. --full also carries weights,
#    decisions, notes, geometry, board, harness, ev_excel_params.
#
#  USAGE
#  -----
#    # See what would move, change nothing:
#    python migrate_cad_to_workspace.py --workspace <WS_ID> --dry-run
#
#    # Do it (CAD library only):
#    python migrate_cad_to_workspace.py --workspace <WS_ID>
#
#    # Migrate from an explicit legacy local file instead of ./project.json:
#    python migrate_cad_to_workspace.py --workspace <WS_ID> \
#           --source-json /path/to/old_project.json
#
#    # Carry the entire ledger, not just CAD:
#    python migrate_cad_to_workspace.py --workspace <WS_ID> --full
#
#  For the Supabase source path, the same SUPABASE_URL / SUPABASE_KEY the app
#  used pre-accounts must be set. For the Supabase DESTINATION, SUPABASE_URL +
#  SUPABASE_ANON_KEY (or SUPABASE_KEY) must be set AND the workspace id must be
#  a UUID that already exists with your membership row (so RLS lets you write).
#  Run this from the fsae_suspension/ directory (same cwd the app uses).
# ============================================================================
from __future__ import annotations

import argparse
import os
import sys

from suspension import project as project_mod
from suspension.project import ProjectStore, CADFile
from suspension.workspace import (
    Workspace, WorkspaceContext, workspace_store, validate_workspace_id,
    WorkspaceError,
)


# --------------------------------------------------------------------------- #
#  Legacy source loading
# --------------------------------------------------------------------------- #
def load_legacy_store(source_json: str | None, project_id: str) -> ProjectStore:
    """Open the pre-accounts project.

    If --source-json is given, read that local file directly (bypasses
    Supabase entirely — handy for migrating an exported blob). Otherwise use
    the app's normal auto-backend: Supabase when SUPABASE_URL/KEY are set,
    else ./project.json. `project_id` is the legacy row key (default "elbee").
    """
    if source_json:
        if not os.path.exists(source_json):
            sys.exit(f"[fatal] --source-json not found: {source_json}")
        # Force the local JSON backend against the given file.
        backend = project_mod.JSONFileBackend(source_json)
        store = ProjectStore(backend=backend)
        return store

    # Match how the app picked its backend pre-accounts. If Supabase creds are
    # present, read the legacy single-tenant row; else the local project.json.
    url = project_mod._read_credential("SUPABASE_URL")
    key = project_mod._read_credential("SUPABASE_KEY")
    if url and key:
        backend = project_mod.SupabaseBackend(url, key, project_id=project_id)
        store = ProjectStore(backend=backend)
    else:
        store = ProjectStore(project_mod.DEFAULT_PROJECT)  # ./project.json
    return store


# --------------------------------------------------------------------------- #
#  Destination workspace store
# --------------------------------------------------------------------------- #
def resolve_token(explicit_token: str, email: str, password: str) -> str:
    """Return the user JWT to authenticate as. Precedence:
      1. an explicit --access-token / SUPABASE_ACCESS_TOKEN (already a JWT),
      2. --email/--password (or SUPABASE_EMAIL/SUPABASE_PASSWORD) → sign in via
         the app's own SupabaseAuth and hand back session.access_token.
    Signing in here uses the SAME code path the app does, so the token has
    identical semantics (auth.uid() = the human, RLS membership applies).
    Returns "" when nothing is provided (fine for local runs)."""
    if explicit_token:
        return explicit_token
    email = email or os.environ.get("SUPABASE_EMAIL", "")
    password = password or os.environ.get("SUPABASE_PASSWORD", "")
    if not (email and password):
        return ""
    url = project_mod._read_credential("SUPABASE_URL")
    key = (project_mod._read_credential("SUPABASE_ANON_KEY")
           or project_mod._read_credential("SUPABASE_KEY"))
    if not (url and key):
        sys.exit("[fatal] --email/--password given but SUPABASE_URL / anon key "
                 "are not set, so there's nothing to sign in against.")
    try:
        from suspension.auth import SupabaseAuth
        auth = SupabaseAuth(url, key)
        session = auth.sign_in(email, password)
        if not session.is_valid():
            sys.exit("[fatal] sign-in returned no valid session (check the "
                     "credentials, or confirm the account's email first).")
        print(f"Signed in as {session.email} — using that session's token.")
        return session.access_token
    except SystemExit:
        raise
    except Exception as e:
        sys.exit(f"[fatal] sign-in failed: {e}")


def open_workspace_store(workspace_id: str, root: str,
                         access_token: str = "") -> ProjectStore:
    """Build the tenant-scoped store the signed-in app would use for this
    workspace. Loading it establishes the correct optimistic-concurrency
    baseline (_loaded_version) so our later save() is a clean insert/update,
    not a blind overwrite.

    access_token: the signed-in user's Supabase JWT. REQUIRED for a Supabase
    destination — it's the identity RLS checks (auth.uid()). Omit it only for
    local (--source-json / no-Supabase) runs, where there is no RLS.
    """
    try:
        validate_workspace_id(workspace_id)
    except WorkspaceError as e:
        sys.exit(f"[fatal] {e}")
    # A minimal context: owner role locally, plus the user's JWT so that on a
    # Supabase destination auth.uid() resolves to the human and RLS can find
    # their workspace_members row. Without the token, a Supabase write is the
    # anon role and RLS will (correctly) reject it.
    ctx = WorkspaceContext(
        workspace=Workspace(id=workspace_id, name=workspace_id),
        role="owner",
        access_token=access_token or "",
        email=os.environ.get("MIGRATION_ACTOR", "cad-migration"),
    )
    return workspace_store(ctx, root=root)


# --------------------------------------------------------------------------- #
#  --preflight : will the destination write actually be allowed?
# --------------------------------------------------------------------------- #
def preflight(workspace_id: str, root: str, access_token: str) -> bool:
    """Answer, before touching any data, the one question that decides whether
    the migration can write: does RLS consider me a writer of this workspace?

    For a Supabase destination we call the SAME db function RLS uses,
    workspace_role(ws), through the JWT-bound client. A result in
    (owner, lead, member) means the kwp_write / kwp_update policy will pass;
    null means there's no workspace_members row for this user (you haven't
    created/joined the workspace via the app yet) and the write WOULD be
    rejected.

    For a local destination there is no RLS, so we just confirm the target
    directory is writable.
    """
    try:
        validate_workspace_id(workspace_id)
    except WorkspaceError as e:
        print(f"✗ {e}")
        return False

    url = project_mod._read_credential("SUPABASE_URL")
    key = (project_mod._read_credential("SUPABASE_ANON_KEY")
           or project_mod._read_credential("SUPABASE_KEY"))

    print("── Preflight ──")
    print(f"Workspace: {workspace_id}")

    if not (url and key):
        # Local destination — no RLS. Check we can create/write the folder.
        target = os.path.join(root, "workspaces", workspace_id)
        try:
            os.makedirs(target, exist_ok=True)
            probe = os.path.join(target, ".preflight")
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
            print(f"Destination: LOCAL ({target})")
            print("\n✓ Local target is writable — no RLS to satisfy. Good to migrate.")
            return True
        except Exception as e:
            print(f"\n✗ Local target not writable: {e}")
            return False

    # Supabase destination — the real RLS check.
    print("Destination: SUPABASE")
    if not access_token:
        print("\n✗ No access token. A Supabase write needs the signed-in user's "
              "JWT (auth.uid()); without it you're the anon role and RLS will "
              "reject the write. Pass --access-token <JWT> or set "
              "SUPABASE_ACCESS_TOKEN.")
        return False
    try:
        from supabase import create_client
        from suspension.workspace import refuse_service_role
        refuse_service_role(key)          # same guard the backend applies
        client = create_client(url, key)
        client.postgrest.auth(access_token)   # become the human
        resp = client.rpc("workspace_role", {"ws": workspace_id}).execute()
        role = resp.data
        # rpc may return the scalar directly or wrapped; normalise.
        if isinstance(role, list):
            role = role[0] if role else None
        print(f"workspace_role(...) → {role!r}")
        if role in ("owner", "lead", "member"):
            print(f"\n✓ You are '{role}' of this workspace — the migration write "
                  f"will pass RLS. Good to migrate.")
            return True
        if role == "viewer":
            print("\n✗ You are 'viewer' — read-only. RLS allows SELECT but not "
                  "INSERT/UPDATE, so the migration can't write. Ask an owner to "
                  "raise your role to member+.")
            return False
        print("\n✗ No membership row for you in this workspace (workspace_role "
              "returned null). Create or join the workspace via the app while "
              "signed in as this user, then re-run. The write would be rejected "
              "right now.")
        return False
    except Exception as e:
        print(f"\n✗ Preflight could not verify membership ({type(e).__name__}: {e}). "
              "Check SUPABASE_URL / key, that run_all.sql has been applied "
              "(workspace_role must exist), and that the token is valid.")
        return False


# --------------------------------------------------------------------------- #
#  The copy
# --------------------------------------------------------------------------- #
def migrate(src: ProjectStore, dst: ProjectStore, *, full: bool):
    """Copy CAD files (and optionally the rest of the ledger) src -> dst,
    de-duplicating CAD by id. Returns (added, skipped, carried_sections)."""
    existing_ids = {c.id for c in (getattr(dst, "cad_files", None) or [])}
    src_cad = list(getattr(src, "cad_files", None) or [])

    added, skipped = [], []
    for c in src_cad:
        if c.id in existing_ids:
            skipped.append(c)
            continue
        # Re-wrap through CADFile so __post_init__ backfills ts/id if the source
        # row was ever hand-edited or partial. Copy field-by-field to survive
        # small schema drift between revisions.
        dst.add_cad_file(CADFile(
            name=c.name, subsystem=getattr(c, "subsystem", "general"),
            uploader=getattr(c, "uploader", ""), kind=getattr(c, "kind", "file"),
            data_b64=getattr(c, "data_b64", ""), link=getattr(c, "link", ""),
            size_bytes=getattr(c, "size_bytes", 0), note=getattr(c, "note", ""),
            ts=getattr(c, "ts", ""), id=c.id,
        ))
        existing_ids.add(c.id)
        added.append(c)

    carried = ["cad_files"]
    if full:
        # Only fill destination sections that are currently empty, so a
        # migration never clobbers work already done in the new workspace.
        def _empty(v):
            return not v

        if _empty(getattr(dst, "weights", None)) and getattr(src, "weights", None):
            dst.weights = list(src.weights); carried.append("weights")
        if _empty(getattr(dst, "decisions", None)) and getattr(src, "decisions", None):
            dst.decisions = list(src.decisions); carried.append("decisions")
        if _empty(getattr(dst, "notes", None)) and getattr(src, "notes", None):
            dst.notes = list(src.notes); carried.append("notes")
        if getattr(src, "geometry", None) and not getattr(dst, "geometry", None):
            dst.geometry = src.geometry; carried.append("geometry")
        if getattr(src, "board", None) and not getattr(dst, "board", None):
            dst.board = src.board; carried.append("board")
        if getattr(src, "harness", None) and not getattr(dst, "harness", None):
            dst.harness = src.harness; carried.append("harness")
        if getattr(src, "ev_excel_params", None) and not getattr(dst, "ev_excel_params", None):
            dst.ev_excel_params = dict(src.ev_excel_params); carried.append("ev_excel_params")

    return added, skipped, carried


def _fmt(c: CADFile) -> str:
    where = "link" if getattr(c, "kind", "file") == "link" else "embedded"
    kb = (getattr(c, "size_bytes", 0) or 0) / 1024.0
    tag = getattr(c, "subsystem", "general")
    return f"    · {c.name}  [{tag}] {where}, {kb:,.0f} KB  (id {c.id})"


# --------------------------------------------------------------------------- #
#  --list-workspaces : enumerate destinations you can actually reach
# --------------------------------------------------------------------------- #
def list_workspaces(root: str):
    """There is no global 'all workspaces' API by design — workspaces are
    reached through membership (RLS on Supabase, per-id folders locally). So we
    list what THIS caller/host can see:

      * Local backend  : every <root>/workspaces/<id>/project.json on disk.
      * Supabase       : distinct workspace_id in kinematik_workspace_projects
                         that your signed-in JWT is allowed to read.
    """
    url = project_mod._read_credential("SUPABASE_URL")
    key = (project_mod._read_credential("SUPABASE_ANON_KEY")
           or project_mod._read_credential("SUPABASE_KEY"))

    print("── Reachable workspaces ──")
    printed = False

    # Local folders (always show these; they're free to read).
    ws_dir = os.path.join(root, "workspaces")
    if os.path.isdir(ws_dir):
        rows = []
        for name in sorted(os.listdir(ws_dir)):
            pj = os.path.join(ws_dir, name, "project.json")
            if os.path.exists(pj):
                try:
                    import json as _json
                    blob = _json.load(open(pj))
                    n_cad = len(blob.get("cad_files", []) or [])
                    updated = blob.get("updated") or blob.get("_written_at") or "?"
                except Exception:
                    n_cad, updated = "?", "?"
                rows.append((name, n_cad, updated))
        if rows:
            printed = True
            print(f"\nLocal ({ws_dir}):")
            for name, n_cad, updated in rows:
                print(f"  · {name}   cad_files={n_cad}   updated={updated}")

    # Supabase: distinct workspace ids the JWT can see.
    if url and key:
        try:
            from supabase import create_client
            from suspension.workspace import TABLE_PROJECTS
            client = create_client(url, key)
            resp = client.table(TABLE_PROJECTS).select("workspace_id,id,updated_at").execute()
            seen = {}
            for r in (resp.data or []):
                wid = r.get("workspace_id")
                seen.setdefault(wid, r.get("updated_at"))
            if seen:
                printed = True
                print(f"\nSupabase ({TABLE_PROJECTS}) — visible to your JWT:")
                for wid, updated in sorted(seen.items(), key=lambda kv: str(kv[0])):
                    print(f"  · {wid}   updated_at={updated}")
            else:
                print("\nSupabase: no workspace rows visible to this key "
                      "(fresh table, or RLS is scoping you out).")
        except Exception as e:
            print(f"\nSupabase: could not list ({type(e).__name__}: {e}).")
    else:
        print("\nSupabase: credentials not set — showing local only.")

    if not printed:
        print("  (none found)")


# --------------------------------------------------------------------------- #
#  --verify : confirm the destination now contains the source's CAD library
# --------------------------------------------------------------------------- #
def verify(src: ProjectStore, dst: ProjectStore) -> bool:
    """Post-migration check: every source CAD id must be present in the
    destination, and embedded payloads must match byte-for-byte. Prints a
    reconciliation table and returns True only when the destination is a
    superset of the source library. Safe to run before decommissioning the old
    row."""
    src_by_id = {c.id: c for c in (getattr(src, "cad_files", None) or [])}
    dst_by_id = {c.id: c for c in (getattr(dst, "cad_files", None) or [])}

    missing, mismatched, ok = [], [], []
    for cid, sc in src_by_id.items():
        dc = dst_by_id.get(cid)
        if dc is None:
            missing.append(sc)
            continue
        # Compare the payload that actually persists (embedded bytes or link).
        s_pay = (sc.data_b64 or "") if sc.kind != "link" else (sc.link or "")
        d_pay = (dc.data_b64 or "") if dc.kind != "link" else (dc.link or "")
        if sc.kind != dc.kind or s_pay != d_pay or sc.name != dc.name:
            mismatched.append((sc, dc))
        else:
            ok.append(sc)

    print("── Verification ──")
    print(f"Source library : {len(src_by_id)} file(s)")
    print(f"Dest  library  : {len(dst_by_id)} file(s)")
    print(f"Matched OK     : {len(ok)}")
    if missing:
        print(f"MISSING in dest: {len(missing)}")
        for c in missing:
            print(_fmt(c))
    if mismatched:
        print(f"MISMATCHED     : {len(mismatched)} (same id, different name/kind/payload)")
        for sc, dc in mismatched:
            print(f"    · id {sc.id}: src={sc.name}/{sc.kind}  dst={dc.name}/{dc.kind}")

    good = not missing and not mismatched
    print("\n" + ("✓ Destination contains the full source CAD library — safe to "
                  "decommission the legacy row." if good else
                  "✗ Destination is NOT yet a complete copy — re-run the "
                  "migration before removing the legacy data."))
    return good


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(
        description="Migrate the legacy CAD library into a workspace-scoped store.")
    ap.add_argument("--workspace",
                    help="Destination workspace id (UUID for Supabase). "
                         "Required for migration and --verify.")
    ap.add_argument("--list-workspaces", action="store_true",
                    help="List reachable workspaces (local folders + Supabase "
                         "rows your JWT can see) and exit.")
    ap.add_argument("--verify", action="store_true",
                    help="Check the destination already contains the source "
                         "CAD library (no writes) and exit.")
    ap.add_argument("--preflight", action="store_true",
                    help="Check whether the destination write will be allowed "
                         "(RLS membership on Supabase; folder-writable locally) "
                         "and exit. Writes nothing.")
    ap.add_argument("--access-token", default=None,
                    help="Signed-in user's Supabase JWT (or set "
                         "SUPABASE_ACCESS_TOKEN). Required to write/preflight a "
                         "Supabase destination — it's the identity RLS checks.")
    ap.add_argument("--email", default=None,
                    help="Sign in with this email (or SUPABASE_EMAIL) to fetch "
                         "the JWT automatically — no need to extract a token by "
                         "hand. Pair with --password.")
    ap.add_argument("--password", default=None,
                    help="Password for --email (or SUPABASE_PASSWORD). Prefer "
                         "the env var so it isn't stored in shell history.")
    ap.add_argument("--source-json", default=None,
                    help="Legacy local project.json to read instead of the "
                         "auto-detected backend.")
    ap.add_argument("--legacy-project-id", default="elbee",
                    help="Legacy Supabase row id (default: elbee).")
    ap.add_argument("--root", default=os.getcwd(),
                    help="Root for local workspace files (default: cwd).")
    ap.add_argument("--full", action="store_true",
                    help="Also carry weights/decisions/notes/geometry/board/"
                         "harness/ev params (only into empty destination "
                         "sections).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show the plan; write nothing.")
    args = ap.parse_args()
    token = resolve_token(
        args.access_token or os.environ.get("SUPABASE_ACCESS_TOKEN", ""),
        args.email, args.password)

    # Mode 0: preflight only.
    if args.preflight:
        if not args.workspace:
            sys.exit("[fatal] --preflight needs --workspace.")
        ok = preflight(args.workspace, args.root, token)
        sys.exit(0 if ok else 1)

    # Mode 1: just list destinations.
    if args.list_workspaces:
        list_workspaces(args.root)
        return

    # Mode 2: verify an existing migration (read-only).
    if args.verify:
        if not args.workspace:
            sys.exit("[fatal] --verify needs --workspace.")
        src = load_legacy_store(args.source_json, args.legacy_project_id)
        if getattr(src, "load_error", None):
            sys.exit(f"[fatal] could not read legacy project: {src.load_error}")
        dst = open_workspace_store(args.workspace, args.root, token)
        if getattr(dst, "load_error", None):
            sys.exit(f"[fatal] could not open destination workspace: {dst.load_error}")
        ok = verify(src, dst)
        sys.exit(0 if ok else 1)

    # Mode 3 (default): migrate.
    if not args.workspace:
        sys.exit("[fatal] migration needs --workspace (or use --list-workspaces).")

    print("── KinematiK CAD-library migration ──")
    src = load_legacy_store(args.source_json, args.legacy_project_id)
    if getattr(src, "load_error", None):
        sys.exit(f"[fatal] could not read legacy project: {src.load_error}")

    src_cad = list(getattr(src, "cad_files", None) or [])
    print(f"Source: {'--source-json ' + args.source_json if args.source_json else 'auto-backend'} "
          f"— {len(src_cad)} CAD file(s) found.")
    if not src_cad and not args.full:
        print("Nothing to migrate (no CAD files). Use --full to carry the "
              "rest of the ledger.")
        return

    dst = open_workspace_store(args.workspace, args.root, token)
    if getattr(dst, "load_error", None):
        sys.exit(f"[fatal] could not open destination workspace: {dst.load_error}")

    added, skipped, carried = migrate(src, dst, full=args.full)

    print(f"\nDestination workspace: {args.workspace}")
    print(f"Would add {len(added)} file(s); {len(skipped)} already present "
          f"(skipped by id).")
    if added:
        print("  New files:")
        for c in added:
            print(_fmt(c))
    if skipped:
        print("  Already in destination:")
        for c in skipped:
            print(_fmt(c))
    if args.full:
        print(f"  Ledger sections carried: {', '.join(carried)}")

    if args.dry_run:
        print("\n[dry-run] No changes written.")
        return

    if not added and args.full is False:
        print("\nNothing new to write. Done.")
        return

    # Auto-preflight before writing so a membership/RLS problem is reported in
    # plain language instead of a raw database rejection. Local runs pass this
    # trivially (no RLS); Supabase runs need a real member+ role.
    if not preflight(args.workspace, args.root, token):
        print("\n[aborted] Preflight failed — nothing written. Fix the above, "
              "then re-run (the script is idempotent).")
        sys.exit(1)

    ok = dst.save()
    if ok:
        print("\n✓ Migration saved to the workspace store.")
    else:
        conflict = getattr(dst, "save_conflict", None)
        err = getattr(dst, "save_error", None)
        if conflict:
            print("\n✗ A newer version exists in the destination workspace "
                  "(someone saved while this ran). Re-run — the script is "
                  "idempotent and will reconcile by id.")
        print(f"✗ Not saved: {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
