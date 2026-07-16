-- ============================================================================
--  KinematiK — project version history (snapshot-on-write, server-side)
--  Run once in the Supabase SQL editor. Idempotent — safe to re-run.
--
--  WHY: the project ledger is one JSONB blob per (workspace, project). Even
--  with optimistic locking in the app, a bad merge, a bug, or a user clicking
--  through a conflict can still destroy data. This trigger snapshots the
--  PREVIOUS blob on every overwrite, entirely server-side, so recovery never
--  depends on the app having done the right thing. Keeps the last 20 versions
--  per project (a blob is ~1 MB, so worst case ~20 MB/project — bounded).
--
--  Run order: AFTER suspension/workspace_isolation.sql (needs its tables +
--  the is_workspace_member() helper it defines; if your helper is named
--  differently, adjust the two policy lines marked below).
-- ============================================================================

-- 1) History table --------------------------------------------------------
create table if not exists kinematik_project_history (
    hist_id       bigint generated always as identity primary key,
    workspace_id  uuid  not null,
    id            text  not null,
    data          jsonb not null,
    -- the overwritten row's own stamps, for point-in-time restore
    was_updated_at timestamptz,
    replaced_at    timestamptz not null default now()
);

create index if not exists kinematik_project_history_lookup
    on kinematik_project_history (workspace_id, id, replaced_at desc);

-- 2) Snapshot trigger ------------------------------------------------------
-- SECURITY DEFINER so the snapshot insert works under RLS regardless of the
-- writing user's own grants; the function only ever copies the row being
-- replaced, so it cannot be used to read or write anything else.
create or replace function kinematik_snapshot_project()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    -- Only snapshot when the blob actually changed (a no-op save costs nothing)
    if OLD.data is distinct from NEW.data then
        insert into kinematik_project_history
            (workspace_id, id, data, was_updated_at)
        values
            (OLD.workspace_id, OLD.id, OLD.data, OLD.updated_at);

        -- Prune: keep the newest 20 snapshots for this project
        delete from kinematik_project_history h
        where h.workspace_id = OLD.workspace_id
          and h.id = OLD.id
          and h.hist_id not in (
              select hist_id from kinematik_project_history
              where workspace_id = OLD.workspace_id and id = OLD.id
              order by replaced_at desc, hist_id desc
              limit 20);
    end if;
    return NEW;
end;
$$;

drop trigger if exists trg_kinematik_snapshot_project
    on kinematik_workspace_projects;
create trigger trg_kinematik_snapshot_project
    before update on kinematik_workspace_projects
    for each row execute function kinematik_snapshot_project();

-- 3) RLS: members may READ their workspace's history; nobody writes directly
--    (only the trigger inserts, and it runs as the definer).
alter table kinematik_project_history enable row level security;
alter table kinematik_project_history force row level security;

drop policy if exists history_member_read on kinematik_project_history;
create policy history_member_read on kinematik_project_history
    for select
    using (
        exists (
            select 1 from workspace_members m           -- << adjust if your
            where m.workspace_id = kinematik_project_history.workspace_id
              and m.user_id = auth.uid()                -- << membership check
        )                                               --    differs
    );

revoke insert, update, delete on kinematik_project_history from anon, authenticated;
grant  select on kinematik_project_history to authenticated;

-- 4) Restore recipe (manual, read-only to run; write the restore explicitly):
--    select hist_id, replaced_at, was_updated_at
--      from kinematik_project_history
--     where workspace_id = '<ws-uuid>' and id = 'default'
--     order by replaced_at desc;
--
--    update kinematik_workspace_projects p
--       set data = h.data, updated_at = now()
--      from kinematik_project_history h
--     where h.hist_id = <chosen hist_id>
--       and p.workspace_id = h.workspace_id and p.id = h.id;
--    (The restore itself triggers a snapshot of what it replaced — so even a
--     wrong restore is recoverable.)
