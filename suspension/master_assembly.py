# ============================================================================
#  KinematiK — Master Assembly compilation engine
#  Progressive reconciliation of true (uploaded) CAD with parametric dummy
#  bounding volumes, driven live by the kinematic hardpoint tables.
#
#  Design doc: master_assembly_design.md.  Core invariant:
#      DUMMIES CONFORM; TRUE CAD REPORTS.
#  A dummy recomputes its scale from the live anchor vectors on every solve;
#  a true CAD part is only ever placed rigidly, and a disagreement between
#  its registered geometry and the kinematics table raises a flag — it is
#  never "fixed" by scaling real geometry.
#
#  Frame: SAE car axes in mm (x rear, y right, z up), origin at the front-
#  axle plane / centreline / ground — the same frame fullcar3d and the
#  hardpoint tables already use.  Pure numpy; no Streamlit, no Supabase —
#  callers wire persistence and UI around this module.
# ============================================================================

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

# --------------------------------------------------------------------------
#  Constants (defaults mirror assembly_slots column defaults in the schema)
# --------------------------------------------------------------------------
DEFAULT_MARGIN_MM = 8.0        # clearance pad added to dummy extents
DEFAULT_MIN_DIM_MM = 15.0      # minimum-feature clamp per axis
DEFAULT_FIT_TOL_MM = 1.5       # τ_fit — max registration residual
DEFAULT_BRIDGE_RADIUS_MM = 12.0
_EPS = 1e-9

# Slot states (hot-swap finite state machine, §3.1 of the design doc)
DUMMY = "DUMMY"
UPLOADING = "UPLOADING"
REGISTERING = "REGISTERING"
VALIDATING = "VALIDATING"
TRUE_CAD = "TRUE_CAD"
MISFIT = "MISFIT"
QUARANTINED = "QUARANTINED"

_FSM: Dict[Tuple[str, str], str] = {
    (DUMMY, "upload_start"): UPLOADING,
    (UPLOADING, "upload_ok"): REGISTERING,
    (UPLOADING, "upload_fail"): QUARANTINED,
    (REGISTERING, "register_ok"): VALIDATING,
    (REGISTERING, "register_fail"): QUARANTINED,
    (VALIDATING, "validate_pass"): TRUE_CAD,
    (VALIDATING, "validate_block"): QUARANTINED,
    (TRUE_CAD, "anchors_out_of_tol"): MISFIT,
    (TRUE_CAD, "revert"): DUMMY,
    (MISFIT, "anchors_in_tol"): TRUE_CAD,
    (MISFIT, "revert"): DUMMY,
    (QUARANTINED, "revert"): DUMMY,
    (QUARANTINED, "upload_start"): UPLOADING,
}


def advance(state: str, event: str) -> str:
    """Hot-swap FSM transition. Unknown (state, event) pairs are no-ops —
    a stray event must never corrupt a slot's state."""
    return _FSM.get((state, event), state)


# Registration-confidence factor κ used by the Assembly Completion Index.
KAPPA = {
    "solved": 1.0,
    "roll_assumed": 0.9,
    "low_confidence": 0.75,
    "unregistered": 0.5,   # uploaded but never registered → half credit
}
KAPPA_MISFIT = 0.6         # geometry present but disagrees with kinematics


# --------------------------------------------------------------------------
#  Slot definitions
# --------------------------------------------------------------------------
@dataclass
class SlotDef:
    """A position a physical part can occupy, bound to kinematic anchors.

    anchor_keys : ordered hardpoint keys resolved by the caller to world mm.
    kind        : 'envelope' (k>=3 anchors, box dummy) or 'bridge' (k==2,
                  cylinder dummy spanning the two anchors).
    axis_pair   : indices into anchor_keys forming the primary axis u
                  (envelope only; e.g. kingpin = upper_outer - lower_outer).
    secondary   : anchor index whose offset from the centroid forms w.
    """
    slot_key: str
    subsystem: str
    anchor_keys: Tuple[str, ...]
    kind: str = "envelope"
    axis_pair: Tuple[int, int] = (0, 1)
    secondary: int = 2
    margin_mm: float = DEFAULT_MARGIN_MM
    min_dim_mm: float = DEFAULT_MIN_DIM_MM
    bridge_radius_mm: float = DEFAULT_BRIDGE_RADIUS_MM
    fit_tol_mm: float = DEFAULT_FIT_TOL_MM
    criticality: float = 1.0


@dataclass
class Flag:
    slot_key: str
    kind: str            # matches interference_flags.kind check constraint
    severity: str        # 'info' | 'warn' | 'block'
    detail: dict = field(default_factory=dict)


@dataclass
class SlotFit:
    """Resolved world placement of one slot for the current solve."""
    slot_key: str
    kind: str
    center: np.ndarray            # (3,) world mm
    rotation: np.ndarray          # (3,3) orthonormal, det = +1
    size: np.ndarray              # (3,) dummy L,W,H (or r,r,ℓ for bridges)
    flags: List[Flag] = field(default_factory=list)

    def aabb(self) -> Tuple[np.ndarray, np.ndarray]:
        """World axis-aligned bounds of the oriented box (broadphase)."""
        half = np.abs(self.rotation) @ (self.size / 2.0)
        return self.center - half, self.center + half

    def matrix(self) -> np.ndarray:
        """4x4 world matrix (T·R·S) — column-vector convention, mm."""
        m = np.eye(4)
        m[:3, :3] = self.rotation @ np.diag(self.size)
        m[:3, 3] = self.center
        return m


# --------------------------------------------------------------------------
#  §1.2 — slot frame from live anchors
# --------------------------------------------------------------------------
def slot_frame(anchors: np.ndarray, axis_pair=(0, 1), secondary=2
               ) -> Tuple[np.ndarray, np.ndarray, Optional[str]]:
    """Derive (centroid, R) from world anchor vectors.

    Returns (c, R, err) where err is None or 'degenerate_frame'.  On a
    degenerate configuration R falls back to identity — the CALLER should
    hold its previous frame instead (updateSlot kernel does exactly that);
    the identity fallback only exists so a cold start is still renderable.
    """
    a = np.asarray(anchors, float).reshape(-1, 3)
    c = a.mean(axis=0)
    u = a[axis_pair[0]] - a[axis_pair[1]]
    nu = np.linalg.norm(u)
    if nu < 1e-6:
        return c, np.eye(3), "degenerate_frame"
    u = u / nu
    w = a[secondary] - c
    w = w - np.dot(w, u) * u
    nw = np.linalg.norm(w)
    if nw < 1e-6:
        return c, np.eye(3), "degenerate_frame"
    w = w / nw
    v = np.cross(w, u)                       # right-handed: det(R) = +1
    R = np.column_stack([u, v, w])
    return c, R, None


# --------------------------------------------------------------------------
#  §1.3 — dummy fitting
# --------------------------------------------------------------------------
def fit_envelope_dummy(anchors: np.ndarray, sd: SlotDef) -> SlotFit:
    """Case A: k>=3 anchors → oriented box sized to the anchor cloud."""
    a = np.asarray(anchors, float).reshape(-1, 3)
    c, R, err = slot_frame(a, sd.axis_pair, sd.secondary)
    flags: List[Flag] = []
    if err:
        flags.append(Flag(sd.slot_key, "degenerate_frame", "warn",
                          {"n_anchors": int(len(a))}))
    p = (a - c) @ R                          # project into slot basis
    lo, hi = p.min(axis=0), p.max(axis=0)
    raw = hi - lo + 2.0 * sd.margin_mm
    size = np.maximum(raw, sd.min_dim_mm)
    if bool((raw < sd.min_dim_mm).any()):
        flags.append(Flag(sd.slot_key, "scale_clamped", "info",
                          {"raw_mm": raw.tolist(), "min_dim_mm": sd.min_dim_mm}))
    c = c + R @ ((hi + lo) / 2.0)            # midpoint offset, not centroid
    return SlotFit(sd.slot_key, "envelope", c, R, size, flags)


def fit_bridge_dummy(p0, p1, sd: SlotDef) -> SlotFit:
    """Case B: two anchors → cylinder of radius r spanning p0→p1.
    Local +z is the cylinder axis (matches the unit-primitive convention)."""
    p0 = np.asarray(p0, float)
    p1 = np.asarray(p1, float)
    d = p1 - p0
    ell = float(np.linalg.norm(d))
    flags: List[Flag] = []
    if ell < 1e-6:
        flags.append(Flag(sd.slot_key, "degenerate_frame", "warn",
                          {"reason": "coincident bridge anchors"}))
        return SlotFit(sd.slot_key, "bridge", (p0 + p1) / 2.0, np.eye(3),
                       np.array([sd.bridge_radius_mm, sd.bridge_radius_mm,
                                 sd.min_dim_mm]), flags)
    dh = d / ell
    z = np.array([0.0, 0.0, 1.0])
    dot = float(np.clip(np.dot(z, dh), -1.0, 1.0))
    if dot < -1.0 + 1e-9:                    # antiparallel: 180° about x̂
        R = np.diag([1.0, -1.0, -1.0])
    else:
        v = np.cross(z, dh)
        s = np.linalg.norm(v)
        if s < 1e-12:
            R = np.eye(3)
        else:                                # Rodrigues
            K = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
            R = np.eye(3) + K + K @ K * ((1 - dot) / (s * s))
    size = np.array([sd.bridge_radius_mm, sd.bridge_radius_mm, ell])
    return SlotFit(sd.slot_key, "bridge", (p0 + p1) / 2.0, R, size, flags)


# --------------------------------------------------------------------------
#  §1.4 — Kabsch/Umeyama rigid registration of an uploaded part
# --------------------------------------------------------------------------
@dataclass
class Registration:
    rotation: np.ndarray          # (3,3), det = +1 guaranteed
    translation: np.ndarray       # (3,) mm
    uniform_scale: float          # unit normalisation only (e.g. 1000 for glTF)
    residual_mm: float            # max_j ||R·s·q_j + t − p_j||
    residuals: np.ndarray         # per-connector
    confidence: str               # 'solved' | 'roll_assumed' | 'low_confidence'

    def apply(self, pts: np.ndarray) -> np.ndarray:
        return (self.uniform_scale * np.asarray(pts, float)) @ self.rotation.T \
               + self.translation

    def quaternion(self) -> np.ndarray:
        """(x, y, z, w) — matches cad_part_versions.reg_quaternion."""
        R = self.rotation
        t = np.trace(R)
        if t > 0:
            s = math.sqrt(t + 1.0) * 2
            return np.array([(R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s,
                             (R[1, 0] - R[0, 1]) / s, 0.25 * s])
        i = int(np.argmax(np.diag(R)))
        j, k = (i + 1) % 3, (i + 2) % 3
        s = math.sqrt(max(_EPS, 1.0 + R[i, i] - R[j, j] - R[k, k])) * 2
        q = np.zeros(4)
        q[i] = 0.25 * s
        q[j] = (R[j, i] + R[i, j]) / s
        q[k] = (R[k, i] + R[i, k]) / s
        q[3] = (R[k, j] - R[j, k]) / s
        return q


def register_part(connectors_local: np.ndarray, anchors_world: np.ndarray,
                  unit_scale: float = 1.0) -> Registration:
    """Closed-form rigid alignment of part-local connector points onto slot
    anchors.  `unit_scale` is the mm-normalisation factor (glTF metres →
    1000) and is the ONLY scaling ever applied to true CAD — uniform, known
    a priori, never estimated from the fit.
    """
    q = unit_scale * np.asarray(connectors_local, float).reshape(-1, 3)
    p = np.asarray(anchors_world, float).reshape(-1, 3)
    n = len(q)
    if n == 0 or n != len(p):
        raise ValueError("register_part: need equal, non-empty point sets")
    if n == 1:                               # translation only
        R = np.eye(3)
        t = p[0] - q[0]
        conf = "low_confidence"
    else:
        qc, pc = q.mean(axis=0), p.mean(axis=0)
        H = (q - qc).T @ (p - pc)
        U, _, Vt = np.linalg.svd(H)
        D = np.diag([1.0, 1.0, np.sign(np.linalg.det(Vt.T @ U.T))])  # no mirrors
        R = Vt.T @ D @ U.T
        t = pc - R @ qc
        conf = "roll_assumed" if n == 2 else "solved"
    res = np.linalg.norm((q @ R.T + t) - p, axis=1)
    return Registration(R, t, float(unit_scale), float(res.max()),
                        res, conf)


def check_registration(reg: Registration, sd: SlotDef,
                       anchor_names: Optional[Sequence[str]] = None
                       ) -> Optional[Flag]:
    """Level-1 interference check: residual vs the slot's τ_fit.  Returns a
    hardpoint_mismatch flag naming the worst anchor, or None if within tol.
    Called at upload (severity 'block' → QUARANTINED) and re-called on every
    hardpoint edit (severity 'warn' → MISFIT)."""
    if reg.residual_mm <= sd.fit_tol_mm:
        return None
    worst = int(np.argmax(reg.residuals))
    name = (anchor_names[worst] if anchor_names and worst < len(anchor_names)
            else f"connector[{worst}]")
    return Flag(sd.slot_key, "hardpoint_mismatch", "warn",
                {"worst_anchor": name,
                 "residual_mm": round(float(reg.residuals[worst]), 3),
                 "tolerance_mm": sd.fit_tol_mm})


# --------------------------------------------------------------------------
#  §4 — Level-2 broadphase interference (AABB sweep)
# --------------------------------------------------------------------------
def aabb_overlaps(fits: Sequence[SlotFit],
                  allow_pairs: Iterable[Tuple[str, str]] = ()) -> List[Flag]:
    """Pairwise world-AABB overlap minus the intended-contact allow-list.
    O(n²) on a few hundred boxes — microseconds; run on every solve."""
    allowed = {frozenset(p) for p in allow_pairs}
    boxes = [(f.slot_key, *f.aabb()) for f in fits]
    out: List[Flag] = []
    for i in range(len(boxes)):
        ki, lo_i, hi_i = boxes[i]
        for j in range(i + 1, len(boxes)):
            kj, lo_j, hi_j = boxes[j]
            if frozenset((ki, kj)) in allowed:
                continue
            if bool((lo_i <= hi_j).all() and (lo_j <= hi_i).all()):
                depth = np.minimum(hi_i, hi_j) - np.maximum(lo_i, lo_j)
                out.append(Flag(ki, "aabb_overlap", "warn",
                                {"other_slot": kj,
                                 "overlap_mm": [round(float(d), 1)
                                                for d in depth]}))
    return out


# --------------------------------------------------------------------------
#  §3.2 — Assembly Completion Index
# --------------------------------------------------------------------------
def assembly_completion_index(rows: Sequence[dict]) -> dict:
    """rows: [{'slot_key', 'volume_mm3', 'criticality', 'state',
               'reg_confidence'}, ...]
    Volume-weighted, criticality-weighted, discounted by registration
    quality κ — an unregistered blob upload scores less than a verified fit.
    Dummy volumes are live, so ACI legitimately moves when hardpoints move.
    """
    num = den = 0.0
    n_cad = 0
    per_subsystem: Dict[str, List[float]] = {}
    for r in rows:
        wv = float(r.get("criticality", 1.0)) * float(r.get("volume_mm3", 0.0))
        st = r.get("state", DUMMY)
        if st == TRUE_CAD:
            k = KAPPA.get(r.get("reg_confidence", "solved"), 1.0)
        elif st == MISFIT:
            k = KAPPA_MISFIT
        elif st == QUARANTINED and r.get("reg_confidence"):
            k = KAPPA["unregistered"]
        else:
            k = 0.0
        if st in (TRUE_CAD, MISFIT):
            n_cad += 1
        num += wv * k
        den += wv
        sub = r.get("subsystem", "other")
        per_subsystem.setdefault(sub, [0.0, 0.0])
        per_subsystem[sub][0] += wv * k
        per_subsystem[sub][1] += wv
    aci = (num / den) if den > 0 else 0.0
    return {
        "aci": aci,
        "n_slots": len(rows),
        "n_cad": n_cad,
        "total_volume_mm3": den,
        "per_subsystem": {s: (a / b if b > 0 else 0.0)
                          for s, (a, b) in per_subsystem.items()},
    }


def aci_from_part_boxes(part_boxes: Dict[str, dict],
                        custom_parts: Sequence[dict]) -> dict:
    """Bridge for the existing Streamlit app: compute the ACI from the boxes
    fullcar3d actually drew (fig._part_boxes: name → {centre, size}) plus
    the session's placed CAD parts (car3d_custom_parts).

    A drawn dummy replaced by a custom part counts as TRUE_CAD; the custom
    part's own L·W·H is its boundary volume; κ comes from its reconcile
    verdict when present ('fit_ok' → solved) else low_confidence.
    """
    replaced = set()
    for p in custom_parts or []:
        for dn in (p.get("replaces_drawnames") or []):
            replaced.add(dn)
        for key in ("replaces_drawname", "replaces_dummy"):
            if p.get(key):
                replaced.add(p[key])
    rows: List[dict] = []
    for name, box in (part_boxes or {}).items():
        if name in replaced:
            continue                          # its volume re-enters as CAD below
        size = box.get("size") or [0, 0, 0]
        vol = float(abs(size[0] * size[1] * size[2]))
        rows.append(dict(slot_key=name, volume_mm3=vol, criticality=1.0,
                         state=DUMMY, subsystem=box.get("subsys", "other")))
    for p in custom_parts or []:
        vol = float(abs(p.get("l_mm", 0.0) * p.get("w_mm", 0.0)
                        * p.get("h_mm", 0.0)))
        conf = "solved" if p.get("fit_ok") else "low_confidence"
        rows.append(dict(slot_key=p.get("name", "custom"), volume_mm3=vol,
                         criticality=1.0, state=TRUE_CAD, reg_confidence=conf,
                         subsystem=p.get("subsys", "other")))
    return assembly_completion_index(rows)


# --------------------------------------------------------------------------
#  §2 — commit hashing (content-addressed Master Assembly state)
# --------------------------------------------------------------------------
def commit_hash(entries: Sequence[dict]) -> str:
    """sha256 over the canonically sorted per-slot configuration.  Identical
    configurations hash identically across branches; the hash doubles as the
    rollback token.  entries: [{'slot_key', 'occupancy',
    'part_sha256'|None, 'dummy_params'|None}, ...]"""
    canon = sorted(
        (e["slot_key"], e.get("occupancy", "dummy"),
         e.get("part_sha256") or "",
         json.dumps(e.get("dummy_params") or {}, sort_keys=True,
                    separators=(",", ":")))
        for e in entries)
    return hashlib.sha256(
        json.dumps(canon, separators=(",", ":")).encode()).hexdigest()


# --------------------------------------------------------------------------
#  Default FSAE suspension slot catalog (per corner, 15-key hardpoint set)
# --------------------------------------------------------------------------
def corner_slots(corner: str) -> List[SlotDef]:
    """Slots for one corner ('fl','fr','rl','rr') over the standard hardpoint
    keys.  Envelope slots orient on real mechanical axes (upright: kingpin);
    bridges span exactly the gap their missing member must close."""
    c = corner
    return [
        SlotDef(f"susp.{c}.upright", "suspension",
                ("upper_outer", "lower_outer", "wheel_center"),
                kind="envelope", axis_pair=(0, 1), secondary=2,
                criticality=2.0),
        SlotDef(f"susp.{c}.uwb", "suspension",
                ("upper_front_inner", "upper_rear_inner", "upper_outer"),
                kind="envelope", axis_pair=(0, 1), secondary=2),
        SlotDef(f"susp.{c}.lwb", "suspension",
                ("lower_front_inner", "lower_rear_inner", "lower_outer"),
                kind="envelope", axis_pair=(0, 1), secondary=2,
                criticality=1.5),
        SlotDef(f"susp.{c}.pushrod", "suspension",
                ("pushrod_outer", "rocker_pushrod"), kind="bridge",
                bridge_radius_mm=9.0),
        SlotDef(f"susp.{c}.tierod", "suspension",
                ("tie_rod_inner", "tie_rod_outer"), kind="bridge",
                bridge_radius_mm=7.0),
        SlotDef(f"susp.{c}.spring", "suspension",
                ("rocker_spring", "spring_inner"), kind="bridge",
                bridge_radius_mm=20.0),
        SlotDef(f"susp.{c}.rocker", "suspension",
                ("rocker_pivot", "rocker_pushrod", "rocker_spring"),
                kind="envelope", axis_pair=(1, 2), secondary=0,
                margin_mm=5.0),
    ]


def evaluate_corner(corner: str, points: Dict[str, Sequence[float]],
                    slots: Optional[List[SlotDef]] = None
                    ) -> Tuple[List[SlotFit], List[Flag]]:
    """Fit every slot of one corner against its solved world hardpoints.
    `points`: hardpoint key → (x,y,z) world mm (e.g. _solved_corner_points).
    Slots whose anchors are missing from `points` are skipped — a partial
    kinematics table degrades to a partial dummy set, never to an error."""
    fits: List[SlotFit] = []
    flags: List[Flag] = []
    for sd in (slots if slots is not None else corner_slots(corner)):
        try:
            a = np.array([points[k] for k in sd.anchor_keys], float)
        except KeyError:
            continue
        f = (fit_bridge_dummy(a[0], a[1], sd) if sd.kind == "bridge"
             else fit_envelope_dummy(a, sd))
        fits.append(f)
        flags.extend(f.flags)
    # intended-contact pairs within a corner never flag against each other
    allow = []
    keys = [f.slot_key for f in fits]
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            allow.append((keys[i], keys[j]))
    # cross-corner / cross-subsystem overlaps are the caller's sweep; within
    # a corner everything is designed to touch, so suppress local pairs.
    flags.extend(aabb_overlaps(fits, allow_pairs=allow))
    return fits, flags
