# KinematiK — team-accounts rollout plan

The self-serve engine (sign-in gate, workspaces, invite links, backup/restore
migration) is built, tested, and dormant behind one line in
`auth_ui.require_workspace`. This is the sequence for flipping it without
burning the trust that got you 100+ users in three days.

## Principles

Users forgive change; they don't forgive surprise or data loss. Everything
below exists to guarantee three sentences stay true: "you were warned",
"your work survived", "it's better on the other side".

## Sequence

**T-14 days — arm the banner.** Set the `MIGRATION_NOTICE` secret to the
deadline text (e.g. `August 15`). Every local-mode user now sees the banner
with the one-click ungated backup download. No code deploy needed. Post the
announcement (below) in the Discord the same day.

**T-14 → T-0 — watch and remind.** The analytics tab tells you how many
sessions saw the banner. Re-post a short reminder at T-7 and T-1. During this
window, run the Supabase SQL in order if not already applied:
`workspace_isolation.sql` → `workspace_members_rpc.sql` →
`project_history.sql` → `workspace_invites.sql`. Set the `APP_BASE_URL`
secret so invite links come out clickable. Create YOUR workspace (Elbee) and
mint its invite link ahead of time.

**T-0 — flip the gate.** Delete the marked `return None` in
`auth_ui.require_workspace`. Deploy. From this moment: sign-in wall, invited
users auto-join, fresh workspaces offer the restore-backup prompt, everything
saves server-side with locking + history.

**T-0 — the Elbee migration is the dress rehearsal.** Drop your own team's
invite link in your team chat first. 25 people joining, restoring backups,
and hitting real concurrency for a week is the QA pass before you hand
invite links to other FSAE teams.

**T+7 — open the doors.** Post in the wider SAE Discord: any team lead can
create a workspace and invite their team in one link. This is the moment the
funnel switches from individuals to teams.

## Rollback

The gate is one line; reverting it restores local mode instantly. Server-side
data is untouched either way. If redemption or sign-up misbehaves at T-0,
revert, fix, re-flip — users lose nothing but see the wall disappear.

## What to watch after the flip

Sign-up completion rate (email confirm is where funnels die — if Supabase
email templates aren't customised, do it before T-0: the default emails look
like phishing), invite redemptions per created link (the team-multiplier
metric), restore-prompt usage (did the migration message land), and the
save-conflict banner frequency (real concurrency arriving — expected, healthy,
but a spike means a UX problem in a specific tab).

---

## Discord announcement (draft — post at T-14)

> **KinematiK is getting team accounts — here's what you need to do (2 min)**
>
> On **[DATE]**, KinematiK moves from anonymous sessions to team workspaces.
> What you get: your whole team in one shared project — declarations, weights,
> and decisions that **survive restarts**, a full version history of who
> changed what (with one-click restore), and an invite link so your lead adds
> the entire team in one paste instead of everyone working in silos.
>
> What you need to do **before [DATE]**: open KinematiK, click
> **"Download my project backup"** in the banner at the top, and keep the
> file. After the switch, sign in, join your team's workspace, and restore it
> in one click — you'll be exactly where you left off.
>
> Nothing about the tools changes. Nothing gets slower. Your data stays
> yours (it's still open source, still AGPL, still no ads, and the privacy
> policy is in the README).
>
> Questions or something breaks: post here, I read everything.

Keep the tone exactly this dry. The users trust the tool because it never
oversells; the announcement shouldn't either.
