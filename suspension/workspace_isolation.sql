-- ============================================================================
--  KinematiK — Workspace isolation migration (multi-tenant hardening)
--  Idempotent: safe to re-run. Run AFTER analytics_schema.sql / myth_schema.sql.
--
--  TENANCY MODEL
--    workspaces            one row per team / external EV startup
--    workspace_members     (workspace_id, user_id, role) — the ONLY grant path
--    kinematik_workspace_projects   tenant-scoped project ledger,
--                                   PK (workspace_id, id)
--    + workspace_id columns retrofitted onto myth/analytics tables.
--
--  ISOLATION RULES (enforced here, mirrored in suspension/workspace.py):
--    * RLS ENABLED + FORCED on every tenant table (FORCE = even table owner
--      obeys policies; only service_role, used exclusively server-side, bypasses).
--    * Every policy routes through is_workspace_member(); there is no policy
--      that grants by workspace name, project id, or "true".
--    * INSERT/UPDATE use WITH CHECK so a member of A can never write a row
--      stamped B — cross-workspace writes fail at the database, not the app.
--    * anon has NO direct table privileges; authenticated has table privileges
--      but every row still passes RLS.
-- ============================================================================

create extension if not exists pgcrypto;

-- ----------------------------------------------------------------------------
--  1. Tenancy spine
-- ----------------------------------------------------------------------------
create table if not exists workspaces (
    id          uuid primary key default gen_random_uuid(),
    name        text not null,
    kind        text not null default 'team'
                check (kind in ('team', 'ev_startup', 'sandbox')),
    created_by  uuid,                       -- auth.users.id of the creator
    created_at  timestamptz not null default now()
);

create table if not exists workspace_members (
    workspace_id uuid not null references workspaces(id) on delete cascade,
    user_id      uuid not null,             -- auth.users.id
    role         text not null default 'member'
                 check (role in ('owner', 'lead', 'member', 'viewer')),
    added_at     timestamptz not null default now(),
    primary key (workspace_id, user_id)
);
create index if not exists idx_wm_user on workspace_members (user_id);

-- SECURITY DEFINER so policies on member-gated tables can consult membership
-- without recursing into workspace_members' own RLS. STABLE: one snapshot/stmt.
create or replace function is_workspace_member(ws uuid)
returns boolean language sql stable security definer set search_path = public as $$
    select exists (select 1 from workspace_members m
                   where m.workspace_id = ws and m.user_id = auth.uid());
$$;

create or replace function workspace_role(ws uuid)
returns text language sql stable security definer set search_path = public as $$
    select m.role from workspace_members m
    where m.workspace_id = ws and m.user_id = auth.uid();
$$;

-- ----------------------------------------------------------------------------
--  2. Tenant-scoped project ledger (accounts/parameters/configurations/vehicle
--     ledgers all live inside the jsonb `data` document, one doc per project)
-- ----------------------------------------------------------------------------
create table if not exists kinematik_workspace_projects (
    workspace_id uuid not null references workspaces(id) on delete cascade,
    id           text not null default 'default',
    data         jsonb not null default '{}'::jsonb,
    updated_at   timestamptz not null default now(),
    primary key (workspace_id, id)
);
create index if not exists idx_kwp_ws on kinematik_workspace_projects (workspace_id);

-- One-time migration of the legacy single-tenant table into a legacy workspace.
do $$
declare legacy_ws uuid;
begin
    if exists (select 1 from information_schema.tables
               where table_name = 'kinematik_project')
       and not exists (select 1 from workspaces where name = '__legacy__') then
        insert into workspaces (name, kind) values ('__legacy__', 'team')
        returning id into legacy_ws;
        insert into kinematik_workspace_projects (workspace_id, id, data)
        select legacy_ws, p.id, p.data from kinematik_project p
        on conflict (workspace_id, id) do nothing;
    end if;
end $$;

-- ----------------------------------------------------------------------------
--  3. Retrofit workspace_id onto existing shared tables (myth KB, analytics).
--     Conditional: each block is a no-op if the table isn't deployed.
-- ----------------------------------------------------------------------------
do $$
declare t text;
begin
    foreach t in array array['myth_entities','myth_edges','feature_events'] loop
        if exists (select 1 from information_schema.tables where table_name = t) then
            execute format(
                'alter table %I add column if not exists workspace_id uuid
                 references workspaces(id) on delete cascade', t);
            execute format(
                'create index if not exists idx_%s_ws on %I (workspace_id)', t, t);
        end if;
    end loop;
end $$;

-- Per-workspace uniqueness for myth entities (global unique name would leak
-- existence across tenants and block two teams from naming the same part).
do $$
begin
    if exists (select 1 from information_schema.tables
               where table_name = 'myth_entities') then
        begin
            alter table myth_entities drop constraint if exists myth_entities_name_key;
        exception when others then null;
        end;
        create unique index if not exists uq_myth_entities_ws_name
            on myth_entities (workspace_id, lower(name));
    end if;
end $$;

-- ----------------------------------------------------------------------------
--  4. Row-Level Security — the tenant wall
-- ----------------------------------------------------------------------------
alter table workspaces                    enable row level security;
alter table workspaces                    force  row level security;
alter table workspace_members             enable row level security;
alter table workspace_members             force  row level security;
alter table kinematik_workspace_projects  enable row level security;
alter table kinematik_workspace_projects  force  row level security;

-- workspaces: visible only to members; creatable by any authenticated user
-- (creator immediately self-enrolls as owner via the trigger below).
drop policy if exists ws_select on workspaces;
create policy ws_select on workspaces for select
    using (is_workspace_member(id));
drop policy if exists ws_insert on workspaces;
create policy ws_insert on workspaces for insert to authenticated
    with check (created_by = auth.uid());
drop policy if exists ws_update on workspaces;
create policy ws_update on workspaces for update
    using (workspace_role(id) = 'owner') with check (workspace_role(id) = 'owner');
drop policy if exists ws_delete on workspaces;
create policy ws_delete on workspaces for delete
    using (workspace_role(id) = 'owner');

create or replace function _ws_owner_bootstrap() returns trigger
language plpgsql security definer set search_path = public as $$
begin
    insert into workspace_members (workspace_id, user_id, role)
    values (new.id, new.created_by, 'owner')
    on conflict do nothing;
    return new;
end $$;
drop trigger if exists trg_ws_owner_bootstrap on workspaces;
create trigger trg_ws_owner_bootstrap after insert on workspaces
    for each row execute function _ws_owner_bootstrap();

-- workspace_members: members see their workspace's roster; only owner/lead mutate.
drop policy if exists wm_select on workspace_members;
create policy wm_select on workspace_members for select
    using (is_workspace_member(workspace_id));
drop policy if exists wm_insert on workspace_members;
create policy wm_insert on workspace_members for insert
    with check (workspace_role(workspace_id) in ('owner','lead'));
drop policy if exists wm_update on workspace_members;
create policy wm_update on workspace_members for update
    using (workspace_role(workspace_id) = 'owner')
    with check (workspace_role(workspace_id) = 'owner');
drop policy if exists wm_delete on workspace_members;
create policy wm_delete on workspace_members for delete
    using (workspace_role(workspace_id) = 'owner' or user_id = auth.uid());

-- project ledger: member-read, writer-roles write, workspace stamped & checked.
drop policy if exists kwp_select on kinematik_workspace_projects;
create policy kwp_select on kinematik_workspace_projects for select
    using (is_workspace_member(workspace_id));
drop policy if exists kwp_write on kinematik_workspace_projects;
create policy kwp_write on kinematik_workspace_projects for insert
    with check (workspace_role(workspace_id) in ('owner','lead','member'));
drop policy if exists kwp_update on kinematik_workspace_projects;
create policy kwp_update on kinematik_workspace_projects for update
    using (workspace_role(workspace_id) in ('owner','lead','member'))
    with check (workspace_role(workspace_id) in ('owner','lead','member'));
drop policy if exists kwp_delete on kinematik_workspace_projects;
create policy kwp_delete on kinematik_workspace_projects for delete
    using (workspace_role(workspace_id) in ('owner','lead'));

-- Retrofitted tables get the same member gate (conditional on deployment).
do $$
declare t text;
begin
    foreach t in array array['myth_entities','myth_edges','feature_events'] loop
        if exists (select 1 from information_schema.tables where table_name = t) then
            execute format('alter table %I enable row level security', t);
            execute format('alter table %I force  row level security', t);
            execute format('drop policy if exists %s_ws_select on %I', t, t);
            execute format(
                'create policy %s_ws_select on %I for select
                 using (is_workspace_member(workspace_id))', t, t);
            execute format('drop policy if exists %s_ws_write on %I', t, t);
            execute format(
                'create policy %s_ws_write on %I for insert
                 with check (is_workspace_member(workspace_id)
                             and workspace_role(workspace_id) <> ''viewer'')', t, t);
            execute format('drop policy if exists %s_ws_update on %I', t, t);
            execute format(
                'create policy %s_ws_update on %I for update
                 using (is_workspace_member(workspace_id))
                 with check (is_workspace_member(workspace_id)
                             and workspace_role(workspace_id) <> ''viewer'')', t, t);
        end if;
    end loop;
end $$;

-- ----------------------------------------------------------------------------
--  5. Privilege hygiene: nothing for anon; authenticated goes through RLS.
-- ----------------------------------------------------------------------------
revoke all on workspaces, workspace_members, kinematik_workspace_projects from anon;
grant select, insert, update, delete
    on workspaces, workspace_members, kinematik_workspace_projects to authenticated;
grant execute on function is_workspace_member(uuid), workspace_role(uuid)
    to authenticated;
revoke execute on function is_workspace_member(uuid), workspace_role(uuid) from anon;

-- Legacy single-tenant table: freeze it read-none for API roles once migrated.
do $$
begin
    if exists (select 1 from information_schema.tables
               where table_name = 'kinematik_project') then
        revoke all on kinematik_project from anon, authenticated;
        alter table kinematik_project enable row level security;
        alter table kinematik_project force  row level security;  -- no policies ⇒ no access
    end if;
end $$;
