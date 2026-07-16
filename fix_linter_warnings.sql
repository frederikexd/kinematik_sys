-- ============================================================================
--  KinematiK — Supabase linter remediation
--  Run in: Supabase SQL Editor.
--
--  Addresses the warnings from the Database Linter:
--    1. CRITICAL  Security Definer View     → public.v_time_to_first_result (+ all v_*)
--    2. WARN      Auth RLS Initialization Plan → public.workspaces
--    3. WARN      Auth RLS Initialization Plan → public.workspace_members
--    4. WARN      Multiple Permissive Policies → public.kinematik_project (DIAGNOSTIC ONLY)
--
--  Idempotent. Safe to re-run.
-- ============================================================================


-- ----------------------------------------------------------------------------
--  1. SECURITY DEFINER VIEW (CRITICAL) — run every view as the CALLER
-- ----------------------------------------------------------------------------
--  A view without security_invoker runs with the OWNER's privileges and can
--  bypass RLS — that's why it's flagged CRITICAL. Setting security_invoker = on
--  makes it run as the caller so RLS is honoured.
--
--  PRECONDITION: the calling roles need SELECT on the base tables, or invoker
--  views raise 42501 "permission denied" and the dashboard goes blank. The base
--  tables already carry open read policies (ae_read using(true)), so granting
--  table SELECT is safe and makes invoker reads work.
--
--  NOTE: this is the same fix already present in analytics_hardening.sql §15.
--  If you run that file, you don't need this block. It's repeated here so this
--  script stands alone.
do $$
declare
    _t text;
    _tables text[] := array[
        'analytics_events', 'feature_baselines', 'feature_releases',
        'analytics_config'
    ];
begin
    foreach _t in array _tables loop
        if exists (select 1 from information_schema.tables
                   where table_schema='public' and table_name=_t) then
            execute format(
                'grant select on public.%I to anon, authenticated, service_role', _t);
        end if;
    end loop;
end $$;

do $$
declare
    _v text;
begin
    -- Apply to EVERY view in public schema so no v_* is left in definer mode.
    for _v in
        select table_name from information_schema.views
        where table_schema = 'public'
    loop
        execute format('alter view public.%I set (security_invoker = on)', _v);
        execute format(
            'grant select on public.%I to anon, authenticated, service_role', _v);
    end loop;
end $$;


-- ----------------------------------------------------------------------------
--  2 & 3. AUTH RLS INITIALIZATION PLAN — wrap auth.uid() in a scalar subquery
-- ----------------------------------------------------------------------------
--  `auth.uid()` called bare in a policy is re-evaluated PER ROW. Wrapping it as
--  `(select auth.uid())` lets Postgres evaluate it ONCE per query (an initplan),
--  which is the entire fix — identical behaviour, much less per-row work on
--  large scans. We recreate the affected policies with the wrapped form.
--
--  These mirror the definitions in workspace_isolation.sql; only the auth.uid()
--  calls change. The helper functions is_workspace_member()/workspace_role()
--  already run SECURITY DEFINER with a pinned search_path, so they're untouched.

-- workspaces --------------------------------------------------------------- --
drop policy if exists ws_insert on public.workspaces;
create policy ws_insert on public.workspaces for insert to authenticated
    with check (created_by = (select auth.uid()));

-- (ws_select/ws_update/ws_delete use is_workspace_member()/workspace_role(),
--  not bare auth.uid(), so they don't trigger the initplan warning. Left as-is.)

-- workspace_members -------------------------------------------------------- --
drop policy if exists wm_delete on public.workspace_members;
create policy wm_delete on public.workspace_members for delete
    using (workspace_role(workspace_id) = 'owner'
           or user_id = (select auth.uid()));

-- (wm_select/wm_insert/wm_update use the helper functions, not bare auth.uid().)

--  If the linter still flags either table after this, list the exact policies
--  and their expressions so the remaining bare auth.uid() can be found:
--    select policyname, cmd, qual, with_check
--    from pg_policies
--    where schemaname='public' and tablename in ('workspaces','workspace_members');


-- ----------------------------------------------------------------------------
--  4. MULTIPLE PERMISSIVE POLICIES — public.kinematik_project (DIAGNOSTIC)
-- ----------------------------------------------------------------------------
--  This means TWO OR MORE permissive policies exist for the same role + command
--  on kinematik_project, so Postgres evaluates ALL of them on every query
--  (a performance cost, not a security hole).
--
--  These policies are NOT in the provided codebase — they were created by an
--  earlier migration run directly against the database — so this script does
--  NOT drop anything here (dropping a policy you can't see risks removing access
--  you rely on). Run this to SEE the duplicates, then decide what to merge:
select
    policyname,
    cmd            as command,
    roles,
    permissive,
    qual           as using_expr,
    with_check
from pg_policies
where schemaname = 'public'
  and tablename  = 'kinematik_project'
order by cmd, policyname;

--  TO FIX once you can see them: keep ONE permissive policy per (role, command)
--  and fold the others' conditions into it with OR, e.g.
--     drop policy dup_a on public.kinematik_project;
--     drop policy dup_b on public.kinematik_project;
--     create policy kp_select on public.kinematik_project for select
--         using (<condition_a> or <condition_b>);
--  Only do this after confirming the merged condition preserves the access you
--  intend — verify against a known member and non-member.


-- ----------------------------------------------------------------------------
--  VERIFY — no public view should remain in definer mode.
-- ----------------------------------------------------------------------------
select c.relname as view_still_definer
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relkind = 'v'
  and not coalesce(
        (select option_value::boolean
         from pg_options_to_table(c.reloptions)
         where option_name = 'security_invoker'), false);
-- Expect: zero rows.
