# CAD Library → Workspace Migration

`migrate_cad_to_workspace.py` moves the Team CAD library (and optionally the
whole project ledger) from the legacy single-tenant store into a
workspace-scoped store, so nothing published before the accounts/invite-link
cutover drops off once tenancy is switched on.

## Why it's needed

Before accounts, everything lived in one project blob:

- local dev: `./project.json`
- Supabase: table `kinematik_project`, row id `elbee`

After accounts, the app reads/writes **per workspace**:

- local dev: `./workspaces/<workspace_id>/project.json`
- Supabase: table `kinematik_workspace_projects`, keyed `(workspace_id, id)`

Different locations → files published earlier won't show up under a signed-in
workspace until they're copied over. This script does that copy, once.

## Run it from `fsae_suspension/`

That's the tree with `suspension/workspace.py` and `suspension/auth_ui.py`
(the account-ready code). The top-level `../streamlit_app.py` copy lacks those
modules — don't run against it.

### 0. Set up the database first (Supabase destinations only)

The migration writes into `kinematik_workspace_projects` and needs the
workspace/membership tables + RLS to exist. Run **`run_all.sql`** once in the
Supabase SQL editor — it concatenates all the setup scripts in dependency
order (idempotent, safe to re-run):

```
1  analytics_schema.sql       base tables
2  myth_schema.sql            base tables
3  workspace_isolation.sql    TENANCY SPINE — required
4  workspace_members_rpc.sql  member management
5  workspace_invites.sql      invite links
6  project_history.sql        version history
7  master_assembly_schema.sql optional (master-assembly feature)
8  analytics_hardening.sql    optional (analytics dashboards)
```

Minimum for accounts + invite links + CAD persistence: steps 1-5 (all included
in `run_all.sql`). Then create the destination workspace via the invite/account
flow so its UUID + your membership row exist, and only then run the migration
below. (Local/`--source-json` runs don't need any of this.)

### 1. See where you can write
```
python migrate_cad_to_workspace.py --list-workspaces
```
Lists local workspace folders and, if Supabase creds are set, the
`workspace_id`s your JWT can see.

### 2. Preview the move (writes nothing)
```
python migrate_cad_to_workspace.py --workspace <WS_ID> --dry-run
```

### 2b. Preflight — will the write actually be allowed?
```
python migrate_cad_to_workspace.py --preflight --workspace <WS_ID> \
       --email you@team.com --password '••••'
```
Instead of extracting a JWT by hand, pass `--email`/`--password` (or set
`SUPABASE_EMAIL` / `SUPABASE_PASSWORD`) and the script signs in through the
app's own `SupabaseAuth` and uses that session's token. You can still pass a
raw `--access-token <JWT>` (or `SUPABASE_ACCESS_TOKEN`) if you already have one;
an explicit token wins over email/password.

On a Supabase destination preflight calls the same `workspace_role()` function
RLS uses and tells you go/no-go: `owner`/`lead`/`member` = the write will pass;
null = you have no membership row yet (create/join the workspace via the app
first). Locally it just checks the folder is writable. Exit 0 = good, 1 = not
ready. The migration runs this automatically before writing, so a membership
problem is reported in plain language instead of a raw database error.

### 3. Migrate
```
python migrate_cad_to_workspace.py --workspace <WS_ID>            # CAD library only
python migrate_cad_to_workspace.py --workspace <WS_ID> --full     # + ledger (into empty sections only)
```

### 4. Verify before decommissioning the old row
```
python migrate_cad_to_workspace.py --verify --workspace <WS_ID>
```
Exit code 0 = destination is a complete copy (safe to remove legacy data);
1 = incomplete, re-run first. Good for a CI/one-off gate.

## Options

- `--access-token JWT` — the signed-in user's Supabase JWT (or set
  `SUPABASE_ACCESS_TOKEN`). Required to preflight/write a Supabase destination;
  it's the identity RLS checks. Not needed for local/`--source-json` runs.
- `--email` / `--password` — sign in automatically to fetch that JWT (or set
  `SUPABASE_EMAIL` / `SUPABASE_PASSWORD`); prefer the env vars so the password
  isn't stored in shell history. Uses the app's own auth path; a service_role
  key is refused. An explicit `--access-token` takes precedence.
- `--preflight` — check whether the destination write will be allowed, then
  exit (writes nothing).
- `--source-json PATH` — read a legacy local file / exported blob instead of
  the auto-detected backend.
- `--legacy-project-id ID` — legacy Supabase row id (default `elbee`).
- `--root PATH` — root for local `workspaces/` folders (default: cwd).
- `--full` — also carry weights / decisions / notes / geometry / board /
  harness / EV params, but only into destination sections that are still empty
  (never clobbers work already started in the new workspace).

## Notes

- **Idempotent.** De-duped by `CADFile.id`; re-running adds nothing already
  present. No duplicates.
- **Goes through the real store**, so workspace_id stamping, payload scoping,
  and Supabase RLS all enforce themselves — no raw SQL.
- **Supabase destination prerequisites:** the workspace must already exist as a
  UUID with your membership row, and `SUPABASE_URL` + `SUPABASE_ANON_KEY`
  (or `SUPABASE_KEY`) must be set. Create the workspace via the invite/account
  flow first, then migrate. (A `service_role` key is deliberately refused by the
  workspace backend — use the anon key + a signed-in user session.)
- **10 MB embed rule is unchanged:** embedded files migrate embedded, links
  migrate as links.
