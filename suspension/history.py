# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""
history.py — project version history: fetch, human-readable diff, restore
==========================================================================

The database keeps the last 20 versions of every project blob (see
`project_history.sql` — a Postgres trigger snapshots the previous blob on
every overwrite, server-side, so capture can never be forgotten). This module
turns those snapshots into something an engineer can read:

  * ``fetch_history(backend)``      — list the snapshots the signed-in user may see
  * ``diff_project(old, new)``      — the CHANGES between two blobs, in plain
                                      engineering language ("target mass 230 → 228 kg",
                                      "weight added: Battery segment box, 4.2 kg (electrics)")
  * ``restore(store, blob)``        — make an old version the current one, THROUGH
                                      the store's optimistic lock (so a restore
                                      can't clobber a teammate's newer save, and
                                      the trigger snapshots what it replaced —
                                      even a wrong restore is undoable)

Design rules (same as the rest of the platform):
  * Pure Python + stdlib at import; supabase only touched through the backend
    the caller hands in. Importable and testable with no Streamlit.
  * Never raises to the UI for a missing table / no permission / no rows —
    those come back as an empty list with a reason the panel can show.
  * The diff never guesses. If two blobs differ somewhere the differ doesn't
    model, it says "other fields changed" rather than staying silent — an
    audit trail that hides changes is worse than none.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = [
    "Snapshot", "Change", "fetch_history", "diff_project", "restore",
    "summarize_changes",
]


# --------------------------------------------------------------------------- #
#  Data shapes
# --------------------------------------------------------------------------- #
@dataclass
class Snapshot:
    """One historical version of the project blob."""
    hist_id: int
    replaced_at: str            # when this version STOPPED being current
    was_updated_at: Optional[str]
    data: dict = field(repr=False, default_factory=dict)

    @property
    def label(self) -> str:
        ts = self.data.get("updated") or self.was_updated_at or self.replaced_at
        by = self.data.get("saved_by")
        return f"{ts} — saved by {by}" if by else f"{ts}"


@dataclass
class Change:
    """One human-readable difference between two project versions."""
    kind: str        # "added" | "removed" | "changed"
    area: str        # "weights" | "decisions" | "notes" | "cad" | "params" | ...
    summary: str     # the sentence shown to the user

    def __str__(self):
        icon = {"added": "＋", "removed": "－", "changed": "Δ"}.get(self.kind, "·")
        return f"{icon} [{self.area}] {self.summary}"


# --------------------------------------------------------------------------- #
#  Fetch
# --------------------------------------------------------------------------- #
def fetch_history(backend, limit: int = 20) -> tuple[list[Snapshot], Optional[str]]:
    """Return (snapshots newest-first, reason) for the backend's project.

    ``reason`` is None on success, else a short human sentence for the panel
    ("history isn't stored for local projects", "history table not reachable").
    Works only for the workspace-scoped Supabase backend; every other backend
    honestly reports that history isn't available rather than pretending.
    """
    client = getattr(backend, "client", None)
    ctx = getattr(backend, "ctx", None)
    project_id = getattr(backend, "project_id", None)
    if client is None or ctx is None:
        return [], ("Version history is stored server-side and needs the "
                    "cloud (Supabase) backend — local projects don't have it.")
    try:
        resp = (client.table("kinematik_project_history")
                .select("hist_id,replaced_at,was_updated_at,data")
                .eq("workspace_id", ctx.workspace_id)
                .eq("id", project_id)
                .order("replaced_at", desc=True)
                .limit(int(limit))
                .execute())
        rows = resp.data or []
    except Exception as e:
        return [], (f"Couldn't read the history table "
                    f"({type(e).__name__}) — has suspension/project_history.sql "
                    f"been run in Supabase?")
    out = [Snapshot(hist_id=r.get("hist_id"),
                    replaced_at=str(r.get("replaced_at") or ""),
                    was_updated_at=r.get("was_updated_at"),
                    data=r.get("data") or {}) for r in rows]
    return out, None


# --------------------------------------------------------------------------- #
#  Diff
# --------------------------------------------------------------------------- #
def _fmt_kg(g: Any) -> str:
    try:
        return f"{float(g) / 1000.0:.2f} kg"
    except Exception:
        return str(g)


def _index(items: list, *keys: str) -> dict:
    """Index a list of dicts by the first non-empty combination of keys.
    Duplicate keys get a positional suffix so nothing is silently merged."""
    out: dict = {}
    for i, it in enumerate(items or []):
        if not isinstance(it, dict):
            it = dict(it) if hasattr(it, "keys") else {"_raw": it}
        k = tuple(str(it.get(k, "")) for k in keys)
        if all(v == "" for v in k):
            k = (f"#{i}",)
        while k in out:
            k = k + ("dup",)
        out[k] = it
    return out


def _diff_keyed_list(old: list, new: list, *, keys: tuple,
                     area: str, describe, changed_fields: tuple) -> list[Change]:
    """Generic add/remove/change diff for a list of dicts keyed by `keys`."""
    o, n = _index(old, *keys), _index(new, *keys)
    changes: list[Change] = []
    for k in n.keys() - o.keys():
        changes.append(Change("added", area, describe(n[k])))
    for k in o.keys() - n.keys():
        changes.append(Change("removed", area, describe(o[k])))
    for k in n.keys() & o.keys():
        deltas = []
        for f in changed_fields:
            ov, nv = o[k].get(f), n[k].get(f)
            if ov != nv:
                if f == "mass_g":
                    deltas.append(f"mass {_fmt_kg(ov)} → {_fmt_kg(nv)}")
                else:
                    deltas.append(f"{f} {ov!r} → {nv!r}")
        if deltas:
            changes.append(Change("changed", area,
                                  f"{describe(n[k])}: " + ", ".join(deltas)))
    return changes


_SCALARS = (
    ("team_name", "team name", str),
    ("season", "season", str),
    ("target_mass_kg", "target mass", lambda v: f"{float(v):g} kg"),
)


def diff_project(old: dict, new: dict) -> list[Change]:
    """Human-readable changes from `old` blob to `new` blob.

    Models the fields engineers care about explicitly; anything else that
    differs is reported as a catch-all line so no change can hide."""
    old, new = old or {}, new or {}
    changes: list[Change] = []

    # ---- top-level scalars ------------------------------------------------ #
    for key, label, fmt in _SCALARS:
        ov, nv = old.get(key), new.get(key)
        if ov != nv and not (ov is None and nv is None):
            try:
                o_s = fmt(ov) if ov is not None else "—"
                n_s = fmt(nv) if nv is not None else "—"
            except Exception:
                o_s, n_s = str(ov), str(nv)
            changes.append(Change("changed", "params", f"{label}: {o_s} → {n_s}"))

    # ---- weights (the ledger everyone tunes to) --------------------------- #
    changes += _diff_keyed_list(
        old.get("weights", []), new.get("weights", []),
        keys=("team", "name"), area="weights",
        describe=lambda w: (f"{w.get('name', '?')} "
                            f"({w.get('team', '?')}, {_fmt_kg(w.get('mass_g', 0))}"
                            f"{' ×' + str(w.get('qty')) if (w.get('qty') or 1) != 1 else ''})"),
        changed_fields=("mass_g", "qty", "material", "source", "note"))

    # ---- decisions (the WHY next year inherits) ---------------------------- #
    changes += _diff_keyed_list(
        old.get("decisions", []), new.get("decisions", []),
        keys=("team", "title", "date"), area="decisions",
        describe=lambda d: (f"“{d.get('title', '?')}” "
                            f"({d.get('team', '?')}, {d.get('date', '')})"),
        changed_fields=("rationale", "tags", "part", "author"))

    # ---- cross-team notes --------------------------------------------------#
    changes += _diff_keyed_list(
        old.get("notes", []), new.get("notes", []),
        keys=("from_team", "to_team", "message"), area="notes",
        describe=lambda x: (f"{x.get('from_team', '?')} → {x.get('to_team', '?')}: "
                            f"{str(x.get('message', ''))[:60]}"),
        changed_fields=("resolved",) )

    # ---- CAD library -------------------------------------------------------#
    changes += _diff_keyed_list(
        old.get("cad_files", []), new.get("cad_files", []),
        keys=("name",), area="cad",
        describe=lambda c: f"{c.get('name', '?')} ({c.get('subsystem', '?')})",
        changed_fields=("subsystem", "note"))

    # ---- structured ledgers: count-level, never silent -------------------- #
    for key, label, counter in (
            ("geometry", "mount points / keep-outs",
             lambda g: len((g or {}).get("points", []) or []) +
                       len((g or {}).get("keepouts", []) or [])),
            ("board", "PCB traces/nets",
             lambda b: len((b or {}).get("traces", []) or [])),
            ("harness", "harness runs",
             lambda h: len((h or {}).get("runs", []) or []))):
        try:
            oc, nc = counter(old.get(key)), counter(new.get(key))
        except Exception:
            oc = nc = None
        if oc != nc and oc is not None:
            changes.append(Change("changed", key, f"{label}: {oc} → {nc}"))
        elif oc == nc and (old.get(key) or {}) != (new.get(key) or {}):
            changes.append(Change("changed", key,
                                  f"{label}: details edited (same count)"))

    # ---- Integration ledger: the cross-team declarations ------------------ #
    _ol, _nl = old.get("ledger") or {}, new.get("ledger") or {}
    if _ol != _nl:
        oi = (_ol.get("interfaces") or {})
        ni = (_nl.get("interfaces") or {})
        for sub in sorted(ni.keys() - oi.keys()):
            changes.append(Change("added", "ledger",
                                  f"{sub}: declaration published"))
        for sub in sorted(oi.keys() - ni.keys()):
            changes.append(Change("removed", "ledger",
                                  f"{sub}: declaration withdrawn"))
        for sub in sorted(oi.keys() & ni.keys()):
            osub, nsub = oi[sub] or {}, ni[sub] or {}
            if osub == nsub:
                continue
            moved = []
            for f in sorted(set(osub) | set(nsub)):
                ov, nv = osub.get(f), nsub.get(f)
                if ov == nv:
                    continue
                if isinstance(ov, (int, float)) or isinstance(nv, (int, float)):
                    moved.append(f"{f} {ov} → {nv}")
                else:
                    moved.append(f)
                if len(moved) >= 5:
                    moved.append("…")
                    break
            changes.append(Change("changed", "ledger",
                                  f"{sub}: " + (", ".join(moved) or "edited")))
        # car-level budgets/limits on the ledger itself
        car_moved = [f"{k} {_ol.get(k)} → {_nl.get(k)}"
                     for k in sorted(set(_ol) | set(_nl))
                     if k != "interfaces" and _ol.get(k) != _nl.get(k)]
        if car_moved:
            changes.append(Change("changed", "ledger",
                                  "car-level budgets: " + ", ".join(car_moved[:5])))

    # ---- ev excel params ---------------------------------------------------#
    oe, ne = old.get("ev_excel_params") or {}, new.get("ev_excel_params") or {}
    if oe != ne:
        moved = sorted(set(list(oe.keys()) + list(ne.keys())))
        moved = [k for k in moved if oe.get(k) != ne.get(k)][:6]
        changes.append(Change("changed", "ev",
                              "EV electrical params updated"
                              + (f" ({', '.join(moved)})" if moved else "")))

    # ---- catch-all: nothing may hide from the audit trail ------------------#
    MODELLED = {"team_name", "season", "target_mass_kg", "weights", "decisions",
                "notes", "cad_files", "geometry", "board", "harness",
                "ev_excel_params", "ledger", "updated", "workspace_id", "_written_at",
                "saved_by"}
    other = sorted((set(old) | set(new)) - MODELLED)
    other = [k for k in other if old.get(k) != new.get(k)]
    if other:
        changes.append(Change("changed", "other",
                              f"other fields changed: {', '.join(other[:8])}"))
    return changes


def summarize_changes(changes: list[Change], max_lines: int = 4) -> str:
    """Short one-liner for the timeline row: '3 weights, 1 decision, target mass'."""
    if not changes:
        return "no modelled changes (metadata only)"
    from collections import Counter
    c = Counter(ch.area for ch in changes)
    parts = [f"{n}× {area}" if n > 1 else area for area, n in c.most_common(max_lines)]
    if len(c) > max_lines:
        parts.append("…")
    return ", ".join(parts)


# --------------------------------------------------------------------------- #
#  Restore
# --------------------------------------------------------------------------- #
def restore(store, blob: dict) -> tuple[bool, str]:
    """Make `blob` (a historical version) the CURRENT project, through the
    store's normal apply+save path.

    Safety properties, in order:
      * goes through the optimistic lock — if a teammate saved something newer
        than what this session loaded, the restore is refused with the normal
        conflict message instead of clobbering them;
      * the save is a fresh payload with a fresh `updated` stamp (never a
        byte-replay of the old blob), so the version chain stays linear;
      * the database trigger snapshots the version being replaced — a wrong
        restore is itself one more restore away from undone.

    Returns (ok, message). Never raises.
    """
    try:
        keep = dict(blob or {})
        keep.pop("updated", None)          # _payload() stamps a fresh one
        keep.pop("_written_at", None)

        # _apply() MERGES (absent keys keep current values) — right for load,
        # wrong for restore: reproducing the old version must also CLEAR what
        # didn't exist then. Reset the applied fields to their defaults first.
        store.team_name = "Elbee Racing"
        store.season = str(_dt.date.today().year)
        store.target_mass_kg = 230.0
        store.weights, store.decisions = [], []
        store.notes, store.cad_files = [], []
        store.ev_excel_params = {}
        store.ledger = {}
        for attr, mod, cls in (("geometry", "mountpoints", "GeometryLedger"),
                               ("board", "electronics", "BoardLedger"),
                               ("harness", "harness", "HarnessLedger")):
            try:
                m = __import__(f"suspension.{mod}", fromlist=[cls])
                setattr(store, attr, getattr(m, cls)())
            except Exception:
                setattr(store, attr, None)

        # Schema drift tolerance: an old snapshot may carry item fields that a
        # NEWER dataclass no longer has (WeightItem(**w) would raise). Filter
        # each item to the current schema; report don't crash.
        import dataclasses as _dc
        from .project import WeightItem, Decision, Note, CADFile
        for key, cls in (("weights", WeightItem), ("decisions", Decision),
                         ("notes", Note), ("cad_files", CADFile)):
            items = keep.get(key)
            if not items:
                continue
            allowed = {f.name for f in _dc.fields(cls)}
            keep[key] = [{k: v for k, v in dict(it).items() if k in allowed}
                         for it in items]

        store._apply(keep)
        ok = store.save()
        if ok:
            return True, ("Restored. The replaced version was snapshotted "
                          "server-side, so this restore is also undoable.")
        msg = getattr(store, "save_conflict", None) or \
            getattr(store, "save_error", None) or "save failed"
        return False, str(msg)
    except Exception as e:
        return False, f"Restore failed: {type(e).__name__}: {e}"
