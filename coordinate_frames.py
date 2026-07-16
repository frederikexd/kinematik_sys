# -------------------------------------------------------------------------- #
#  KinematiK — Coordinate Frames & Floating Datums
#  Created for the KinematiK project (c) 2026. AGPL-3.0.
#
#  WHY THIS MODULE EXISTS
#  ----------------------
#  Every formula team has had this exact Discord argument:
#
#    "should i change my model to sae coordinates? its just weird cause the
#     'top plane' changes to like the side"
#    "honestly it might be a full redo cause i have a lot of measurements
#     that are plane-specific"
#    "I was asking how something affected packaging in x and y and someone
#     was like: wait, what are we defining as x and y"
#    "we won't rly know the center of gravity until the master assembly is
#     completely put together... but the chassis changes length sometimes,
#     so relativity to the front axle changes too"
#
#  Four distinct failures hide in that thread:
#
#    1. NO DECLARED CONVENTION.  x/y/z mean different things to different
#       subteams (SolidWorks default vs SAE J670 vs ISO 8855 vs ISO 4130),
#       so numbers get exchanged without a frame attached and silently
#       contradict each other.
#    2. MIGRATION IS PRICED AS "A FULL REDO".  Nobody switches to the right
#       convention because converting hundreds of plane-specific
#       measurements by hand is days of work, so the debt compounds.
#    3. THE ORIGIN FLOATS.  CG isn't known until the car is final; the
#       chassis changes length so even "distance from front axle" drifts.
#       Measurements pinned to a moving datum rot without anyone noticing.
#    4. NOBODY CAN DEFEND IT AT DESIGN JUDGING.  "Idk if judges prefer it" —
#       teams either avoid mentioning directions or risk saying them wrong.
#
#  This module fixes all four:
#
#    * A TEAM CONVENTION CHARTER — one declared frame + master datum for the
#      whole project, visible to every subteam, logged as a Decision.
#    * EXACT FRAME MATHS — right-handed rotation matrices between ISO 8855,
#      SAE J670, ISO 4130, the KinematiK internal frame, a typical
#      SolidWorks setup, and any custom frame built from direction words.
#      Points, free vectors (forces/moments/rates) and rotation senses
#      (which way is +roll/+pitch/+yaw) all convert correctly.
#    * A MIGRATION WIZARD — convert the live hardpoint set or any pasted /
#      uploaded CSV between frames and datums in one click, with a
#      per-point audit and a SolidWorks "Curve Through XYZ Points" export.
#      The "full redo" becomes a two-minute batch job.
#    * FLOATING DATUMS — datums (front axle, rear axle, mid-wheelbase, CG)
#      are resolved LIVE from the vehicle parameters / Integration numbers.
#      When wheelbase or CG moves, the tool shows exactly how far every
#      datum drifted since the charter was saved, instead of letting
#      CG-relative measurements silently rot.
#    * A SIGN-CONVENTION LINTER — mirror-pair symmetry, above-ground,
#      unit-sniff and envelope checks on any point set, each defect with a
#      plain-English fix (same per-defect style as the Frame Planner audit).
#    * A JUDGE-READY CHARTER EXPORT — a one-page markdown statement of the
#      convention, rotation senses and phrasebook, so anyone on the team
#      can answer a design judge in one sentence.
#
#  All maths is pure Python + tiny helpers (no numpy dependency needed for
#  3-vectors) so this file is importable and unit-testable without
#  Streamlit.  `render()` at the bottom is the Streamlit tab body.
# -------------------------------------------------------------------------- #

from __future__ import annotations

import csv
import io
import json
import math
import datetime as _dt
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
#  WORLD REFERENCE
#  All frames are expressed relative to one fixed "world" basis so every
#  conversion is a single, auditable path:  frame A -> world -> frame B.
#  World = ISO 8855 axes ( +X forward, +Y left, +Z up ), origin at the
#  CENTRE OF THE FRONT AXLE PROJECTED TO GROUND.  The world basis is an
#  internal bookkeeping choice only — no user-facing number depends on it.
# --------------------------------------------------------------------------- #

_WORLD_WORDS = {          # world unit vectors, by plain-English direction
    "forward":  (1.0, 0.0, 0.0),
    "rearward": (-1.0, 0.0, 0.0),
    "left":     (0.0, 1.0, 0.0),
    "right":    (0.0, -1.0, 0.0),
    "up":       (0.0, 0.0, 1.0),
    "down":     (0.0, 0.0, -1.0),
}
DIRECTION_WORDS = list(_WORLD_WORDS.keys())

# ---- tiny 3-vector helpers (avoid a numpy hard-dependency here) ----------- #

def _dot(a, b):   return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]
def _cross(a, b): return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
def _sub(a, b):   return (a[0]-b[0], a[1]-b[1], a[2]-b[2])
def _add(a, b):   return (a[0]+b[0], a[1]+b[1], a[2]+b[2])
def _norm(a):     return math.sqrt(_dot(a, a))

def _word_for(v, tol=1e-9):
    """Plain-English word for a unit vector that is axis-aligned; None otherwise."""
    for w, u in _WORLD_WORDS.items():
        if abs(v[0]-u[0]) < tol and abs(v[1]-u[1]) < tol and abs(v[2]-u[2]) < tol:
            return w
    return None


@dataclass(frozen=True)
class Frame:
    """A right-handed vehicle coordinate frame.

    `basis` holds the frame's x, y, z unit axes EXPRESSED IN WORLD
    coordinates (columns of the rotation matrix world<-frame).  Because all
    frames here are proper rotations (det = +1), points, free vectors and
    rotation/moment vectors all transform with the same matrix — only points
    additionally shift by the datum origin.
    """
    key: str
    name: str
    basis: tuple            # ((xw),(yw),(zw)) — each a world 3-tuple
    origin_note: str
    blurb: str
    standard: str = ""

    # -- transforms -------------------------------------------------------- #
    def to_world_vec(self, v):
        bx, by, bz = self.basis
        return (bx[0]*v[0] + by[0]*v[1] + bz[0]*v[2],
                bx[1]*v[0] + by[1]*v[1] + bz[1]*v[2],
                bx[2]*v[0] + by[2]*v[1] + bz[2]*v[2])

    def from_world_vec(self, w):
        bx, by, bz = self.basis
        return (_dot(bx, w), _dot(by, w), _dot(bz, w))

    def to_world_point(self, p, origin_world):
        return _add(origin_world, self.to_world_vec(p))

    def from_world_point(self, w, origin_world):
        return self.from_world_vec(_sub(w, origin_world))

    # -- description ------------------------------------------------------- #
    def axis_words(self):
        """('forward','left','up')-style words for +x,+y,+z (or 'skewed')."""
        return tuple(_word_for(a) or "skewed" for a in self.basis)

    def rotation_senses(self):
        """Human meaning of a POSITIVE right-hand rotation about each frame
        axis.  Computed, not hard-coded, so custom frames get it right too —
        this is where SAE vs ISO famously disagree (+pitch nose-up vs
        nose-down, +yaw right vs left)."""
        out = []
        nose = _WORLD_WORDS["forward"]; roof = _WORLD_WORDS["up"]
        axis_role = {"forward": "roll", "rearward": "roll",
                     "left": "pitch", "right": "pitch",
                     "up": "yaw", "down": "yaw"}
        for label, a in zip(("x", "y", "z"), self.basis):
            role = axis_role.get(_word_for(a) or "", "rotation")
            probe = nose if _norm(_cross(a, nose)) > 1e-9 else roof
            probe_name = "nose" if probe is nose else "roof"
            moved = _cross(a, probe)
            n = _norm(moved)
            word = _word_for(tuple(c/n for c in moved)) if n > 1e-9 else None
            sense = f"{probe_name} swings {word}" if word else "see axis triad"
            out.append((label, role, sense))
        return out


def _f(key, name, xw, yw, standard, origin_note, blurb):
    """Build a Frame from two axis words; z derived so it is ALWAYS a valid
    right-handed frame (this is the whole point — no team can accidentally
    declare a left-handed convention)."""
    x = _WORLD_WORDS[xw]; y = _WORLD_WORDS[yw]
    if abs(_dot(x, y)) > 1e-9:
        raise ValueError(f"{xw} and {yw} are not perpendicular")
    z = _cross(x, y)
    return Frame(key, name, (x, y, z), origin_note, blurb, standard)


BUILTIN_FRAMES = {
    "iso8855": _f(
        "iso8855", "ISO 8855 (vehicle dynamics)", "forward", "left",
        "ISO 8855:2011",
        "Origin at the sprung-mass CG (simulation) — pick the CG datum.",
        "Z-up, X-forward, Y-left. The frame every vehicle-dynamics textbook "
        "and most simulation (CAE) tools assume. Normal forces are positive "
        "upward. +pitch is nose-DOWN, +yaw is a LEFT turn — computed below, "
        "not memorised."),
    "sae_j670": _f(
        "sae_j670", "SAE J670 (Z-down)", "forward", "right",
        "SAE J670",
        "Origin conventionally at the CG.",
        "Z-DOWN so that tyre normal forces come out positive into the "
        "ground. X-forward, Y-right. +pitch is nose-UP, +yaw is a RIGHT "
        "turn — the mirror image of ISO 8855 in two of three senses, which "
        "is exactly why quoting bare x/y/z numbers without a frame tag "
        "starts arguments."),
    "iso4130": _f(
        "iso4130", "ISO 4130 (vehicle design / CAD)", "rearward", "right",
        "ISO 4130:1978",
        "Origin at the centre of the front axle — pick the front-axle datum.",
        "X-rearward so that station lines grow positive down the length of "
        "the car (how bodies-in-white are dimensioned). Y-right, Z-up. The "
        "natural frame for a CAD master model."),
    "kinematik": _f(
        "kinematik", "KinematiK internal (hardpoint editor)", "rearward", "right",
        "ISO 4130-style axes",
        "Origin at the front-axle / kingpin reference used by the solver.",
        "x-rear, y-right, z-up — the frame the hardpoint editor and the 3D "
        "constraint solver use. NOTE: this is ISO 4130-STYLE, not SAE J670 "
        "(SAE is Z-down). The editor header used to say 'SAE', which is the "
        "same mislabel that starts the Discord argument — fixed."),
    "sw_front": _f(
        "sw_front", "SolidWorks default (Y-up, Front Plane = front view)",
        "left", "up",
        "SolidWorks template",
        "Origin wherever the first sketch was anchored — usually arbitrary.",
        "The 'we just started modelling' frame: Y-up, Z out of the Front "
        "Plane toward the viewer (= vehicle forward), X to the vehicle "
        "left. Its 'Top Plane' is the car's plan view only if the car was "
        "modelled nose-along-Z — which is why switching conventions makes "
        "the top plane 'change to like the side'. If your master model "
        "differs, build a Custom frame below instead of guessing."),
}
FRAME_ORDER = ["iso8855", "sae_j670", "iso4130", "kinematik", "sw_front"]


def custom_frame(xw: str, yw: str) -> Frame:
    """A user-declared frame from two direction words. Z is derived, so the
    result is guaranteed right-handed and orthogonal."""
    fr = _f("custom", f"Custom (x {xw}, y {yw}, z derived)", xw, yw,
            "team-declared",
            "Origin: whichever datum you pick alongside it.",
            "Your team's own convention, declared once instead of assumed "
            "differently by every subteam.")
    return fr


# --------------------------------------------------------------------------- #
#  FLOATING DATUMS
#  Resolved live from the vehicle parameters so 'distance from CG' stays
#  correct as the design converges, instead of rotting the moment the master
#  assembly changes.  a = L·(1 − weight_dist_front) is the CG setback from
#  the front axle from static axle-load balance.
# --------------------------------------------------------------------------- #

DATUM_LABELS = {
    "front_axle":    "Front axle centre (on ground)",
    "rear_axle":     "Rear axle centre (on ground)",
    "wheelbase_mid": "Mid-wheelbase (on ground)",
    "cg":            "Centre of gravity (live from Integration)",
}
DATUM_ORDER = ["front_axle", "wheelbase_mid", "cg", "rear_axle"]


def resolve_datums(wheelbase_mm: float, weight_dist_front: float,
                   cg_height_mm: float) -> dict:
    """World positions (mm) of every named datum for the CURRENT car."""
    L = float(wheelbase_mm)
    a = L * (1.0 - float(weight_dist_front))   # CG behind front axle
    return {
        "front_axle":    (0.0, 0.0, 0.0),
        "rear_axle":     (-L, 0.0, 0.0),
        "wheelbase_mid": (-L / 2.0, 0.0, 0.0),
        "cg":            (-a, 0.0, float(cg_height_mm)),
    }


def datum_drift(saved: dict, live: dict) -> list:
    """How far each datum moved since the charter snapshot — the answer to
    'we can't base designs on CG because it keeps moving'. You can: the tool
    tells you when it moved and by how much."""
    out = []
    for k in DATUM_ORDER:
        if k in saved and k in live:
            d = _sub(live[k], saved[k])
            mag = _norm(d)
            if mag > 0.05:
                out.append((k, d, mag))
    return out


# --------------------------------------------------------------------------- #
#  CONVERSION + PLAIN-ENGLISH READOUT
# --------------------------------------------------------------------------- #

def convert_point(p, src: Frame, dst: Frame, src_origin_w, dst_origin_w):
    return dst.from_world_point(src.to_world_point(p, src_origin_w), dst_origin_w)


def convert_vector(v, src: Frame, dst: Frame):
    """Free vectors: forces, moments, angular rates — no origin shift."""
    return dst.from_world_vec(src.to_world_vec(v))


def describe_world_point(w, datum_name="front_axle", unit="mm"):
    """The ambiguity killer. '(-312, 187, 244)' becomes words nobody can
    misread across subteams: this is what should be typed into Discord."""
    ref = DATUM_LABELS.get(datum_name, datum_name).split(" (")[0].lower()
    parts = []
    parts.append(f"{abs(w[0]):.1f} {unit} {'forward of' if w[0] >= 0 else 'behind'} the {ref}"
                 if datum_name != "front_axle" or True else "")
    parts[0] = (f"{abs(w[0]):.1f} {unit} "
                f"{'forward of' if w[0] > 0.05 else ('behind' if w[0] < -0.05 else 'at')} "
                f"the {ref}")
    if abs(w[1]) > 0.05:
        parts.append(f"{abs(w[1]):.1f} {unit} {'left' if w[1] > 0 else 'right'} of centreline")
    else:
        parts.append("on centreline")
    if abs(w[2]) > 0.05:
        parts.append(f"{abs(w[2]):.1f} {unit} {'above' if w[2] > 0 else 'below'} ground")
    else:
        parts.append("at ground level")
    return ", ".join(parts)


# --------------------------------------------------------------------------- #
#  SIGN-CONVENTION LINTER
#  Per-defect findings with a fix, matching the Frame Planner audit style.
# --------------------------------------------------------------------------- #

_MIRROR_TOKENS = [("left", "right"), ("_l", "_r"), ("-l", "-r"),
                  (" l ", " r "), ("lh", "rh"), ("port", "stbd")]

def _mirror_name(name: str):
    low = name.lower()
    for a, b in _MIRROR_TOKENS:
        if a in low:
            return low.replace(a, b)
        if b in low:
            return low.replace(b, a)
    return None


def lint_points(points: dict, frame: Frame, origin_w,
                wheelbase_mm: float = 1550.0) -> list:
    """points: {name: (x,y,z) in `frame`, mm}. Returns findings:
    (level, name, message, fix)."""
    findings = []
    world = {n: frame.to_world_point(p, origin_w) for n, p in points.items()}

    # 1) unit sniff — the classic silent m/mm or inch/mm mixup
    mags = [max(abs(c) for c in p) for p in points.values() if any(points.values())]
    if mags:
        biggest = max(mags)
        if biggest < 6.0:
            findings.append(("warn", "—",
                             f"Largest coordinate is {biggest:.3g} — these look like METRES, "
                             "not mm.",
                             "Multiply by 1000 or set the import unit to metres."))
        elif biggest > 12000.0:
            findings.append(("warn", "—",
                             f"Largest coordinate is {biggest:.0f} mm (> 12 m).",
                             "Check for a unit mixup or a point referenced to the wrong datum."))

    for n, w in world.items():
        # 2) below ground
        if w[2] < -1.0:
            findings.append(("fail", n,
                             f"Resolves to {abs(w[2]):.1f} mm BELOW ground in the declared frame.",
                             "Most likely a Z sign flip: the source data was authored in a "
                             "Z-down frame (SAE J670) but imported as Z-up. Re-import with "
                             "the source frame set to SAE J670."))
        # 3) envelope vs wheelbase
        if w[0] > 0.8 * wheelbase_mm or w[0] < -2.0 * wheelbase_mm:
            findings.append(("warn", n,
                             f"Sits {w[0]:.0f} mm longitudinally from the front axle — outside "
                             f"a plausible car envelope for a {wheelbase_mm:.0f} mm wheelbase.",
                             "Check the datum: this smells like a point measured from the rear "
                             "axle or CG but imported as front-axle-relative."))

    # 4) mirror-pair symmetry
    lowmap = {n.lower(): n for n in points}
    seen = set()
    for n in points:
        m = _mirror_name(n)
        if not m or m == n.lower() or n.lower() in seen:
            continue
        if m in lowmap:
            seen.add(m)
            wa, wb = world[n], world[lowmap[m]]
            dy = wa[1] + wb[1]           # should cancel if mirrored about centreline
            dx = wa[0] - wb[0]; dz = wa[2] - wb[2]
            if abs(dy) > 1.0 or abs(dx) > 1.0 or abs(dz) > 1.0:
                findings.append(("warn", f"{n} ↔ {lowmap[m]}",
                                 f"Mirror pair is asymmetric: Δx {dx:+.1f}, Σy {dy:+.1f}, "
                                 f"Δz {dz:+.1f} mm.",
                                 "If the car is meant to be symmetric, one side was probably "
                                 "measured in a different frame or the Y sign flipped on import "
                                 "(ISO Y-left vs SAE Y-right)."))
    if not findings:
        findings.append(("pass", "—", "No sign-convention defects detected.", ""))
    return findings


# --------------------------------------------------------------------------- #
#  CSV / TEXT I-O for the migration wizard
# --------------------------------------------------------------------------- #

def parse_points_text(text: str) -> dict:
    """Accepts 'name,x,y,z' CSV (header optional, ; or tab ok) or bare
    'x,y,z' lines (auto-named P1..). Returns {name: (x,y,z)}."""
    out, auto = {}, 0
    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except Exception:
        dialect = csv.excel
    for row in csv.reader(io.StringIO(text), dialect):
        row = [c.strip() for c in row if c.strip() != ""]
        if not row:
            continue
        try:
            if len(row) >= 4:
                out[row[0]] = (float(row[1]), float(row[2]), float(row[3]))
            elif len(row) == 3:
                auto += 1
                out[f"P{auto}"] = (float(row[0]), float(row[1]), float(row[2]))
        except ValueError:
            continue   # header or junk line
    return out


def points_to_csv(points: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "x", "y", "z"])
    for n, p in points.items():
        w.writerow([n, f"{p[0]:.3f}", f"{p[1]:.3f}", f"{p[2]:.3f}"])
    return buf.getvalue()


def points_to_solidworks_xyz(points: dict) -> str:
    """Tab-separated x y z file for SolidWorks 'Curve Through XYZ Points' —
    the fastest path to re-anchoring a model after a convention migration
    without redrawing anything."""
    return "\n".join(f"{p[0]:.3f}\t{p[1]:.3f}\t{p[2]:.3f}" for p in points.values()) + "\n"


# --------------------------------------------------------------------------- #
#  CHARTER — the one-page, judge-ready convention statement
# --------------------------------------------------------------------------- #

def charter_markdown(frame: Frame, datum_key: str, datums_mm: dict,
                     team_name: str = "", saved_by: str = "") -> str:
    ax = frame.axis_words()
    senses = frame.rotation_senses()
    today = _dt.date.today().isoformat()
    d = datums_mm.get(datum_key, (0, 0, 0))
    lines = [
        f"# Vehicle Coordinate Convention — {team_name or 'Team'} charter",
        "",
        f"*Declared {today}{(' by ' + saved_by) if saved_by else ''} in KinematiK. "
        "One frame, one origin, every subteam.*",
        "",
        f"## Frame: {frame.name}" + (f"  ·  {frame.standard}" if frame.standard else ""),
        "",
        f"| Axis | Points | Positive rotation about it |",
        f"|---|---|---|",
    ]
    for (lab, role, sense), word in zip(senses, ax):
        lines.append(f"| **+{lab}** | vehicle **{word}** | +{role}: {sense} |")
    lines += [
        "",
        f"## Master origin: {DATUM_LABELS.get(datum_key, datum_key)}",
        "",
        f"World position at time of declaration: x {d[0]:+.1f} mm (ISO fwd), "
        f"y {d[1]:+.1f} mm (ISO left), z {d[2]:+.1f} mm above ground. "
        "KinematiK re-resolves this datum live from the Integration numbers "
        "and flags every drift, so CG-relative dimensions cannot silently rot.",
        "",
        "## How to say it (phrasebook)",
        "",
        "When talking across subteams or to design judges, prefer WORDS over "
        "bare axis letters — axis letters only mean something with this "
        "charter attached:",
        "",
        f"* say **\"vehicle {ax[0]}\"** instead of \"+x\"",
        f"* say **\"vehicle {ax[1]}\"** instead of \"+y\"",
        f"* say **\"vehicle {ax[2]}\"** instead of \"+z\"",
        "* always attach the datum: \"…measured from the "
        f"{DATUM_LABELS.get(datum_key, datum_key).split(' (')[0].lower()}\"",
        "",
        "## One-line answer for design judging",
        "",
        f"> \"All vehicle coordinates follow **{frame.name}**"
        + (f" ({frame.standard})" if frame.standard else "")
        + f", origin at the {DATUM_LABELS.get(datum_key, datum_key).split(' (')[0].lower()}, "
        "declared team-wide in our engineering platform; every exchanged "
        "dimension carries this frame tag.\"",
        "",
        "*Generated by KinematiK · Frames & Datums.*",
    ]
    return "\n".join(lines)


# =========================================================================== #
#  STREAMLIT TAB BODY
# =========================================================================== #

def render(get_store=None, points_provider=None):
    """The Frames & Datums tab.

    get_store        — optional callable returning the ProjectStore (for
                       logging the charter as a Decision).
    points_provider  — optional callable returning ({name: (x,y,z)}, label)
                       for the live hardpoint set in the KinematiK frame.
    """
    import streamlit as st

    vp = st.session_state.get("vp", {}) or {}
    wb  = float(vp.get("wheelbase", 1550.0) or 1550.0)
    wdf = float(vp.get("weight_dist_front", 0.5) or 0.5)
    cgh = float(vp.get("cg_height", 300.0) or 300.0)
    datums = resolve_datums(wb, wdf, cgh)

    st.markdown("### 🧭 Frames & Datums")
    st.caption(
        "One declared convention for the whole team — so *\"wait, what are we "
        "defining as x and y\"* never happens again, switching standards is a "
        "batch job instead of a full redo, and a moving CG can't silently rot "
        "your measurements.")

    charter = st.session_state.get("kk_frame_charter")

    # ------------------------------------------------------------------ #
    #  1 · TEAM CONVENTION CHARTER
    # ------------------------------------------------------------------ #
    st.markdown("#### 1 · Team convention charter")
    if charter:
        fr = _charter_frame(charter)
        st.success(
            f"**Declared:** {fr.name} · origin at "
            f"{DATUM_LABELS[charter['datum']]}"
            f" · saved {charter.get('saved_at', '')}"
            + (f" by {charter['saved_by']}" if charter.get("saved_by") else ""))
    else:
        st.info("No convention declared yet. Until one is saved here, every "
                "subteam is guessing — pick one below (30 seconds).")

    c1, c2 = st.columns([2, 1])
    frame_key = c1.selectbox(
        "Convention",
        FRAME_ORDER + ["custom"],
        format_func=lambda k: BUILTIN_FRAMES[k].name if k in BUILTIN_FRAMES
                    else "Custom (build from direction words)",
        index=(FRAME_ORDER + ["custom"]).index(charter["frame_key"]) if charter else 0,
        key="kk_fr_pick")
    datum_key = c2.selectbox(
        "Master origin (datum)", DATUM_ORDER,
        format_func=lambda k: DATUM_LABELS[k],
        index=DATUM_ORDER.index(charter["datum"]) if charter else 0,
        key="kk_fr_datum")

    if frame_key == "custom":
        cc1, cc2, cc3 = st.columns(3)
        xw = cc1.selectbox("+x points…", DIRECTION_WORDS, key="kk_fr_xw")
        yopts = [w for w in DIRECTION_WORDS
                 if abs(_dot(_WORLD_WORDS[w], _WORLD_WORDS[xw])) < 1e-9]
        yw = cc2.selectbox("+y points…", yopts, key="kk_fr_yw")
        frame = custom_frame(xw, yw)
        cc3.metric("+z (derived, right-handed)", frame.axis_words()[2])
    else:
        frame = BUILTIN_FRAMES[frame_key]

    st.caption(frame.blurb)

    ax = frame.axis_words()
    sc1, sc2 = st.columns([1, 1])
    with sc1:
        st.markdown("**Axis triad**")
        st.markdown(f"`+x` → vehicle **{ax[0]}** · `+y` → vehicle **{ax[1]}** "
                    f"· `+z` → vehicle **{ax[2]}**")
    with sc2:
        st.markdown("**Rotation senses (computed, not memorised)**")
        for lab, role, sense in frame.rotation_senses():
            st.markdown(f"`+{role}` (about {lab}): {sense}")

    name_c1, name_c2 = st.columns(2)
    team_name = name_c1.text_input("Team name (for the charter export)",
                                   value=(charter or {}).get("team_name", ""),
                                   key="kk_fr_team")
    saved_by = name_c2.text_input("Declared by",
                                  value=(charter or {}).get("saved_by", ""),
                                  key="kk_fr_by")

    if st.button("📜 Declare this as the team convention", type="primary",
                 key="kk_fr_save"):
        st.session_state["kk_frame_charter"] = {
            "frame_key": frame_key,
            "custom_x": st.session_state.get("kk_fr_xw"),
            "custom_y": st.session_state.get("kk_fr_yw"),
            "datum": datum_key,
            "team_name": team_name, "saved_by": saved_by,
            "saved_at": _dt.date.today().isoformat(),
            "saved_datums": {k: list(v) for k, v in datums.items()},
            "saved_wb": wb, "saved_wdf": wdf, "saved_cgh": cgh,
        }
        # Log it where decisions live, so it survives the season and the
        # next cohort inherits WHY, not just WHAT.
        if get_store is not None:
            try:
                import project as project_mod
                store = get_store()
                store.add_decision(project_mod.Decision(
                    team="integration",
                    title=f"Vehicle coordinate convention: {frame.name}",
                    rationale=("Declared in Frames & Datums. Origin: "
                               f"{DATUM_LABELS[datum_key]}. Axis triad: +x "
                               f"{ax[0]}, +y {ax[1]}, +z {ax[2]}. All exchanged "
                               "dimensions carry this frame tag."),
                    author=saved_by, tags="coordinates,standard",
                    part="whole car"))
                store.save()
            except Exception:
                pass  # the charter itself must never fail on logging
        st.rerun()

    if charter:
        fr = _charter_frame(charter)
        md = charter_markdown(fr, charter["datum"], datums,
                              charter.get("team_name", ""),
                              charter.get("saved_by", ""))
        st.download_button("⬇️ Judge-ready charter (one-page .md)", md,
                           file_name="coordinate_convention_charter.md",
                           key="kk_fr_dl")

    st.divider()

    # ------------------------------------------------------------------ #
    #  2 · DATUM WATCH — the moving-CG problem, solved by watching it
    # ------------------------------------------------------------------ #
    st.markdown("#### 2 · Datum watch (live from vehicle parameters)")
    st.caption("Datums resolve from the CURRENT wheelbase / weight split / CG "
               "height, so 'distance from CG' is always the current CG. If the "
               "design moves, you're told — nothing rots silently.")
    dcols = st.columns(len(DATUM_ORDER))
    for col, k in zip(dcols, DATUM_ORDER):
        w = datums[k]
        col.metric(DATUM_LABELS[k].split(" (")[0],
                   f"x {w[0]:+.0f}", f"z {w[2]:+.0f} mm")
    if charter and charter.get("saved_datums"):
        drift = datum_drift({k: tuple(v) for k, v in charter["saved_datums"].items()},
                            datums)
        if drift:
            for k, d, mag in drift:
                dirw = []
                if abs(d[0]) > 0.05: dirw.append(f"{abs(d[0]):.1f} mm {'forward' if d[0] > 0 else 'rearward'}")
                if abs(d[2]) > 0.05: dirw.append(f"{abs(d[2]):.1f} mm {'up' if d[2] > 0 else 'down'}")
                st.warning(f"**{DATUM_LABELS[k].split(' (')[0]} has moved "
                           f"{mag:.1f} mm since the charter was saved** "
                           f"({', '.join(dirw) or 'lateral'}). Any dimension "
                           "quoted relative to it before this drift should be "
                           "re-checked — run your point set through the "
                           "migration wizard with the OLD datum as source and "
                           "the NEW as target to re-anchor in one click.")
        else:
            st.success("No datum drift since the charter was saved.")

    st.divider()

    # ------------------------------------------------------------------ #
    #  3 · ROSETTA — one number, every frame, plus plain English
    # ------------------------------------------------------------------ #
    st.markdown("#### 3 · Rosetta (settle the Discord argument)")
    st.caption("Type a point once; read it in every convention at the same "
               "time, plus in words nobody can misread. Paste the WORDS into "
               "chat, not the bare numbers.")
    rc = st.columns(5)
    kind = rc[0].selectbox("Quantity", ["point (has an origin)",
                                        "free vector (force / moment / rate)"],
                           key="kk_ro_kind")
    src_key = rc[1].selectbox("Given in", FRAME_ORDER,
                              format_func=lambda k: BUILTIN_FRAMES[k].name,
                              key="kk_ro_src")
    px = rc[2].number_input("x", value=250.0, key="kk_ro_x")
    py = rc[3].number_input("y", value=-180.0, key="kk_ro_y")
    pz = rc[4].number_input("z", value=130.0, key="kk_ro_z")
    src_dat = st.selectbox("…measured from", DATUM_ORDER,
                           format_func=lambda k: DATUM_LABELS[k],
                           key="kk_ro_dat",
                           disabled=kind.startswith("free"))
    src = BUILTIN_FRAMES[src_key]
    rows = []
    if kind.startswith("point"):
        w = src.to_world_point((px, py, pz), datums[src_dat])
        for k in FRAME_ORDER:
            f2 = BUILTIN_FRAMES[k]
            for dk in DATUM_ORDER:
                if dk != src_dat and dk != "front_axle":
                    continue
                q = f2.from_world_point(w, datums[dk])
                rows.append({"frame": f2.name,
                             "from": DATUM_LABELS[dk].split(" (")[0],
                             "x": round(q[0], 2), "y": round(q[1], 2),
                             "z": round(q[2], 2)})
        st.dataframe(rows, width='stretch', hide_index=True)
        st.markdown(f"**In words:** {describe_world_point(w, 'front_axle')}.")
    else:
        w = src.to_world_vec((px, py, pz))
        for k in FRAME_ORDER:
            q = BUILTIN_FRAMES[k].from_world_vec(w)
            rows.append({"frame": BUILTIN_FRAMES[k].name,
                         "x": round(q[0], 3), "y": round(q[1], 3),
                         "z": round(q[2], 3)})
        st.dataframe(rows, width='stretch', hide_index=True)
        st.caption("Forces / moments / angular rates shift components only — "
                   "no datum involved. Note how a +Z tyre load in SAE J670 "
                   "becomes −Z in ISO 8855: the classic sign bug.")

    st.divider()

    # ------------------------------------------------------------------ #
    #  4 · MIGRATION WIZARD — the 'full redo' killer
    # ------------------------------------------------------------------ #
    st.markdown("#### 4 · Migration wizard (the 'full redo' killer)")
    st.caption("Convert the live hardpoints or any CSV between conventions "
               "and datums in one pass, audit every point, and take a "
               "SolidWorks Curve-Through-XYZ file back into CAD. Switching "
               "standards stops being days of retyping.")

    src_choice = st.radio("Source points", ["Live KinematiK hardpoints",
                                            "Paste / upload CSV"],
                          horizontal=True, key="kk_mig_src")
    pts, pts_frame_key, pts_datum = {}, "kinematik", "front_axle"
    if src_choice.startswith("Live"):
        if points_provider is not None:
            try:
                pts, lbl = points_provider()
                st.caption(f"Loaded **{len(pts)}** points from {lbl} "
                           "(KinematiK internal frame, front-axle datum).")
            except Exception as e:
                st.warning(f"Couldn't read live hardpoints: {e}")
        else:
            st.warning("Live hardpoints not wired in this context — paste a CSV.")
    else:
        up = st.file_uploader("CSV — `name,x,y,z` (header optional; bare "
                              "`x,y,z` lines auto-name)", type=["csv", "txt"],
                              key="kk_mig_up")
        pasted = st.text_area("…or paste rows", height=110, key="kk_mig_paste",
                              placeholder="upper_outer, -12.5, -585.0, 310.2")
        text = ""
        if up is not None:
            text = up.getvalue().decode("utf-8", errors="replace")
        elif pasted.strip():
            text = pasted
        if text:
            pts = parse_points_text(text)
            st.caption(f"Parsed **{len(pts)}** points.")
        mc1, mc2 = st.columns(2)
        pts_frame_key = mc1.selectbox("These are in", FRAME_ORDER,
                                      format_func=lambda k: BUILTIN_FRAMES[k].name,
                                      key="kk_mig_srcfr")
        pts_datum = mc2.selectbox("…measured from", DATUM_ORDER,
                                  format_func=lambda k: DATUM_LABELS[k],
                                  key="kk_mig_srcdat")

    tc1, tc2 = st.columns(2)
    default_target = (charter or {}).get("frame_key", "iso8855")
    if default_target not in FRAME_ORDER:
        default_target = "iso8855"
    dst_key = tc1.selectbox("Convert to", FRAME_ORDER,
                            index=FRAME_ORDER.index(default_target),
                            format_func=lambda k: BUILTIN_FRAMES[k].name,
                            key="kk_mig_dst")
    dst_datum = tc2.selectbox("New datum", DATUM_ORDER,
                              index=DATUM_ORDER.index((charter or {}).get("datum", "front_axle")),
                              format_func=lambda k: DATUM_LABELS[k],
                              key="kk_mig_dstdat")

    if pts:
        srcf, dstf = BUILTIN_FRAMES[pts_frame_key], BUILTIN_FRAMES[dst_key]
        conv = {n: convert_point(p, srcf, dstf,
                                 datums[pts_datum], datums[dst_datum])
                for n, p in pts.items()}
        audit = [{"point": n,
                  "src x": round(p[0], 2), "src y": round(p[1], 2), "src z": round(p[2], 2),
                  "new x": round(q[0], 2), "new y": round(q[1], 2), "new z": round(q[2], 2),
                  "in words": describe_world_point(
                      srcf.to_world_point(p, datums[pts_datum]))}
                 for (n, p), q in zip(pts.items(), conv.values())]
        st.dataframe(audit, width='stretch', hide_index=True)
        d1, d2 = st.columns(2)
        d1.download_button("⬇️ Converted CSV", points_to_csv(conv),
                           file_name=f"points_{dst_key}_{dst_datum}.csv",
                           key="kk_mig_dl1")
        d2.download_button("⬇️ SolidWorks Curve-Through-XYZ (.txt)",
                           points_to_solidworks_xyz(conv),
                           file_name=f"sw_xyz_{dst_key}.txt", key="kk_mig_dl2")
        st.caption("SolidWorks: *Insert → Curve → Curve Through XYZ Points → "
                   "Browse* to this file — every migrated point lands as a "
                   "sketchable reference, no retyping.")

        # ------------------------------------------------------------- #
        #  5 · LINTER on whatever set is loaded
        # ------------------------------------------------------------- #
        st.markdown("#### 5 · Sign-convention lint")
        findings = lint_points(pts, srcf, datums[pts_datum], wb)
        for level, name, msg, fix in findings:
            icon = {"pass": "✅", "warn": "🟡", "fail": "🔴"}[level]
            body = f"{icon} **{name}** — {msg}" if name != "—" else f"{icon} {msg}"
            if fix:
                body += f"\n\n> **Fix:** {fix}"
            (st.error if level == "fail" else
             st.warning if level == "warn" else st.success)(body)
    else:
        st.caption("Load or paste points above to convert and lint them.")


def charter_tag_line(charter: dict | None, long: bool = False) -> str:
    """One-line frame declaration for stamping onto exports (DXF annotation
    blocks, handover reports, ledger banners). This is the tag that makes a
    number leaving the platform unambiguous — the whole point of the charter.

    Returns '' when no charter exists and long=False, so callers can skip the
    line cleanly; with long=True an explicit UNDECLARED nudge is returned so
    formal documents never silently omit the convention.
    """
    if not charter:
        return ("Coordinate convention: UNDECLARED — declare one in "
                "🧭 Frames & Datums before exchanging dimensions."
                if long else "")
    fr = _charter_frame(charter)
    ax = fr.axis_words()
    datum = DATUM_LABELS.get(charter.get("datum", "front_axle"),
                             charter.get("datum", "front_axle")).split(" (")[0]
    core = (f"Frame: {fr.name}"
            + (f" ({fr.standard})" if fr.standard else "")
            + f" · +x {ax[0]}, +y {ax[1]}, +z {ax[2]} · origin: {datum}")
    if long:
        who = charter.get("saved_by") or "team"
        when = charter.get("saved_at", "")
        return f"{core} · declared by {who} {when}".rstrip()
    return core


def _charter_frame(charter: dict) -> Frame:
    k = charter.get("frame_key", "iso8855")
    if k == "custom":
        try:
            return custom_frame(charter.get("custom_x") or "forward",
                                charter.get("custom_y") or "left")
        except Exception:
            return BUILTIN_FRAMES["iso8855"]
    return BUILTIN_FRAMES.get(k, BUILTIN_FRAMES["iso8855"])


# --------------------------------------------------------------------------- #
#  SELF-TEST (python coordinate_frames.py) — exact identities, no fuzz
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    iso = BUILTIN_FRAMES["iso8855"]; sae = BUILTIN_FRAMES["sae_j670"]
    kin = BUILTIN_FRAMES["kinematik"]; d = resolve_datums(1550, 0.5, 300)

    # point 100 fwd, 50 left, 200 up (ISO) == 100 fwd, -50, -200 (SAE)
    assert convert_point((100, 50, 200), iso, sae,
                         d["front_axle"], d["front_axle"]) == (100.0, -50.0, -200.0)
    # KinematiK x-rear: same point is (-100, -50, 200)
    assert convert_point((100, 50, 200), iso, kin,
                         d["front_axle"], d["front_axle"]) == (-100.0, -50.0, 200.0)
    # datum shift: CG sits 775 mm behind front axle, 300 up (50/50 split)
    p = convert_point((0, 0, 0), iso, iso, d["cg"], d["front_axle"])
    assert abs(p[0] + 775) < 1e-9 and abs(p[2] - 300) < 1e-9
    # free vector: +Z tyre load in SAE == -Z in ISO
    assert convert_vector((0, 0, 1500), sae, iso) == (0.0, 0.0, -1500.0)
    # round trip through every frame
    for k in FRAME_ORDER:
        f2 = BUILTIN_FRAMES[k]
        q = convert_point((123.4, -56.7, 89.0), iso, f2, d["front_axle"], d["cg"])
        r = convert_point(q, f2, iso, d["cg"], d["front_axle"])
        assert all(abs(a - b) < 1e-9 for a, b in zip(r, (123.4, -56.7, 89.0)))
    # rotation senses: SAE +yaw = nose right, ISO +yaw = nose left
    sae_yaw = [s for lab, role, s in sae.rotation_senses() if role == "yaw"][0]
    iso_yaw = [s for lab, role, s in iso.rotation_senses() if role == "yaw"][0]
    assert "right" in sae_yaw and "left" in iso_yaw
    # right-handedness guaranteed for every custom combination
    for xw in DIRECTION_WORDS:
        for yw in DIRECTION_WORDS:
            if abs(_dot(_WORLD_WORDS[xw], _WORLD_WORDS[yw])) < 1e-9:
                f2 = custom_frame(xw, yw)
                bx, by, bz = f2.basis
                assert _cross(bx, by) == bz
    # linter catches a Z-down import
    fnd = lint_points({"lower_outer": (300, -550, -120)}, iso, d["front_axle"])
    assert any(l == "fail" for l, *_ in fnd)
    # mirror asymmetry
    fnd = lint_points({"ub_left": (300, 550, 120), "ub_right": (300, -505, 120)},
                      iso, d["front_axle"])
    assert any("Mirror" in m or "mirror" in m.lower() for _, _, m, _ in fnd)
    print("coordinate_frames: all self-tests pass")
