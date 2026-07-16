# Workspace Isolation Setup (Option B)

The app now enforces per-workspace tenant isolation. Project data is stored in
`kinematik_workspace_projects`, scoped to a workspace, and protected by Supabase
Row-Level Security. The old anon-key path to `kinematik_project` is gone — that
table stays frozen, which is why you saw the `permission denied` error before.

## What changed in the code

- **`suspension/auth.py`** (new) — signs users in against Supabase Auth and
  resolves their `WorkspaceContext` (user JWT + workspace + role), reading
  memberships *through* RLS.
- **`suspension/auth_ui.py`** (new) — the Streamlit sign-in gate and workspace
  picker. Caches the context in `st.session_state`.
- **`suspension/workspace.py`** (already present) — the workspace-scoped
  Supabase backend. Now actually wired up.
- **`streamlit_app.py`** — a sign-in gate runs before any project data renders;
  `_make_store()` / `get_store()` build a workspace-scoped store from the
  active context. All store construction routes through `_make_store()`.

## 1. Run the schema (if you haven't already)

In the Supabase SQL editor, run in order:
`analytics_schema.sql` (if used) → `myth_schema.sql` (if used) →
`suspension/workspace_isolation.sql`. It's idempotent.

Then run `suspension/workspace_members_rpc.sql` (also idempotent). This adds the
member-management functions the app's Members panel calls — they resolve emails
to users server-side (something the client can't do) while re-checking
owner/lead permission inside each function.

## 2. Configure secrets

On Streamlit Cloud (Settings → Secrets) or as environment variables:

```toml
SUPABASE_URL      = "https://YOUR-PROJECT.supabase.co"
SUPABASE_ANON_KEY = "eyJ...your anon / publishable key..."
```

**Use the anon (publishable) key, never the service_role key.** The app refuses
a service_role key on purpose — it bypasses RLS, which is the tenant wall.
`SUPABASE_ANON_KEY` is preferred; the legacy name `SUPABASE_KEY` still works as a
fallback.

## 3. Enable email/password auth in Supabase

Authentication → Providers → Email. If you turn on "Confirm email", new users
must confirm before their first sign-in (the app tells them so).

## 4. First run

1. Open the app → you'll see a **Sign in / Create account** screen.
2. Create an account, then (if confirmation is on) confirm via email and sign in.
3. You'll have no workspaces yet → create one. The database trigger enrolls you
   as its **owner** automatically.
4. The sidebar workspace picker switches tenants; the store rebuilds per
   workspace, so you never see another team's data.

## Roles & member management

`owner` / `lead` / `member` can write; `viewer` is read-only (enforced in both
the app and the database). The app hands out **lead** and **member**; `owner` is
the workspace creator.

Open the **Members** expander in the sidebar (visible to owners and leads) to:

- **Add a member by email** — they must already have a KinematiK account with
  that email; if not, the app says so.
- **Change a role** between lead and member.
- **Remove a member**, or leave the workspace yourself.

Every action is re-checked in the database (the RPCs in
`workspace_members_rpc.sql`), so the panel can never grant more than the caller's
role allows. The owner can't be removed or re-roled through the UI.

## Local / offline mode

If `SUPABASE_URL` / key are absent, the app runs exactly as before: no sign-in,
local JSON files per workspace under `./workspaces/<id>/`. Good for laptops and
tests. No cloud isolation applies in that mode because there's no shared store.
