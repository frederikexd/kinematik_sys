# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Dynamic full-vehicle 3D model (pure Python + Plotly).
#
#  A live Formula-style car assembled from the data every sub-team has already
#  entered. Edit a hardpoint, a spring rate, a wing's downforce, the battery
#  mass — and the body that subsystem owns visibly changes here, instantly,
#  because the figure is rebuilt from the same session state those tabs write.
# ============================================================================

"""
WHAT THIS DRAWS

A single Plotly 3D figure of an open-wheel Formula car, built from:

  * suspension geometry   (Hardpoints)          -> the four corners + tires
  * vehicle parameters    (VehicleParams)        -> wheelbase, track, CG, mass,
                                                    ride height (from spring rate)
  * the integration ledger (IntegrationLedger)   -> every other subsystem:
        aerodynamics -> front & rear wings sized by declared downforce
        powertrain   -> EV traction motor + inverter sized by power
        cooling      -> sidepod radiator ducts sized by required airflow
        electrics    -> accumulator / battery box sized by its envelope+mass
        brakes       -> brake discs at each corner sized by brake torque
        chassis      -> the FSAE survival cell (pointed nose, tub, main & front
                        roll hoops, driver helmet)
        data-acq     -> a small logger pod (no meaningful envelope, shown small)

Every body is a real triangulated mesh (Mesh3d) or line set, positioned in the
kinematics frame (mm, SAE axes: x rear, y right, z up). Because the geometry is
recomputed from state on every Streamlit rerun, the "dynamic" requirement is met
structurally: there is no cached car. Each subsystem sees its own change and the
knock-on to the whole car (CG marker, mass readout) the moment it edits.

HOW A SUBSYSTEM'S NUMBERS BECOME GEOMETRY

Where a subsystem declares an explicit envelope box (env_x/y/z), we draw that box —
it is the literal thing they reserved. Where they declare a performance number but
no box (e.g. aero downforce, powertrain power), we map that number through a
documented, monotonic sizing law to a sensible body so the change is *visible* and
*directional* (more downforce -> bigger wing) without pretending to be CFD. Bodies
sized this way are labelled "(sized from <channel>)" so nobody mistakes the drawing
for analysis.
"""

from __future__ import annotations

import numpy as np
import plotly.graph_objects as go

from .kinematics import Hardpoints, SuspensionKinematics
from .interfaces import SUBSYSTEMS

# --------------------------------------------------------------------------- #
#  Palette — matched to the app's dark instrument styling.
# --------------------------------------------------------------------------- #
COLORS = dict(
    upper="#37e0d0", lower="#ffb02e", upright="#ffffff", tie="#ff5a52",
    push="#9b8cff", rocker="#5ad17a", spring="#ff9f43",
    wheel="#6f7d8c", tire="#15181c", tire_edge="#3a434c", rim="#23282e",
    # Matte-black tub with a hint of the photo's red/yellow livery accent.
    monocoque="#1d2024", frame_tube="#9aa3ad", nose="#23262b", livery="#d63a2f",
    frame="#0d1013", hoop="#0d1013", helmet="#e9edf1", helmet_band="#d63a2f",
    halo="#10141a",
    wing="#202428", wing_edge="#3a414a", endplate="#16191d",
    sidepod="#23262b", radiator="#ff6b5a", inlet="#0e1216",
    engine="#3a3a3f", motor="#454a52", airbox="#33373d",
    battery="#1f2a20", batt_edge="#5ad17a",
    brake="#c2410c", logger="#33373d",
    point="#e7ecf1", floor="#0c1014", cg="#ffd166",
    custom="#37e0d0", cad="#1f8bff",
)


# --------------------------------------------------------------------------- #
#  Mesh primitives
# --------------------------------------------------------------------------- #
def _units_axis_cfg(title_txt, lo_mm, hi_mm):
    """Scene-axis dict whose tick labels follow the active unit system.

    Doctrine matches units.py: the geometry stays in mm forever — only the
    PRESENTATION converts. Tick positions are chosen at round numbers in the
    display unit (mm or in) and placed at their mm-equivalent coordinates, so
    toggling Imperial relabels the axes without touching a single vertex.
    Falls back to plain mm labels if units.py is unavailable or the extent
    is degenerate (empty scene).
    """
    base = dict(backgroundcolor="#0e1216", gridcolor="#1d242c",
                color="#8d99a6")
    factor, lbl = 1.0, "mm"
    try:
        from . import units as _units
        lbl = _units.label("mm")                    # 'mm' or 'in'
        if lbl == "in":
            factor = 25.4                           # mm per display unit
    except Exception:
        pass
    base["title"] = f"{title_txt} [{lbl}]"
    try:
        lo, hi = float(lo_mm), float(hi_mm)
        if np.isfinite(lo) and np.isfinite(hi) and hi > lo:
            span = (hi - lo) / factor               # extent in display units
            # nice step targeting ~6 ticks: 1/2/5 × 10^k in the display unit
            raw = span / 6.0
            mag = 10.0 ** np.floor(np.log10(max(raw, 1e-9)))
            step = min((s for s in (1 * mag, 2 * mag, 5 * mag, 10 * mag)
                        if s >= raw), default=mag)
            t0 = np.floor(lo / factor / step) * step
            vals = np.arange(t0, hi / factor + step, step)
            base["tickvals"] = [float(v) * factor for v in vals]  # mm coords
            base["ticktext"] = [f"{v:g}" for v in vals]           # unit labels
    except Exception:
        pass                                        # title-only fallback
    return base


def _bbox_wire(lo, hi):
    """12 edges of the axis-aligned box [lo,hi] as polyline x/y/z lists (with
    None breaks between segments) for a single Scatter3d trace."""
    x0, y0, z0 = float(lo[0]), float(lo[1]), float(lo[2])
    x1, y1, z1 = float(hi[0]), float(hi[1]), float(hi[2])
    c = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    edges = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6),
             (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)]
    xs, ys, zs = [], [], []
    for a, b in edges:
        xs += [c[a][0], c[b][0], None]
        ys += [c[a][1], c[b][1], None]
        zs += [c[a][2], c[b][2], None]
    return xs, ys, zs


def _box(cx, cy, cz, lx, ly, lz):
    """Axis-aligned box centred at (cx,cy,cz) with full extents (lx,ly,lz)."""
    hx, hy, hz = lx / 2.0, ly / 2.0, lz / 2.0
    v = np.array([
        [cx - hx, cy - hy, cz - hz], [cx + hx, cy - hy, cz - hz],
        [cx + hx, cy + hy, cz - hz], [cx - hx, cy + hy, cz - hz],
        [cx - hx, cy - hy, cz + hz], [cx + hx, cy - hy, cz + hz],
        [cx + hx, cy + hy, cz + hz], [cx - hx, cy + hy, cz + hz],
    ], float)
    faces = [
        (0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6),
        (0, 5, 1), (0, 4, 5), (3, 2, 6), (3, 6, 7),
        (1, 5, 6), (1, 6, 2), (0, 3, 7), (0, 7, 4),
    ]
    i = [f[0] for f in faces]; j = [f[1] for f in faces]; k = [f[2] for f in faces]
    return v, np.array(i), np.array(j), np.array(k)


def _prism_xsection(profile_xy, x_positions, scales):
    """Loft a 2D cross-section (y,z) along x. scales: (sy,sz,oy,oz) per station."""
    prof = np.asarray(profile_xy, float)
    n = len(prof)
    rings = []
    for x, (sy, sz, oy, oz) in zip(x_positions, scales):
        rings.append(np.column_stack([
            np.full(n, x), prof[:, 0] * sy + oy, prof[:, 1] * sz + oz]))
    verts = np.vstack(rings)
    I, J, K = [], [], []
    for s in range(len(rings) - 1):
        b0, b1 = s * n, (s + 1) * n
        for a in range(n):
            b = (a + 1) % n
            v00, v01, v10, v11 = b0 + a, b0 + b, b1 + a, b1 + b
            I += [v00, v00]; J += [v01, v11]; K += [v11, v10]
    return verts, np.array(I), np.array(J), np.array(K)


def _ellipse_ring(n=20):
    th = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return [(np.cos(t), np.sin(t)) for t in th]


def _cylinder(center, axis, radius, length, n=24, cap=True):
    axis = np.asarray(axis, float); axis /= (np.linalg.norm(axis) + 1e-12)
    ref = np.array([0, 0, 1.0]) if abs(axis[2]) < 0.9 else np.array([1.0, 0, 0])
    u = np.cross(axis, ref); u /= (np.linalg.norm(u) + 1e-12)
    v = np.cross(axis, u)
    th = np.linspace(0, 2 * np.pi, n, endpoint=False)
    rim = np.array([radius * (np.cos(t) * u + np.sin(t) * v) for t in th])
    c = np.asarray(center, float)
    a0, a1 = c - axis * (length / 2), c + axis * (length / 2)
    verts = np.vstack([a0 + rim, a1 + rim])
    I, J, K = [], [], []
    for a in range(n):
        b = (a + 1) % n
        I += [a, a]; J += [b, a + n]; K += [a + n, b + n]
    if cap:
        ci0, ci1 = len(verts), len(verts) + 1
        verts = np.vstack([verts, a0, a1])
        for a in range(n):
            b = (a + 1) % n
            I += [ci0]; J += [b]; K += [a]
            I += [ci1]; J += [a + n]; K += [b + n]
    return verts, np.array(I), np.array(J), np.array(K)


def _orient_part_mesh(verts, *, axis_map="z_up", yaw_deg=0.0, scale=1.0,
                      centre=(0.0, 0.0, 0.0), roll_deg=0.0, pitch_deg=0.0):
    """Place an imported CAD part's vertices into the car's SAE frame.

    verts come recentred on the part's own bbox centre (from chassis.load_part_mesh).
    We optionally remap axes (CAD up-axis -> car z-up), apply free rotation about
    the car's three axes, scale, then translate the part's centre to `centre`.
    Returns an (N,3) array.

        axis_map : "z_up"  CAD already z-up (no swap)
                   "y_up"  CAD is y-up (Y->Z, Z->-Y): common SolidWorks export
                   "x_up"  CAD is x-up (X->Z, Z->-X)

    roll_deg   : rotation about the car's x-axis (fore-aft) — tips the part L/R.
    pitch_deg  : rotation about the car's y-axis (lateral) — noses it up/down.
    yaw_deg    : rotation about the car's z-axis (vertical) — spins it flat.
    The three are applied roll -> pitch -> yaw, after the CAD-up-axis remap.
    """
    V = np.asarray(verts, float).reshape(-1, 3) * float(scale)
    # Defensive recentre: placement translates the part's CENTRE to `centre`, so
    # the incoming verts must be centred on their own bbox. load_part_mesh does
    # this, but a payload from elsewhere (older cache, hand-built) might not — in
    # which case the part would land offset by its bbox centre. Recentring here
    # makes "place by centre" hold no matter the source, which is what keeps an
    # imported CAD body aligned with the dummy slot it replaces.
    if len(V):
        _bb = (V.min(axis=0) + V.max(axis=0)) / 2.0
        V = V - _bb
    if axis_map == "y_up":
        V = np.column_stack([V[:, 0], -V[:, 2], V[:, 1]])
    elif axis_map == "x_up":
        V = np.column_stack([-V[:, 2], V[:, 1], V[:, 0]])

    def _rot_x(deg):
        a = np.radians(float(deg)); c, s = np.cos(a), np.sin(a)
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])

    def _rot_y(deg):
        a = np.radians(float(deg)); c, s = np.cos(a), np.sin(a)
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])

    def _rot_z(deg):
        a = np.radians(float(deg)); c, s = np.cos(a), np.sin(a)
        return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])

    if roll_deg:
        V = V @ _rot_x(roll_deg).T
    if pitch_deg:
        V = V @ _rot_y(pitch_deg).T
    if yaw_deg:
        V = V @ _rot_z(yaw_deg).T
    return V + np.asarray(centre, float)


def _airfoil_section(n=14, thickness=0.12):
    """A closed 2D aerofoil-ish ring in (chord, thickness) coords, chord 0..1.

    Cambered teardrop: thicker near the leading edge, tapering to the trailing
    edge, so a lofted wing reads as a real element rather than a flat slab.
    """
    xs = (1 - np.cos(np.linspace(0, np.pi, n))) / 2  # cosine-spaced 0..1
    yt = thickness * (1.4845 * np.sqrt(np.clip(xs, 0, 1)) - 0.63 * xs
                      - 1.758 * xs**2 + 1.4215 * xs**3 - 0.5075 * xs**4)
    camber = 0.06 * (1 - (2 * xs - 1) ** 2)  # gentle single-element camber
    upper = np.column_stack([xs, camber + yt])
    lower = np.column_stack([xs[::-1], (camber - yt)[::-1]])
    return np.vstack([upper, lower])


def _wing_element(cx, cy, cz, chord, span, *, thickness=0.12, aoa_deg=-6.0,
                  n_sec=14):
    """A single aerofoil wing element centred at (cx,cy,cz), spanning in y.

    chord runs in x (fore-aft), span in y, with a small angle of attack rotating
    the section in the x-z plane. Returns mesh arrays ready for Mesh3d.
    """
    sec = _airfoil_section(n_sec, thickness)           # (m,2): chord, thick
    m = len(sec)
    a = np.deg2rad(aoa_deg)
    ca, sa = np.cos(a), np.sin(a)
    # section local -> (x,z): chord along x (centred), thickness along z, rotated
    chord_local = (sec[:, 0] - 0.5) * chord
    thick_local = sec[:, 1] * chord
    sx = chord_local * ca - thick_local * sa
    sz = chord_local * sa + thick_local * ca
    ys = np.array([cy - span / 2, cy + span / 2])
    rings = []
    for y in ys:
        rings.append(np.column_stack([cx + sx, np.full(m, y), cz + sz]))
    verts = np.vstack(rings)
    I, J, K = [], [], []
    for s in range(len(rings) - 1):
        b0, b1 = s * m, (s + 1) * m
        for aa in range(m):
            bb = (aa + 1) % m
            I += [b0 + aa, b0 + aa]; J += [b0 + bb, b1 + bb]; K += [b1 + bb, b1 + aa]
    return verts, np.array(I), np.array(J), np.array(K)


def _tube(p0, p1, radius, n=12):
    """A capped cylinder between two endpoints — for roll hoops and frame tubes."""
    p0 = np.asarray(p0, float); p1 = np.asarray(p1, float)
    axis = p1 - p0
    length = np.linalg.norm(axis) + 1e-12
    center = (p0 + p1) / 2
    return _cylinder(center, axis, radius, length, n=n, cap=True)


def _swept_tube(points, radius, n=10):
    """A tube following a polyline of points — for the curved main roll hoop."""
    pts = [np.asarray(p, float) for p in points]
    V = []
    I = []
    J = []
    K = []
    base = 0
    for a in range(len(pts) - 1):
        v, i, j, k = _tube(pts[a], pts[a + 1], radius, n=n)
        V.append(v)
        I += list(i + base); J += list(j + base); K += list(k + base)
        base += len(v)
    return np.vstack(V), np.array(I), np.array(J), np.array(K)


def _sphere(center, radius, n=16):
    """A UV sphere — used for the driver's helmet."""
    c = np.asarray(center, float)
    u = np.linspace(0, np.pi, n)          # polar
    w = np.linspace(0, 2 * np.pi, n)      # azimuth
    U, W = np.meshgrid(u, w)
    x = c[0] + radius * np.sin(U) * np.cos(W)
    y = c[1] + radius * np.sin(U) * np.sin(W)
    z = c[2] + radius * np.cos(U)
    verts = np.column_stack([x.ravel(), y.ravel(), z.ravel()])
    I, J, K = [], [], []
    cols = n
    for a in range(n - 1):
        for b in range(n - 1):
            v00 = a * cols + b
            v01 = a * cols + (b + 1)
            v10 = (a + 1) * cols + b
            v11 = (a + 1) * cols + (b + 1)
            I += [v00, v00]; J += [v01, v11]; K += [v11, v10]
    return verts, np.array(I), np.array(J), np.array(K)


# --------------------------------------------------------------------------- #
#  Corner geometry transforms
# --------------------------------------------------------------------------- #
def _corner_transform(p, *, mirror_y, lateral_scale, x_shift, y_center_ref,
                      size_scale=1.0, z_ground=0.0):
    if p is None:
        return None
    q = np.array(p, float).copy()
    # Global size scale (from an imported define_car chassis): grow/shrink the
    # corner's own geometry about the ground contact so the wheel radius, upright
    # height and link lengths track the car's overall size. Applied to x and z
    # about (x_shift-relative 0, ground); lateral handled by lateral_scale.
    if size_scale != 1.0:
        q[0] = q[0] * size_scale
        q[2] = z_ground + (q[2] - z_ground) * size_scale
    dy = (q[1] - y_center_ref) * lateral_scale
    q[1] = y_center_ref + (-dy if mirror_y else dy)
    q[0] = q[0] + x_shift
    return q


def _solved_corner_points(hp: Hardpoints, ride_drop_mm: float = 0.0):
    kin = SuspensionKinematics(hp)
    s = kin.static
    pts = dict(
        upper_front_inner=np.array(hp.upper_front_inner, float),
        upper_rear_inner=np.array(hp.upper_rear_inner, float),
        lower_front_inner=np.array(hp.lower_front_inner, float),
        lower_rear_inner=np.array(hp.lower_rear_inner, float),
        tie_rod_inner=np.array(hp.tie_rod_inner, float),
        upper_outer=np.array(s.upper_outer, float),
        lower_outer=np.array(s.lower_outer, float),
        tie_rod_outer=np.array(s.tie_rod_outer, float),
        wheel_center=np.array(s.wheel_center, float),
        contact_patch=np.array(s.contact_patch, float),
    )
    if hp.has_rocker():
        for kk in ("rocker_pivot", "rocker_pushrod", "rocker_spring", "spring_inner"):
            vv = getattr(hp, kk)
            if vv is not None:
                pts[kk] = np.array(vv, float)
        po = s.pushrod_outer if s.pushrod_outer is not None else hp.pushrod_outer
        if po is not None:
            pts["pushrod_outer"] = np.array(po, float)
    if ride_drop_mm:
        for kk in pts:
            pts[kk] = pts[kk] - np.array([0, 0, ride_drop_mm], float)
    return pts, s


# --------------------------------------------------------------------------- #
#  Topology-agnostic corner extractor
#
#  The full car must reflect whatever suspension ARCHITECTURE the team picked,
#  not just double wishbones. A double-wishbone corner is described by named
#  Hardpoints; every other topology (MacPherson, multi-link, trailing/semi-
#  trailing arm, solid axle, twist-beam, truck steer linkage, free-form) is
#  described by a GenericKinematics mechanism that reports its own member set via
#  render_segments(). This helper normalises BOTH into the same list of drawable
#  segments + the wheel centre / contact patch / camber the tire needs, so the
#  rest of the renderer is identical regardless of architecture.
# --------------------------------------------------------------------------- #

# Stable colour assignment for agnostic member labels, so the same link is the
# same colour on all four corners and across reruns.
_AGNOSTIC_PALETTE = [
    "#37e0d0", "#ffb02e", "#9b8cff", "#5ad17a", "#ff9f43",
    "#5cd2ff", "#ff7ab6", "#b6ff5a", "#ffd166", "#7d8893",
]


def _agnostic_color(label, registry):
    """Deterministic colour for a member label (its leading token), assigned on
    first sight and reused, so member 'L2' is always the same hue."""
    base = (label or "link").split()[0]
    if base not in registry:
        registry[base] = _AGNOSTIC_PALETTE[len(registry) % len(_AGNOSTIC_PALETTE)]
    return registry[base], base


# Map a subsystem name to the COLORS key whose hue best represents it, so a
# user-dropped custom part reads as "belonging to" that sub-team at a glance.
_SUBSYS_COLOR_KEY = {
    "aerodynamics": "wing", "brakes": "brake", "chassis": "monocoque",
    "cooling": "radiator", "electrics": "batt_edge", "powertrain": "motor",
    "suspension": "point", "data-acquisition": "logger",
}


def sub_color_key(subsys):
    """COLORS key for a subsystem's representative hue ('custom' if unknown)."""
    return _SUBSYS_COLOR_KEY.get(subsys, "custom")


def _is_wishbone_hardpoints(corner) -> bool:
    """True if `corner` is a double-wishbone Hardpoints (has the named fields)."""
    return isinstance(corner, Hardpoints)


def _extract_corner(corner, ride_drop_mm, color_registry):
    """Normalise a corner (Hardpoints OR GenericKinematics-like) into:
        dict(segments=[(p, q, label, color, group)],
             markers=[points...], wheel_center, contact_patch, camber)
    All points already lowered by ride_drop_mm.

    For wishbones we keep the named-link colour scheme (cyan upper, amber lower,
    etc). For any other topology we draw exactly the members render_segments()
    reports, coloured per-label, so a MacPherson shows a strut, a multi-link
    shows its links, a solid axle shows its Panhard rod — the real architecture.
    """
    drop = np.array([0, 0, ride_drop_mm], float)

    if _is_wishbone_hardpoints(corner):
        pts, s = _solved_corner_points(corner, ride_drop_mm)
        cam = getattr(corner, "static_camber", -1.5)
        # Named wishbone links -> fixed colours (matches the GEOMETRY 3D tab).
        segs = [
            (pts["upper_front_inner"], pts["upper_outer"], "Upper wishbone", COLORS["upper"], "upper"),
            (pts["upper_rear_inner"], pts["upper_outer"], "Upper wishbone", COLORS["upper"], "upper"),
            (pts["lower_front_inner"], pts["lower_outer"], "Lower wishbone", COLORS["lower"], "lower"),
            (pts["lower_rear_inner"], pts["lower_outer"], "Lower wishbone", COLORS["lower"], "lower"),
            (pts["lower_outer"], pts["upper_outer"], "Upright", COLORS["upright"], "upright"),
            (pts["tie_rod_inner"], pts["tie_rod_outer"], "Tie rod", COLORS["tie"], "tie"),
        ]
        po = pts.get("pushrod_outer"); rpv = pts.get("rocker_pivot")
        rpu = pts.get("rocker_pushrod"); rsp = pts.get("rocker_spring")
        spi = pts.get("spring_inner")
        if po is not None and rpu is not None:
            segs.append((po, rpu, "Pushrod", COLORS["push"], "push"))
        if rpv is not None and rpu is not None and rsp is not None:
            segs.append((rpv, rpu, "Rocker", COLORS["rocker"], "rocker"))
            segs.append((rpv, rsp, "Rocker", COLORS["rocker"], "rocker"))
        if rsp is not None and spi is not None:
            segs.append((rsp, spi, "Spring/damper", COLORS["spring"], "spring"))
        # Named pickup points, so each hardpoint can be focused individually.
        _marker_names = {
            "upper_front_inner": "Upper front pickup",
            "upper_rear_inner": "Upper rear pickup",
            "lower_front_inner": "Lower front pickup",
            "lower_rear_inner": "Lower rear pickup",
            "upper_outer": "Upper ball joint",
            "lower_outer": "Lower ball joint",
            "tie_rod_inner": "Tie rod inner",
            "tie_rod_outer": "Tie rod outer",
        }
        markers = [(_marker_names[k], pts[k]) for k in (
            "upper_front_inner", "upper_rear_inner", "lower_front_inner",
            "lower_rear_inner", "upper_outer", "lower_outer",
            "tie_rod_inner", "tie_rod_outer")]
        return dict(segments=segs, markers=markers,
                    wheel_center=pts["wheel_center"],
                    contact_patch=pts["contact_patch"], camber=cam)

    # ---- architecture-agnostic mechanism -------------------------------- #
    # `corner` quacks like GenericKinematics: render_segments(), named_points(),
    # static.wheel_center / contact_patch.
    raw = corner.render_segments()
    segs = []
    for p, q, label in raw:
        p = np.asarray(p, float) - drop
        q = np.asarray(q, float) - drop
        if label == "Wheel":
            continue  # the wheel hub line is drawn from wc/cp below
        color, base = _agnostic_color(label, color_registry)
        segs.append((p, q, label, color, base))
    named = corner.named_points()
    # Friendly names for the abbreviated point codes various topologies expose,
    # so each pickup reads clearly and (where it matches) groups with the named
    # wishbone pickups. Anything unmapped falls back to a de-abbreviated title.
    _AGN = {
        "ufi": "Upper front pickup", "uri": "Upper rear pickup",
        "lfi": "Lower front pickup", "lri": "Lower rear pickup",
        "uo": "Upper ball joint", "lo": "Lower ball joint",
        "tri": "Tie rod inner", "tro": "Tie rod outer",
        "st": "Strut top", "sl": "Strut lower", "wc": "Wheel centre",
        "cp": "Contact patch",
    }
    markers = [(_AGN.get(str(k).lower(),
                         str(k).replace("_", " ").capitalize()),
                np.asarray(v, float) - drop) for k, v in named.items()]
    st = corner.static
    wc = np.asarray(st.wheel_center, float) - drop
    cp = np.asarray(st.contact_patch, float) - drop
    cam = getattr(st, "camber", -1.5)
    return dict(segments=segs, markers=markers,
                wheel_center=wc, contact_patch=cp, camber=cam)


# --------------------------------------------------------------------------- #
#  Sizing laws
# --------------------------------------------------------------------------- #
def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _wing_span_chord(downforce_n, default_span, default_chord):
    """More downforce -> a visibly bigger wing. Monotonic around a 600 N ref."""
    if not downforce_n:
        return default_span, default_chord
    f = _clamp(downforce_n / 600.0, 0.45, 2.2)
    return (default_span * _clamp(f ** 0.3, 0.7, 1.4),
            default_chord * _clamp(f ** 0.5, 0.6, 1.8))


# --------------------------------------------------------------------------- #
#  Ledger helpers
# --------------------------------------------------------------------------- #
def _iface(led, name):
    if led is None:
        return None
    try:
        return led.get(name)
    except Exception:
        try:
            from .interfaces import SubsystemInterface
            if isinstance(led, dict):
                d = led.get("interfaces", {}).get(name)
                return SubsystemInterface.from_dict(d) if d else None
        except Exception:
            return None
    return None


def _g(it, attr, default=None):
    if it is None:
        return default
    v = getattr(it, attr, default)
    return default if v is None else v


# --------------------------------------------------------------------------- #
#  Heatmap — colour the car by a physical metric (heat / power / mass)
# --------------------------------------------------------------------------- #
# A perceptually-ordered cool→warm ramp. Low load reads cool blue/teal, high
# load reads amber/red, so "where's the load concentrated" is legible at a
# glance. Over-budget bodies get a distinct hot magenta-red outline on top.
_HEAT_RAMP = [
    (0.00, (37, 99, 173)),    # deep blue   — light load
    (0.25, (55, 224, 208)),   # teal
    (0.50, (90, 209, 122)),   # green
    (0.70, (255, 176, 46)),   # amber
    (0.88, (255, 90, 82)),    # red
    (1.00, (214, 33, 33)),    # deep red    — heavy load
]
_OVER_BUDGET_COLOR = "#ff2bd6"   # magenta outline for "exceeds capacity"
_HEAT_NEUTRAL = "#3a4048"        # grey for bodies with no value for this metric


def heat_color(t: float) -> str:
    """Hex colour for a normalised load 0..1 along the cool→warm ramp."""
    t = max(0.0, min(1.0, float(t)))
    for (a_t, a_c), (b_t, b_c) in zip(_HEAT_RAMP, _HEAT_RAMP[1:]):
        if t <= b_t:
            f = 0.0 if b_t == a_t else (t - a_t) / (b_t - a_t)
            r = round(a_c[0] + (b_c[0] - a_c[0]) * f)
            g = round(a_c[1] + (b_c[1] - a_c[1]) * f)
            b = round(a_c[2] + (b_c[2] - a_c[2]) * f)
            return "#%02x%02x%02x" % (r, g, b)
    return "#%02x%02x%02x" % _HEAT_RAMP[-1][1]


# Which subsystems carry a meaningful value for each metric, and the human label
# + units shown in the legend. The metric is read from each subsystem's declared
# interface, so the heatmap reflects exactly what the teams have entered.
_METRIC_SPECS = {
    "heat": dict(field="heat_reject_w", label="Heat rejection", unit="W",
                 blurb="Waste heat each subsystem dumps into the car — the load "
                       "your cooling package has to carry away."),
    "power": dict(field="power_draw_w", label="Electrical draw", unit="W",
                  blurb="Continuous electrical draw on the low-voltage system — "
                        "what the supply has to feed."),
    "mass": dict(field="mass_kg", label="Mass", unit="kg",
                 blurb="Each subsystem's mass — what drives the car's weight and "
                       "where the CG ends up."),
}


def subsystem_metrics(vp, ledger, metric: str = "heat") -> dict:
    """Per-subsystem values for a heatmap, plus normalisation + warnings.

    Returns a dict with:
      values   : {subsystem: raw_value}          (only those that declared it)
      norm     : {subsystem: 0..1}               (raw / max, for colouring)
      over     : {subsystem: bool}               (exceeds capacity — see below)
      vmax     : float                            (the scale top, for the legend)
      unit,label,blurb : strings for the legend
      capacity : float | None                     (the car's budget for this metric)
      warnings : [ {severity, message, subsystems} ]  (from the ledger findings)

    Over-budget logic is metric-specific and uses the SAME numbers the ledger's
    consistency checks use, so the colours and the warnings never disagree:
      * heat  — flagged when total declared heat needs more cooling airflow than
                the cooling package can move (the cooling-airflow check).
      * power — flagged when total LV draw exceeds LV supply (the lv-power check).
      * mass  — flagged when the whole-car mass is over the target budget.
    """
    spec = _METRIC_SPECS.get(metric, _METRIC_SPECS["heat"])
    field = spec["field"]

    # Pull the raw value each subsystem declares for this metric.
    values: dict[str, float] = {}
    for s in SUBSYSTEMS:
        it = _iface(ledger, s)
        v = _g(it, field, None)
        if v is not None and float(v) > 0:
            values[s] = float(v)

    vmax = max(values.values()) if values else 0.0
    norm = {s: (v / vmax if vmax > 0 else 0.0) for s, v in values.items()}

    # ---- capacity + over-budget, mirrored from the ledger checks ---------- #
    over: dict[str, bool] = {}
    capacity = None
    findings = []
    try:
        findings = [f.as_dict() if hasattr(f, "as_dict") else f
                    for f in ledger.check_all()]
    except Exception:
        findings = []

    def _finding(check):
        for f in findings:
            if f.get("check") == check:
                return f
        return None

    def _capacity(field):
        """Read a budget/capacity that lives on the IntegrationLedger (where the
        consistency checks read it from), falling back to vp only if absent. This
        keeps the heatmap's legend capacity consistent with the over-budget flags,
        which come from those same ledger checks."""
        for src in (ledger, vp):
            v = getattr(src, field, None)
            if v is not None:
                try:
                    return float(v)
                except Exception:
                    pass
        return 0.0

    if metric == "heat":
        # cooling can move X m³/s; heat-rejecting subsystems each need some airflow.
        cap_air = _capacity("total_cooling_airflow_cms")
        cool_it = _iface(ledger, "cooling")
        cap_air = max(cap_air, float(_g(cool_it, "cooling_airflow_cms", 0.0)))
        capacity = cap_air
        f = _finding("cooling-airflow")
        if f and f.get("severity") in ("fail", "missing"):
            # everything that needs cooling is implicated
            for s in values:
                if s != "cooling":
                    over[s] = True
    elif metric == "power":
        cap_w = _capacity("lv_supply_capacity_w")
        capacity = cap_w
        f = _finding("lv-power")
        if f and f.get("severity") == "fail":
            # The finding names the LV users PLUS "electrics" as the owning system.
            # Flag only the genuine LV draws — those at/near the LV bus voltage —
            # so an HV traction load that merely shares the colourmap isn't blamed
            # for an LV-supply shortfall. Mirrors the ledger's own LV test.
            lv_bus = _capacity("lv_voltage_v") or 24.0
            for s in values:
                it = _iface(ledger, s)
                v_s = _g(it, "voltage_v", None)
                if v_s is None or abs(float(v_s) - lv_bus) < 5.0:
                    over[s] = True
    elif metric == "mass":
        cap_kg = _capacity("target_mass_kg")
        capacity = cap_kg
        # mass is a whole-car budget; flag the heaviest contributors when over.
        f = _finding("mass-budget-total")
        total = sum(values.values())
        if (f and f.get("severity") in ("fail", "warning")) or \
           (cap_kg > 0 and total > cap_kg):
            # mark the top contributors (those above the mean) as the ones to cut
            if values:
                mean_v = total / len(values)
                for s, v in values.items():
                    if v >= mean_v:
                        over[s] = True

    # ---- warnings: the relevant findings, plain-language -------------------#
    relevant = {"heat": {"cooling-airflow", "aero-cooling-duct"},
                "power": {"lv-power", "hv-voltage"},
                "mass": {"mass-budget", "mass-budget-total", "cg", "cg-lateral"}}
    warnings = [
        dict(severity=f.get("severity"), message=f.get("message"),
             subsystems=f.get("subsystems", []))
        for f in findings
        if f.get("check") in relevant.get(metric, set())
        and f.get("severity") in ("warning", "fail", "missing")
    ]

    return dict(values=values, norm=norm, over=over, vmax=vmax,
                unit=spec["unit"], label=spec["label"], blurb=spec["blurb"],
                capacity=capacity, warnings=warnings, metric=metric)


# --------------------------------------------------------------------------- #
#  Main entry point
# --------------------------------------------------------------------------- #
def build_full_car_figure(
    hp_front=None,
    vp=None,
    hp_rear=None,
    ledger=None,
    *,
    corner_front=None,
    corner_rear=None,
    topology_label: str | None = None,
    show_chassis=True, show_tires=True, show_floor=True,
    show_aero=True, show_powertrain=True, show_cooling=True,
    show_electrics=True, show_brakes=True, show_bodywork=True,
    show_cg=True,
    highlight_subsystem: str | None = None,
    focus_subsystem: str | None = None,
    focus_part: str | None = None,
    heatmap: dict | None = None,
    tire_width_mm: float = 180.0,
    part_overrides: dict | None = None,
    custom_parts: list | None = None,
    suppress_subsystems: set | None = None,
    suppress_parts: set | None = None,
    height: int = 720,
):
    """Assemble a live Formula-car 3D figure.

    The suspension reflects the chosen ARCHITECTURE. Pass the corner either as:
      * a double-wishbone `Hardpoints`  (via hp_front / hp_rear), or
      * any topology's solved kinematics (via corner_front / corner_rear) — a
        GenericKinematics-like object exposing render_segments(), named_points()
        and .static.wheel_center / .contact_patch.
    corner_* takes precedence over hp_* when both are given. This lets a
    MacPherson, multi-link, trailing-arm, solid-axle, twist-beam or free-form
    car render its real members instead of being forced into wishbones.

    PART OVERRIDES — user edits to size & position
    ----------------------------------------------
    `part_overrides` lets the user nudge the dimensions and location of any
    drawn part without changing the underlying engineering numbers. It is a
    dict keyed by the part's display name (exactly the `name=` each body is
    drawn with, e.g. "Front wing", "Sidepod (cooling)", "Motor + inverter",
    "Accumulator", "Spaceframe", "Tire", "Brake disc", "Roll hoop", "Driver",
    "Data logger", "Radiator core"). Each value is a dict with any of:

        dx, dy, dz : float   # translate the whole part, in mm (SAE axes)
        sx, sy, sz : float   # per-axis scale about the part's own centroid
        scale      : float   # uniform scale (applied if sx/sy/sz absent)

    The transform is applied at the single chokepoint every body passes
    through (the local `mesh`/`seg` helpers), so it covers every part — meshes
    and line members alike — and the per-subsystem bounding boxes used for
    click-to-zoom are computed AFTER the override, so the camera still frames
    the part where the user moved it. Missing keys default to no change
    (dx=dy=dz=0, scale=1), so an empty/None override leaves the car untouched.

    CUSTOM PARTS — "drop my part on the car"
    ----------------------------------------
    `custom_parts` lets a sub-team drop a part onto the car in REAL millimetres,
    straight off a spec sheet, with no scale-factor fiddling. It is a list of
    dicts, each:

        name : str            # label shown on the body and in the legend
        subsys : str          # which subsystem it belongs to (for colour +
                              #   click-to-zoom + spotlight); any of SUBSYSTEMS,
                              #   or None for a neutral grey "custom" body
        l_mm, w_mm, h_mm : float   # the part's real size: length(x) width(y) height(z)
        x_mm, y_mm, z_mm : float   # where its CENTRE sits in SAE car axes
                              #   (x: +forward from mid-wheelbase, y: +right of
                              #    centreline, z: +up from ground)
        shape : str           # "box" (default) or "cylinder" (l_mm = length
                              #   along x, w_mm = diameter)
        color : str           # optional hex; defaults to the subsystem colour

    Every custom part is a first-class body: it flows through the same `mesh`
    chokepoint as the built-in parts, so it honours part_overrides, the
    highlight spotlight, and — because its vertices are accrued under its
    subsystem — click-to-zoom frames it too. This is the path a powertrain lead
    uses to type "Radiator 289×124×34" and see it sit on the car immediately.
    """
    # Resolve the front/rear corner objects (architecture-agnostic).
    cf = corner_front if corner_front is not None else hp_front
    cr = corner_rear if corner_rear is not None else hp_rear
    if cf is None:
        cf = Hardpoints.default()
    if cr is None:
        cr = cf

    wb = float(getattr(vp, "wheelbase", 1550.0))
    tf = float(getattr(vp, "track_front", 1200.0))
    tr = float(getattr(vp, "track_rear", 1180.0))

    # ---- imported CAD chassis fits the placeholder ------------------------- #
    # A `define_car` chassis (Chassis slot, "fit the rest of the car around this
    # part") is scaled to occupy the SAME footprint as the dummy monocoque on the
    # standard car, so it drops into the placeholder's exact envelope with the
    # wheels already correctly proportioned around it. We keep the car's normal
    # wheelbase/track (the dummy is already sized to them) rather than reshaping,
    # which kept the frame and wheels mismatched. The scale + centre are computed
    # in `_def_target` once the dummy footprint is known, below.
    _car_part = None
    for _cp in (custom_parts or []):
        if _cp.get("define_car") and _cp.get("mesh"):
            _car_part = _cp
            break
    _car_scale = 1.0            # wheels/tyres keep their natural size

    # Softer front spring -> more static sag -> body visibly lower. Cue, not a calc.
    kf = float(getattr(vp, "spring_rate_front", 35.0) or 35.0)
    ride_drop = _clamp((35.0 - kf) * 0.6, -12.0, 18.0)

    # Extract each axle's corner into a uniform, topology-independent description.
    color_registry = {}
    front_corner = _extract_corner(cf, ride_drop, color_registry)
    rear_corner = _extract_corner(cr, ride_drop, color_registry)

    y_center = 0.0
    scale_f = (tf / 2.0) / (abs(front_corner["contact_patch"][1] - y_center) or 1.0)
    scale_r = (tr / 2.0) / (abs(rear_corner["contact_patch"][1] - y_center) or 1.0)
    x_front, x_rear = +wb / 2.0, -wb / 2.0

    fig = go.Figure()

    # ---- user part overrides (size & position) -------------------------- #
    # Resolve once: a part name -> (dx, dy, dz, sx, sy, sz). Scaling is about
    # the part's centroid so a part keeps its place while changing size; the
    # translate then moves it. Parts drawn in several pieces (a wing = elements
    # + endplates, a "Tire" = tire + rim across 4 corners) must scale about a
    # SHARED centroid, else the pieces fly apart — so we pin each part's scale
    # centre to the centroid of the FIRST batch of vertices seen under that
    # name, and reuse it for every later piece. Overrides are applied at the
    # mesh/seg chokepoint, before _accrue, so click-to-zoom frames the moved part.
    _ov = part_overrides or {}

    # Subsystems / parts whose PROCEDURAL body is replaced by a user CAD/custom
    # part: skip drawing the stand-in so only the real geometry shows there. A
    # suppressed SUBSYSTEM also suppresses every catalog body it owns (via
    # PART_CATALOG). Callers may pass either subsystem keys (suppress_subsystems)
    # or explicit draw-names (suppress_parts); the two are unioned into one set
    # of draw-names that the mesh/seg chokepoint honours through _is_hidden.
    _suppress = set(suppress_subsystems or ())
    _suppress_names = set(suppress_parts or ())
    try:
        for _pk, _pdn, _ps, _pc in PART_CATALOG:
            if _ps in _suppress:
                _suppress_names.add(_pdn)
    except Exception:
        pass

    # Real drawn extent of each built-in body, keyed by draw-name — recorded even
    # when the body is then suppressed, so a replacing CAD part can be fitted to
    # EXACTLY the box its placeholder occupied. Exposed on the returned figure as
    # `_part_boxes` for the Streamlit CAD "fit to area" tool.
    _part_boxes: dict = {}
    # Per-call boxes under each draw-name, clustered into per-INSTANCE boxes
    # after the build — see fig._part_instance_boxes below.
    _part_pieces: dict = {}

    def _ov_for(name):
        o = _ov.get(name) if name else None
        if not o:
            return None
        dx = float(o.get("dx", 0.0) or 0.0)
        dy = float(o.get("dy", 0.0) or 0.0)
        dz = float(o.get("dz", 0.0) or 0.0)
        us = o.get("scale", 1.0)
        us = 1.0 if us is None else float(us)
        sx = float(o.get("sx", us) or us)
        sy = float(o.get("sy", us) or us)
        sz = float(o.get("sz", us) or us)
        if dx == dy == dz == 0.0 and sx == sy == sz == 1.0:
            return None
        return (dx, dy, dz, sx, sy, sz)

    def _is_hidden(name):
        """True when this body should be skipped — either because a part_override
        asks to hide it, or because a user CAD/custom part replaces its subsystem
        or the body itself (suppress_subsystems / suppress_parts)."""
        if name and name in _suppress_names:
            return True
        o = _ov.get(name) if name else None
        return bool(o and o.get("hide"))

    _scale_centre: dict[str, np.ndarray] = {}
    # Parts drawn once per corner (4×): scale each instance about ITS OWN
    # centroid (so each tire/disc grows in place) rather than a shared car-wide
    # centre (which would splay the four apart). Translate still applies to all.
    _PER_INSTANCE = {"Tire", "Brake disc"}

    def _apply_ov(name, V):
        """Return V transformed by the named part's override (or V unchanged)."""
        spec = _ov_for(name)
        if spec is None:
            return V
        dx, dy, dz, sx, sy, sz = spec
        A = np.asarray(V, float)
        flat = A.reshape(-1, 3)
        if name in _PER_INSTANCE:
            c = flat.mean(axis=0)            # this instance's own centre
        else:
            c = _scale_centre.get(name)
            if c is None:
                c = flat.mean(axis=0)
                _scale_centre[name] = c
        out = (flat - c) * np.array([sx, sy, sz]) + c + np.array([dx, dy, dz])
        return out.reshape(A.shape)

    # `highlight_subsystem` may be a single key (legacy) or a set/list/tuple of
    # keys (so several blended subteams can all glow at full opacity while the
    # rest of the car ghosts back). Normalise to a set once.
    if highlight_subsystem is None:
        _hl_set = None
    elif isinstance(highlight_subsystem, (set, list, tuple)):
        _hl_set = {h for h in highlight_subsystem if h}
        if not _hl_set:
            _hl_set = None
    else:
        _hl_set = {highlight_subsystem}

    def op(subsys, base):
        if _hl_set is None:
            return base
        # Highlighted parts are pushed to FULL opacity (brighter than their
        # resting state) so selecting a subteam reads as the part *lighting up*,
        # not merely the rest dimming. Everything else ghosts hard.
        if subsys in _hl_set:
            return min(1.0, max(base, 0.92))
        return base * 0.16

    def edge_op(subsys):
        if _hl_set is None or subsys is None:
            return 1.0
        return 1.0 if subsys in _hl_set else 0.14

    # Heatmap colouring. When a heatmap is supplied, a body's own colour is
    # replaced by the cool→warm colour for its subsystem's normalised load, so
    # the whole car reads as a load map. Subsystems that declared no value for
    # the chosen metric go neutral grey (they're not part of this story). The
    # set of over-budget subsystems is returned so we can outline them.
    _hm_norm = (heatmap or {}).get("norm", {}) if heatmap else {}
    _hm_over = (heatmap or {}).get("over", {}) if heatmap else {}

    def heat_for(subsys):
        """(fill_colour, is_over) for a subsystem under the active heatmap, or
        (None, False) when no heatmap is active."""
        if not heatmap or not subsys:
            return None, False
        if subsys in _hm_norm:
            return heat_color(_hm_norm[subsys]), bool(_hm_over.get(subsys))
        return _HEAT_NEUTRAL, False

    legend_done = set()

    # Per-subsystem point accumulator. Every body that belongs to a clickable
    # subsystem feeds its vertices here, so afterwards we know the bounding box
    # of each subsystem and can frame the camera on whichever one is clicked.
    subsys_pts: dict[str, list] = {}
    # Per-PART point accumulator, keyed by the body's display name (e.g. "Front
    # wing", "Tire", "Brake disc"). This is the finer grain that lets the camera
    # frame a single part — not just the whole subsystem it belongs to — so a
    # part-level click/pick can zoom onto exactly that body. Several stations may
    # share a name (four tires, four brake discs); they all merge under the one
    # key, so focusing "Tire" frames all four, which is the sensible behaviour.
    part_pts: dict[str, list] = {}

    def _accrue(subsys, pts, part=None, corner=None):
        if pts is None:
            return
        arr = np.asarray(pts, float).reshape(-1, 3).tolist()
        if subsys:
            subsys_pts.setdefault(subsys, []).extend(arr)
        if part:
            part_pts.setdefault(part, []).extend(arr)
            # Also bucket a per-CORNER key ("<part>#FL") so a part that exists at
            # all four wheels can be drilled to a single corner. The shared key
            # above still frames all instances; this finer key frames just one.
            if corner:
                part_pts.setdefault("%s#%s" % (part, corner), []).extend(arr)

    def seg(p, q, color, w=5, name=None, group=None, subsys=None, corner=None):
        if p is None or q is None:
            return
        # Override key: prefer the legend name, fall back to the group token so
        # unnamed members of a named part (hoop braces, wing mounts) move too.
        _ovk = name if (name and name in _ov) else (group if group in _ov else name)
        if _is_hidden(name) or _is_hidden(group):
            return
        pq = _apply_ov(_ovk, np.array([p, q], float))
        p, q = pq[0], pq[1]
        # Accrue under a STABLE part key. corner_name() returns a label only the
        # first time (legend dedup), so for the 2nd–4th corners `name` is None;
        # the `group` token, however, is the same on every corner. Prefer it so
        # all four corners of e.g. the upper wishbone merge into one focus box,
        # while the per-corner key (added in _accrue) frames a single corner.
        _part_key = group or name
        _accrue(subsys, [p, q], part=_part_key, corner=corner)
        # Under a heatmap, recolour line members by their subsystem's load too (so
        # e.g. brake lines warm with the discs) and grey out subsystems with no
        # value, so the bright topology hues don't fight the load colourmap.
        _lcol = color
        _lop = edge_op(subsys)
        if heatmap:
            _hc, _ = heat_for(subsys)
            if _hc is not None:
                _lcol = _hc
                _lop = 0.95 if subsys in _hm_norm else 0.5
        fig.add_trace(go.Scatter3d(
            x=[p[0], q[0]], y=[p[1], q[1]], z=[p[2], q[2]],
            mode="lines", line=dict(color=_lcol, width=w),
            opacity=_lop, name=name, legendgroup=group,
            showlegend=name is not None, hoverinfo="skip",
            customdata=[[subsys, _part_key, corner or ""]] * 2 if subsys else None))

    def mesh(verts, i, j, k, color, name, subsys, base_op=0.6, hover=None,
             corner=None):
        # Record this body's true drawn box (keyed by draw-name) BEFORE any
        # suppression, so a replacing CAD part can land in exactly the same place
        # its placeholder occupied — even when the placeholder is now hidden.
        if name:
            try:
                _vv = _apply_ov(name, verts)
                _lo = np.asarray(_vv, float).reshape(-1, 3).min(axis=0)
                _hi = np.asarray(_vv, float).reshape(-1, 3).max(axis=0)
                if name in _part_boxes:
                    _pb = _part_boxes[name]
                    _pb[0] = np.minimum(_pb[0], _lo)
                    _pb[1] = np.maximum(_pb[1], _hi)
                else:
                    _part_boxes[name] = [_lo.copy(), _hi.copy()]
                # Also keep the PER-CALL box: a body drawn once per corner /
                # side (4 tires, 2 radiators) merges into one misleading
                # car-spanning box above; the pieces are clustered into true
                # per-INSTANCE boxes after the build (fig._part_instance_boxes)
                # so fit / clearance tools can reason about one instance.
                _part_pieces.setdefault(name, []).append(
                    (_lo.copy(), _hi.copy()))
            except Exception:
                pass
        if _is_hidden(name):
            return
        once = name not in legend_done
        legend_done.add(name)
        verts = _apply_ov(name, verts)
        _accrue(subsys, verts, part=name, corner=corner)
        # customdata carries the clickable subsystem id, the part name, and the
        # corner tag (if any) on every vertex, so a Streamlit selection event can
        # read which subsystem, which part, AND which corner the user picked.
        cd = [[subsys, name, corner or ""]] * len(verts) if subsys else None
        # Heatmap override: recolour by load, and lift opacity so the colour
        # reads cleanly. Over-budget bodies are drawn solid + outlined below.
        _hcol, _hover_budget = heat_for(subsys)
        _fill = _hcol if _hcol is not None else color
        _mop = (max(base_op, 0.85) if _hcol is not None else op(subsys, base_op))
        fig.add_trace(go.Mesh3d(
            x=verts[:, 0], y=verts[:, 1], z=verts[:, 2], i=i, j=j, k=k,
            color=_fill, opacity=_mop, flatshading=True,
            name=name, showlegend=once, customdata=cd,
            hoverinfo="text" if hover else "skip", text=hover))
        # Over-budget outline: a bright magenta bounding-box wireframe when this
        # body's subsystem exceeds the car's capacity for the active metric, so
        # failures pop without flooding the scene with per-triangle edges.
        if _hover_budget:
            try:
                lo = verts.min(axis=0)
                hi = verts.max(axis=0)
                _bx, _by, _bz = _bbox_wire(lo, hi)
                fig.add_trace(go.Scatter3d(
                    x=_bx, y=_by, z=_bz, mode="lines",
                    line=dict(color=_OVER_BUDGET_COLOR, width=5),
                    opacity=0.95, showlegend=False, hoverinfo="skip"))
            except Exception:
                pass

    def corner_name(base):
        if base in legend_done:
            return None
        legend_done.add(base)
        return base

    # ---- 1) suspension corners + tires + brake discs -------------------- #
    #  Each station reuses the SAME extracted corner description, transformed to
    #  its wheel position (mirror L/R, scale to axle track, shift fore/aft). The
    #  segments came from the chosen topology, so a MacPherson draws a strut, a
    #  multi-link draws its links, etc — the architecture is honoured everywhere.
    stations = [
        ("front", front_corner, scale_f, x_front, False),
        ("front", front_corner, scale_f, x_front, True),
        ("rear",  rear_corner,  scale_r, x_rear,  False),
        ("rear",  rear_corner,  scale_r, x_rear,  True),
    ]
    brake_tq = _g(_iface(ledger, "brakes"), "brake_torque_nm")

    def _xform(p, mirror, lat_scale, x_shift):
        return _corner_transform(p, mirror_y=mirror, lateral_scale=lat_scale,
                                 x_shift=x_shift, y_center_ref=y_center,
                                 size_scale=_car_scale)

    for axle, corner, lat_scale, x_shift, mirror in stations:
        # Stable corner id (FL/FR/RL/RR) for per-corner focus. Side comes from the
        # TRANSFORMED wheel-centre y (y is "right"), so it's correct regardless of
        # the base geometry's handedness or the mirror convention.
        _wc_tag = _xform(corner["wheel_center"], mirror, lat_scale, x_shift)
        _side = "R" if _wc_tag[1] >= 0 else "L"
        _corner_id = ("F" if axle == "front" else "R") + _side

        # draw every member the topology reported
        for p, q, label, color, group in corner["segments"]:
            pT = _xform(p, mirror, lat_scale, x_shift)
            qT = _xform(q, mirror, lat_scale, x_shift)
            seg(pT, qT, color, 5, corner_name(label), group, "suspension",
                corner=_corner_id)

        wc = _xform(corner["wheel_center"], mirror, lat_scale, x_shift)
        cp = _xform(corner["contact_patch"], mirror, lat_scale, x_shift)
        # wheel hub line
        seg(cp, wc, COLORS["wheel"], 3, corner_name("Wheel hub"), "wheel",
            "suspension", corner=_corner_id)

        cam = np.deg2rad(corner["camber"])
        sign = -1.0 if mirror else 1.0
        axis = np.array([0.0, sign * np.cos(cam), np.sin(cam)])
        radius = abs(wc[2] - cp[2]) or 228.0
        if show_tires:
            tv, ti, tj, tk = _cylinder(wc, axis, radius, tire_width_mm, n=30)
            mesh(tv, ti, tj, tk, COLORS["tire"], "Tire", "suspension",
                 base_op=0.95, corner=_corner_id)
            # Rim: a slightly inset, lighter disc so the wheel reads as a wheel,
            # not a black drum — sits at ~62% of tire radius on the outboard face.
            rim_r = radius * 0.62
            rv, ri, rj, rk = _cylinder(wc, axis, rim_r, tire_width_mm * 0.9, n=24)
            mesh(rv, ri, rj, rk, COLORS["rim"], "Tire", "suspension",
                 base_op=0.98, corner=_corner_id)
        if show_brakes:
            disc_r = (radius * _clamp(0.62 + (brake_tq or 0) / 4000.0, 0.5, 0.85)
                      if brake_tq else radius * 0.62)
            dv, di, dj, dk = _cylinder(wc, axis, disc_r,
                                       max(8.0, tire_width_mm * 0.07), n=26)
            hv = ("Brake disc · r≈%.0f mm" % disc_r
                  + (" (sized from %.0f N·m)" % brake_tq if brake_tq else ""))
            mesh(dv, di, dj, dk, COLORS["brake"], "Brake disc", "brakes",
                 base_op=0.9, hover=hv, corner=_corner_id)

        # Inner pickup points: each is now NAMED and individually focusable. We
        # draw them as one trace per point so a click (or a part button) can frame
        # a single hardpoint at a single corner. The part key is the pickup name;
        # the per-corner key ("Upper front pickup#FL") frames just this corner's.
        for _mname, _mpt in corner["markers"]:
            _mT = _xform(_mpt, mirror, lat_scale, x_shift)
            _accrue("suspension", [_mT], part=_mname, corner=_corner_id)
            fig.add_trace(go.Scatter3d(
                x=[_mT[0]], y=[_mT[1]], z=[_mT[2]],
                mode="markers", marker=dict(size=4, color=COLORS["point"]),
                opacity=edge_op("suspension"), showlegend=False,
                hoverinfo="text", text="%s · %s" % (_mname, _corner_id),
                customdata=[["suspension", _mname, _corner_id]]))

    # z-extent + tire radius derived from the extracted corners (any topology).
    z_all = []
    for corner in (front_corner, rear_corner):
        for p, q, *_ in corner["segments"]:
            z_all += [p[2], q[2]]
        z_all += [corner["wheel_center"][2], corner["contact_patch"][2]]
    z_lo, z_hi = (min(z_all), max(z_all)) if z_all else (0.0, 300.0)
    tire_r = (abs(front_corner["wheel_center"][2]
                  - front_corner["contact_patch"][2]) or 228.0) * _car_scale
    inner_y_f = tf / 2.0 - tire_width_mm - 40
    inner_y_r = tr / 2.0 - tire_width_mm - 40

    # For a `define_car` chassis: scale it to MATCH the dummy monocoque's real
    # footprint — the placeholder is already proportioned correctly to the wheels
    # and the rest of the car, so matching it makes the imported frame sit in the
    # same envelope. We compute the dummy's exact extents (the same formulas the
    # monocoque uses below) and scale the CAD uniformly (true shape) so its
    # dominant length matches, centred on the dummy's centre.
    _def_target = None
    if _car_part is not None:
        try:
            _pl0 = float(_car_part.get("l_mm", 0) or 0)
            _pw0 = float(_car_part.get("w_mm", 0) or 0)
            _ph0 = float(_car_part.get("h_mm", 0) or 0)

            # --- dummy monocoque footprint (mirror the section-2 formulas) ---
            _dummy_tub_w = _clamp(min(inner_y_f, inner_y_r) * 1.1, 140, 320)
            _dummy_tub_bot = max(z_lo * 0.5, tire_r * 0.14)
            _dummy_tub_top = _dummy_tub_bot + _clamp(tire_r * 0.95, 180, 360)
            _dummy_nose_x = x_front + tire_r * 1.9
            _dummy_tail_x = x_rear + tire_r * 0.15
            _dummy_len = abs(_dummy_nose_x - _dummy_tail_x)
            _dummy_wid = _dummy_tub_w
            _dummy_hgt = _dummy_tub_top - _dummy_tub_bot
            _dummy_cx = (_dummy_nose_x + _dummy_tail_x) / 2.0
            _dummy_cz = (_dummy_tub_top + _dummy_tub_bot) / 2.0

            # Scale so the CAD fits INSIDE the dummy monocoque envelope on every
            # axis (tightest ratio), keeping true shape, so it occupies the same
            # space the placeholder did without poking past it. Length-dominant
            # frames match the length; bulky ones match width/height.
            _ratios = []
            if _pl0 > 1:
                _ratios.append(_dummy_len / _pl0)
            if _pw0 > 1:
                _ratios.append(_dummy_wid / _pw0)
            if _ph0 > 1:
                _ratios.append(_dummy_hgt / _ph0)
            _def_scale = max(0.01, min(_ratios) if _ratios else 1.0)
            _def_target = dict(
                scale=_def_scale,
                centre=(_dummy_cx, 0.0, _dummy_cz))
        except Exception:
            _def_target = None

    # ---- 2) chassis: FSAE steel SPACEFRAME + roll hoops + driver -------- #
    #  Tube frame in place of the old lofted monocoque shell: front bulkhead,
    #  front hoop, cockpit bay, rear box, with bottom/top rails and
    #  side-impact diagonals — reads instantly as a welded FSAE spaceframe.
    #  Spans the SAME envelope the shell did (nose tip -> tail, same width and
    #  deck heights), so slot fitting, part boxes and `define_car` scaling all
    #  keep working unchanged.
    if show_bodywork:
        ch_it = _iface(ledger, "chassis")
        tub_w = _clamp(min(inner_y_f, inner_y_r) * 1.1, 140, 320)
        # Sit the frame low like an FSAE car: floor near the ground, deck low.
        tub_bot = max(z_lo * 0.5, tire_r * 0.14)
        tub_top = tub_bot + _clamp(tire_r * 0.95, 180, 360)
        hzz = (tub_top - tub_bot) / 2
        nose_tip_x = x_front + tire_r * 1.9
        tail_x = x_rear + tire_r * 0.15

        # Frame stations, front to rear: (x, half-width, z_bottom, z_top).
        _stn = [
            # front bulkhead — small, raised off the floor line
            (nose_tip_x, tub_w * 0.22,
             tub_bot + hzz * 0.50, tub_bot + hzz * 1.30),
            # front hoop (dash) — full width, taller than the deck
            (x_front - wb * 0.05, tub_w * 0.48, tub_bot, tub_top + hzz * 0.55),
            # main-hoop station (cockpit rear) — the curved hoop itself is the
            # separate "Roll hoop" body drawn elsewhere
            (x_front - wb * 0.34, tub_w * 0.50, tub_bot, tub_top),
            # rear bulkhead / rear box
            (tail_x, tub_w * 0.36, tub_bot, tub_bot + hzz * 1.60),
        ]
        _rt = _clamp(tub_w * 0.045, 8.0, 14.0)     # tube radius (~25 mm OD)

        _fv, _fi, _fj, _fk = [], [], [], []
        _off = 0

        def _frame_tube(p, q):
            nonlocal _off
            v, i, j, k = _tube(np.asarray(p, float), np.asarray(q, float),
                               _rt, n=10)
            _fv.append(v)
            _fi.extend((np.asarray(i) + _off).tolist())
            _fj.extend((np.asarray(j) + _off).tolist())
            _fk.extend((np.asarray(k) + _off).tolist())
            _off += len(v)

        for _s in (-1.0, 1.0):                       # left / right side
            for _a, _b in zip(_stn[:-1], _stn[1:]):
                # bottom rail, top rail, and the bay's side-impact diagonal
                _frame_tube((_a[0], _s * _a[1], _a[2]),
                            (_b[0], _s * _b[1], _b[2]))
                _frame_tube((_a[0], _s * _a[1], _a[3]),
                            (_b[0], _s * _b[1], _b[3]))
                _frame_tube((_a[0], _s * _a[1], _a[2]),
                            (_b[0], _s * _b[1], _b[3]))
            for _p in _stn:                          # station verticals
                _frame_tube((_p[0], _s * _p[1], _p[2]),
                            (_p[0], _s * _p[1], _p[3]))
        for _p in _stn:                              # station cross members
            _frame_tube((_p[0], -_p[1], _p[2]), (_p[0], +_p[1], _p[2]))
            _frame_tube((_p[0], -_p[1], _p[3]), (_p[0], +_p[1], _p[3]))
        # floor cross bracing in the two big bays (cockpit + rear)
        for _a, _b in ((_stn[1], _stn[2]), (_stn[2], _stn[3])):
            _frame_tube((_a[0], -_a[1], _a[2]), (_b[0], +_b[1], _b[2]))
            _frame_tube((_a[0], +_a[1], _a[2]), (_b[0], -_b[1], _b[2]))

        hv = "Spaceframe / welded tube frame"
        if _g(ch_it, "mass_kg"):
            hv += " · %.1f kg" % _g(ch_it, "mass_kg")
        mesh(np.vstack(_fv), np.array(_fi), np.array(_fj), np.array(_fk),
             COLORS["frame_tube"], "Spaceframe", "chassis",
             base_op=0.96, hover=hv)

        # Mid-frame deck height, reused by the roll hoops / driver below.
        cz = (tub_top + tub_bot) / 2

        # Cockpit opening reference + driver: a helmet sphere sitting in the bay.
        cockpit_x = x_front - wb * 0.16
        helmet_r = tub_w * 0.34
        helmet_z = tub_top + helmet_r * 0.65
        hv_e = _sphere([cockpit_x, 0, helmet_z], helmet_r, n=16)
        mesh(hv_e[0], hv_e[1], hv_e[2], hv_e[3], COLORS["helmet"],
             "Driver", "chassis", 0.98, "Driver (helmet)")
        # Helmet stripe band.
        bandv = _sphere([cockpit_x, 0, helmet_z], helmet_r * 1.01, n=14)
        bv = bandv[0]
        keep = np.abs(bv[:, 2] - helmet_z) < helmet_r * 0.18
        if keep.any():
            seg([cockpit_x - helmet_r, 0, helmet_z],
                [cockpit_x + helmet_r, 0, helmet_z],
                COLORS["helmet_band"], 6, None, "helmet", "chassis")

        # MAIN roll hoop: a curved tube arching above and behind the helmet.
        hoop_x = cockpit_x - tire_r * 0.55
        hoop_w = tub_w * 0.92
        hoop_top = helmet_z + helmet_r * 0.9
        main_hoop = [
            [hoop_x, -hoop_w, tub_bot + hzz * 0.2],
            [hoop_x, -hoop_w * 0.95, cz],
            [hoop_x - tire_r * 0.1, -hoop_w * 0.55, hoop_top * 0.92],
            [hoop_x - tire_r * 0.12, 0, hoop_top],
            [hoop_x - tire_r * 0.1, hoop_w * 0.55, hoop_top * 0.92],
            [hoop_x, hoop_w * 0.95, cz],
            [hoop_x, hoop_w, tub_bot + hzz * 0.2],
        ]
        mh = _swept_tube(main_hoop, radius=tire_r * 0.07, n=10)
        mesh(mh[0], mh[1], mh[2], mh[3], COLORS["hoop"], "Roll hoop",
             "chassis", 0.95, "Main roll hoop")
        # Rear hoop braces down to the tub.
        for sgn in (-1, 1):
            tb = _tube([hoop_x - tire_r * 0.1, sgn * hoop_w * 0.5, hoop_top * 0.92],
                       [hoop_x - tire_r * 1.1, sgn * hoop_w * 0.4, tub_bot + hzz * 0.3],
                       radius=tire_r * 0.045, n=8)
            mesh(tb[0], tb[1], tb[2], tb[3], COLORS["frame"], "Roll hoop",
                 "chassis", 0.95)

        # FRONT hoop: smaller, ahead of the dash.
        fh_x = cockpit_x + tire_r * 1.0
        fh_top = tub_top + helmet_r * 0.2
        front_hoop = [
            [fh_x, -tub_w * 0.7, cz],
            [fh_x, -tub_w * 0.5, fh_top],
            [fh_x, 0, fh_top + helmet_r * 0.1],
            [fh_x, tub_w * 0.5, fh_top],
            [fh_x, tub_w * 0.7, cz],
        ]
        fhm = _swept_tube(front_hoop, radius=tire_r * 0.05, n=8)
        mesh(fhm[0], fhm[1], fhm[2], fhm[3], COLORS["frame"], "Roll hoop",
             "chassis", 0.95, "Front hoop")

    # ---- 3) aerodynamics: multi-element wings + endplates --------------- #
    #  FSAE-style: a wide multi-element FRONT wing low and ahead of the front
    #  axle on endplates, and a tall multi-element REAR wing on twin endplates
    #  behind the rear axle. Element count/size still scale with declared
    #  downforce, so the aero team's number visibly grows the wing.
    if show_aero:
        aero_it = _iface(ledger, "aerodynamics")
        df = _g(aero_it, "downforce_n_at_v")
        df_n = df[0] if isinstance(df, (tuple, list)) and df else None

        def _elements(df_n):
            # more downforce -> more elements (2..4) and a touch more chord
            if not df_n:
                return 3
            return int(_clamp(2 + df_n / 500.0, 2, 4))

        # ---- FRONT wing: low, ahead of the front axle --------------------
        fw_span, fw_chord = _wing_span_chord(df_n, tf * 0.98, tire_r * 0.62)
        fw_x = x_front + tire_r * 1.62
        fw_z = tire_r * 0.32
        n_fe = _elements(df_n)
        hint_f = "Front wing" + (" (sized from %.0f N)" % df_n if df_n else "")
        for e in range(n_fe):
            ex = fw_x - e * fw_chord * 0.42
            ez = fw_z + e * fw_chord * 0.22
            ch = fw_chord * (0.7 + 0.12 * e)
            wv = _wing_element(ex, 0, ez, ch, fw_span,
                               thickness=0.11, aoa_deg=-8 - 4 * e)
            mesh(wv[0], wv[1], wv[2], wv[3], COLORS["wing"], "Front wing",
                 "aerodynamics", 0.9, hint_f if e == 0 else None)
        # endplates by the front tires
        for sgn in (-1, 1):
            ev, ei, ej, ek = _box(fw_x - fw_chord * 0.3, sgn * fw_span / 2,
                                   fw_z + fw_chord * 0.15,
                                   fw_chord * 1.5, 6, fw_chord * 1.1)
            mesh(ev, ei, ej, ek, COLORS["endplate"], "Front wing",
                 "aerodynamics", 0.9)
        # nose-to-wing pylons
        _mount_y = (tub_w * 0.3) if show_bodywork else (fw_span * 0.12)
        for sgn in (-1, 1):
            seg([fw_x, sgn * fw_span * 0.18, fw_z],
                [fw_x - fw_chord, sgn * _mount_y, fw_z + tire_r * 0.4],
                COLORS["wing_edge"], 4, None, "fw_mount", "aerodynamics")

        # ---- REAR wing: tall, behind the rear axle -----------------------
        rw_span, rw_chord = _wing_span_chord(df_n, tr * 0.82, tire_r * 0.72)
        rw_x = x_rear - tire_r * 1.55
        rw_z = z_hi + tire_r * 1.15
        n_re = _elements(df_n)
        hint_r = "Rear wing" + (" (sized from %.0f N)" % df_n if df_n else "")
        for e in range(n_re):
            ex = rw_x + e * rw_chord * 0.4
            ez = rw_z + e * rw_chord * 0.34
            ch = rw_chord * (0.8 + 0.1 * e)
            wv = _wing_element(ex, 0, ez, ch, rw_span,
                               thickness=0.12, aoa_deg=-12 - 5 * e)
            mesh(wv[0], wv[1], wv[2], wv[3], COLORS["wing"], "Rear wing",
                 "aerodynamics", 0.9, hint_r if e == 0 else None)
        # twin endplates
        for sgn in (-1, 1):
            ev, ei, ej, ek = _box(rw_x + rw_chord * 0.3, sgn * rw_span / 2,
                                   rw_z + rw_chord * 0.5,
                                   rw_chord * 2.0, 8, rw_chord * 2.2)
            mesh(ev, ei, ej, ek, COLORS["endplate"], "Rear wing",
                 "aerodynamics", 0.92)
        # rear-wing support struts up from the gearbox/tail
        for sgn in (-1, 1):
            seg([rw_x + rw_chord * 0.3, sgn * rw_span * 0.18, rw_z - rw_chord * 0.4],
                [rw_x + rw_chord * 1.2, sgn * rw_span * 0.12, z_hi * 0.7],
                COLORS["wing_edge"], 5, None, "rw_mount", "aerodynamics")

    # ---- 4) cooling: sidepods ------------------------------------------ #
    if show_cooling:
        cool_it = _iface(ledger, "cooling")
        airflow = _g(cool_it, "cooling_airflow_cms")
        heat = _g(cool_it, "heat_reject_w")
        f = _clamp((airflow or 0.4) / 0.4, 0.5, 2.2)
        pod_len = wb * 0.34 * _clamp(f ** 0.4, 0.7, 1.5)
        pod_h = tire_r * 0.7 * _clamp(f ** 0.4, 0.7, 1.4)
        pod_w = 110 * _clamp(f ** 0.5, 0.7, 1.6)
        pod_x = -wb * 0.05
        for sgn in (-1, 1):
            pod_y = sgn * (min(inner_y_f, inner_y_r) * 0.95)
            v, i, j, k = _box(pod_x, pod_y, tire_r * 0.65, pod_len, pod_w, pod_h)
            hv = "Sidepod / radiator duct"
            if airflow:
                hv += " (sized from %.2f m³/s)" % airflow
            if heat:
                hv += " · rejects %.0f W" % heat
            mesh(v, i, j, k, COLORS["sidepod"], "Sidepod (cooling)", "cooling", 0.7, hv)
            rv, ri, rj, rk = _box(pod_x + pod_len / 2, pod_y, tire_r * 0.65,
                                  8, pod_w * 0.8, pod_h * 0.8)
            mesh(rv, ri, rj, rk, COLORS["radiator"], "Radiator core", "cooling", 0.85)

    # ---- 5) powertrain: EV traction motor + inverter + driveshafts ------ #
    #  This is an FSAE EV, so the rear package is a compact traction motor with
    #  its inverter on top, not an IC engine + airbox. Sizing still tracks the
    #  declared power/torque so the powertrain team's number drives the body.
    if show_powertrain:
        pt_it = _iface(ledger, "powertrain")
        pkw = _g(pt_it, "peak_power_kw")
        ptq = _g(pt_it, "peak_torque_nm")
        ex, ey, ez = _g(pt_it, "env_x_mm"), _g(pt_it, "env_y_mm"), _g(pt_it, "env_z_mm")
        if ex and ey and ez:
            blk_l, blk_w, blk_h = ex, ey, ez
            sized = "(declared envelope)"
        else:
            f = _clamp((pkw or 60) / 60.0, 0.5, 2.0)
            blk_l = wb * 0.16 * _clamp(f ** 0.4, 0.7, 1.4)
            blk_w = min(inner_y_r, 150) * 1.2
            blk_h = tire_r * 0.7 * _clamp(f ** 0.3, 0.8, 1.3)
            sized = ("(sized from %.0f kW)" % pkw if pkw else "")
        mot_x = x_rear + tire_r * 1.05
        # Traction motor: a cylinder lying across the car (EV motor, not a block).
        mc = _cylinder([mot_x, 0, tire_r * 0.8], [0, 1, 0],
                       radius=blk_h * 0.55, length=blk_w, n=22)
        hv = "Traction motor " + sized + (" · %.0f N·m" % ptq if ptq else "")
        mesh(mc[0], mc[1], mc[2], mc[3], COLORS["motor"], "Motor + inverter",
             "powertrain", 0.92, hv)
        # Inverter box sitting on top.
        iv, ii, ij, ik = _box(mot_x - blk_l * 0.1, 0, tire_r * 0.8 + blk_h * 0.6,
                              blk_l * 0.8, blk_w * 0.7, blk_h * 0.45)
        mesh(iv, ii, ij, ik, COLORS["engine"], "Motor + inverter", "powertrain",
             0.9, "Inverter")
        # Driveshafts to the rear wheels.
        for sgn in (-1, 1):
            seg([mot_x, sgn * blk_w * 0.45, tire_r * 0.8],
                [x_rear, sgn * tr / 2 * 0.78, tire_r],
                "#8d99a6", 5, None, "drive", "powertrain")

    # ---- 6) electrics: accumulator ------------------------------------- #
    if show_electrics:
        el_it = _iface(ledger, "electrics")
        ex, ey, ez = _g(el_it, "env_x_mm"), _g(el_it, "env_y_mm"), _g(el_it, "env_z_mm")
        emass, pwr = _g(el_it, "mass_kg"), _g(el_it, "power_draw_w")
        bl = bw = bh = 0
        sized = ""
        if ex and ey and ez:
            bl, bw, bh, sized = ex, ey, ez, "(declared envelope)"
        elif emass:
            side = (_clamp(emass, 2, 40) * 1.6e6) ** (1 / 3)
            bl, bw, bh = side * 1.4, side * 1.1, side * 0.7
            sized = "(sized from %.1f kg)" % emass
        else:
            # Nothing declared yet: draw a nominal accumulator box so the part
            # is always present in the car and clickable, with a hint that it's
            # a placeholder until electrics declares an envelope or mass.
            bl, bw, bh = wb * 0.16, min(inner_y_r, 160) * 1.2, tire_r * 0.55
            sized = "(placeholder — declare envelope/mass in INTEGRATION)"
        if bl:
            bx = x_rear + tire_r * 2.6
            v, i, j, k = _box(bx, 0, tire_r * 0.55, bl, bw, bh)
            hv = "Accumulator / battery " + sized + (" · %.0f W" % pwr if pwr else "")
            mesh(v, i, j, k, COLORS["battery"], "Accumulator", "electrics", 0.85, hv)

    # ---- 7) data-acquisition: logger pod ------------------------------- #
    daq_it = _iface(ledger, "data-acquisition")
    _daq_mass = _g(daq_it, "mass_kg") if daq_it is not None else None
    v, i, j, k = _box(x_front - wb * 0.1, -tf * 0.18, tire_r * 1.05, 80, 60, 40)
    _daq_hv = ("Data-acquisition logger · %.1f kg" % _daq_mass if _daq_mass
               else "Data-acquisition logger (placeholder — declare mass in INTEGRATION)")
    mesh(v, i, j, k, COLORS["logger"], "Data logger", "data-acquisition", 0.85, _daq_hv)

    # ---- 8) CG marker from mass roll-up -------------------------------- #
    cg_h = float(getattr(vp, "cg_height", 0.0) or 0.0)
    wdist = float(getattr(vp, "weight_dist_front", 0.5) or 0.5)
    cg_x = x_rear + wdist * (x_front - x_rear)
    cg_y = 0.0
    cg_label = "CG (params)"
    if ledger is not None:
        try:
            roll = ledger.mass_rollup()
            if roll.get("cg_mm"):
                gx, gy, gz = roll["cg_mm"]
                cg_x, cg_y, cg_h = x_front - gx, gy, gz
                cg_label = "CG (declared %.0f kg)" % roll["total_kg"]
        except Exception:
            pass
    # Fold in any user-dropped custom parts that carry a mass, so a heavy
    # imported CAD (accumulator, motor…) visibly shifts the CG marker. We treat
    # the params/declared CG as the baseline car mass acting at (cg_x,cy,cg_h)
    # and add each part's mass at its own centre, then recompute the weighted
    # mean. Parts with no mass_kg don't move the CG (packaging-only bodies).
    _cp_masses = []
    for _cpm in (custom_parts or []):
        try:
            _m = float(_cpm.get("mass_kg", 0.0) or 0.0)
        except Exception:
            _m = 0.0
        if _m > 0:
            _cp_masses.append((_m, float(_cpm.get("x_mm", 0) or 0),
                               float(_cpm.get("y_mm", 0) or 0),
                               float(_cpm.get("z_mm", 0) or 0)))
    if _cp_masses and cg_h > 0:
        _base_kg = 0.0
        if ledger is not None:
            try:
                _base_kg = float(ledger.mass_rollup().get("total_kg", 0.0) or 0.0)
            except Exception:
                _base_kg = 0.0
        if _base_kg <= 0:
            _base_kg = float(getattr(vp, "mass", 0.0) or 0.0)
        if _base_kg <= 0:
            _base_kg = 220.0  # nominal FSAE car mass so the blend is sensible
        _tot = _base_kg
        _sx = cg_x * _base_kg
        _sy = cg_y * _base_kg
        _sz = cg_h * _base_kg
        for _m, _px, _py, _pz in _cp_masses:
            _tot += _m
            _sx += _px * _m
            _sy += _py * _m
            _sz += _pz * _m
        if _tot > 0:
            cg_x, cg_y, cg_h = _sx / _tot, _sy / _tot, _sz / _tot
            cg_label = "CG (+%.1f kg parts)" % sum(m for m, *_ in _cp_masses)

    if show_cg and cg_h > 0:
        # When a subsystem is spotlit, fade the CG marker too — it's a global
        # readout, not part of any subteam, so it shouldn't out-shine the
        # highlighted parts. Full strength only when nothing is highlighted.
        _cg_op = 1.0 if _hl_set is None else 0.18
        fig.add_trace(go.Scatter3d(
            x=[cg_x], y=[cg_y], z=[cg_h], mode="markers+text",
            marker=dict(size=8, color=COLORS["cg"], symbol="diamond"),
            text=[cg_label], textposition="top center",
            textfont=dict(color=COLORS["cg"], size=11),
            opacity=_cg_op,
            name="Centre of gravity", hoverinfo="text"))

    # ---- ground plane -------------------------------------------------- #
    if show_floor:
        pad = max(tf, tr) * 0.8
        xs2 = [x_rear - tire_r * 2.0 - pad, x_front + tire_r * 2.2 + pad]
        ys2 = [-max(tf, tr) / 2 - pad, max(tf, tr) / 2 + pad]
        gx, gy = np.meshgrid(xs2, ys2)
        fig.add_trace(go.Surface(
            x=gx, y=gy, z=np.zeros_like(gx) - ride_drop, showscale=False,
            opacity=0.22, colorscale=[[0, COLORS["floor"]], [1, COLORS["floor"]]],
            hoverinfo="skip", name="Ground", showlegend=False))

    # ---- 9) custom parts: user-dropped bodies in real millimetres ------- #
    # A sub-team can drop any part onto the car straight off a spec sheet: real
    # L×W×H in mm at a real centre, no scale factors. Each becomes a first-class
    # body through the same `mesh` chokepoint, so it inherits highlight dimming,
    # part_overrides and — via _accrue under its subsystem — click-to-zoom.
    # A part flagged `provisional` is a stand-in for a part whose CAD hasn't
    # arrived yet: it draws faint and hatched-amber so nobody mistakes a guess
    # for a confirmed body, but still lets dependent packaging work continue.
    _cyl_default = None
    for cp in (custom_parts or []):
        try:
            nm = str(cp.get("name") or "Custom part").strip() or "Custom part"
            sub = cp.get("subsys") or None
            if sub == "(custom / unassigned)":
                sub = None
            l = float(cp.get("l_mm", 0) or 0)
            w = float(cp.get("w_mm", 0) or 0)
            h = float(cp.get("h_mm", 0) or 0)
            cx = float(cp.get("x_mm", 0) or 0)
            cy = float(cp.get("y_mm", 0) or 0)
            cz = float(cp.get("z_mm", 0) or 0)
            _has_mesh_early = bool(cp.get("mesh") and cp["mesh"].get("verts"))
            if not _has_mesh_early and (
                    l <= 0 or w <= 0 or (h <= 0 and cp.get("shape", "box") == "box")):
                continue
            prov = bool(cp.get("provisional"))
            mesh_payload = cp.get("mesh")
            has_mesh = bool(mesh_payload and mesh_payload.get("verts")
                            and mesh_payload.get("faces"))
            if prov:
                # A waiting-on-CAD stand-in: amber, see-through, clearly a guess.
                col = cp.get("color") or COLORS["cg"]
                base_op = 0.30
                nm_draw = nm if nm.endswith("(awaiting CAD)") else nm + " (awaiting CAD)"
                hov = "%s — PROVISIONAL stand-in, %.0f×%.0f×%.0f mm @ (x %.0f, y %.0f, z %.0f)" % (
                    nm, l, w, h, cx, cy, cz)
            else:
                # Imported CAD meshes always render neon blue so the real
                # geometry stays visible on the dark scene (and any part saved
                # earlier with a dark hue is corrected here too). Non-mesh custom
                # boxes/cylinders keep their chosen/subsystem colour.
                if has_mesh:
                    col = COLORS["cad"]
                else:
                    col = cp.get("color") or COLORS.get(sub_color_key(sub),
                                                         COLORS["custom"])
                base_op = 0.95 if has_mesh else 0.82
                nm_draw = nm
                _kind = "CAD mesh" if has_mesh else "%.0f×%.0f×%.0f mm" % (l, w, h)
                hov = "%s — %s @ (x %.0f, y %.0f, z %.0f)" % (nm, _kind, cx, cy, cz)
            shape = cp.get("shape", "box")
            # A `define_car` chassis is scaled + centred to fit the real car
            # (computed above), so it reads as one whole car with the wheels.
            _msc = float(cp.get("mesh_scale", 1.0) or 1.0)
            _mcx, _mcy, _mcz = cx, cy, cz
            if cp.get("define_car") and has_mesh and _def_target is not None:
                _msc = float(_def_target["scale"])
                _mcx, _mcy, _mcz = _def_target["centre"]
            # User nudge offsets apply ON TOP of any auto placement — including
            # the define_car auto-centre above, which otherwise ignores x/y/z —
            # so an imported chassis can still be slid into alignment by hand.
            _mcx += float(cp.get("dx_mm", 0.0) or 0.0)
            _mcy += float(cp.get("dy_mm", 0.0) or 0.0)
            _mcz += float(cp.get("dz_mm", 0.0) or 0.0)
            if has_mesh:
                # Draw the ACTUAL imported geometry, oriented + placed on the car.
                faces = np.asarray(mesh_payload["faces"], int)
                V = _orient_part_mesh(
                    mesh_payload["verts"],
                    axis_map=cp.get("axis_map", "z_up"),
                    yaw_deg=float(cp.get("yaw_deg", 0.0) or 0.0),
                    roll_deg=float(cp.get("roll_deg", 0.0) or 0.0),
                    pitch_deg=float(cp.get("pitch_deg", 0.0) or 0.0),
                    scale=_msc,
                    centre=(_mcx, _mcy, _mcz))
                mesh(V, faces[:, 0], faces[:, 1], faces[:, 2],
                     col, nm_draw, sub, base_op, hov)
            elif shape == "cylinder":
                v, ii, jj, kk = _cylinder((cx, cy, cz), (1, 0, 0),
                                          radius=w / 2.0, length=l)
                mesh(v, ii, jj, kk, col, nm_draw, sub, base_op, hov)
            else:
                v, ii, jj, kk = _box(cx, cy, cz, l, w, h)
                mesh(v, ii, jj, kk, col, nm_draw, sub, base_op, hov)
            # On a box stand-in, also draw its wireframe edges so its true extent
            # is legible through the transparency — the packaging team is reading
            # a box, and a faint mesh alone is hard to judge.
            if prov and not has_mesh and shape != "cylinder":
                E = [(0,1),(1,2),(2,3),(3,0),(4,5),(5,6),(6,7),(7,4),
                     (0,4),(1,5),(2,6),(3,7)]
                for a, b in E:
                    seg(v[a], v[b], col, w=3, subsys=sub)
        except Exception:
            # A malformed custom part must never take down the whole car view.
            continue

    # ---- camera: zoom to the focused subsystem, if one is clicked ------- #
    # When focus_subsystem is set we re-aim the camera at that part's bounding
    # box centre and pull the eye in proportionally, so clicking a part reads as
    # an automatic zoom. With no focus we keep the standard wide establishing
    # shot of the whole car.
    # uirevision token: constant while the focus is unchanged (so the user's
    # rotation is preserved across reruns), and distinct per focused part (so a
    # new click is allowed to re-aim the camera). "wide" is the no-focus shot.
    camera_revision = "wide"
    scene_camera = dict(eye=dict(x=1.8, y=-1.7, z=1.05))
    # Resolve the focus target. A specific PART takes precedence over its
    # subsystem, so picking "Front wing" frames just that wing while picking the
    # whole "aerodynamics" subsystem frames every aero body. Fall back cleanly:
    # if the named part wasn't drawn (layer hidden, topology without it) we drop
    # to the subsystem box, and if that's empty too we keep the wide shot.
    _focus_pts = None
    _focus_token = None
    if focus_part and part_pts.get(focus_part):
        _focus_pts = part_pts[focus_part]
        _focus_token = "part:%s" % focus_part
    elif focus_subsystem and subsys_pts.get(focus_subsystem):
        _focus_pts = subsys_pts[focus_subsystem]
        _focus_token = "focus:%s" % focus_subsystem
    if _focus_pts:
        camera_revision = _focus_token
        pts = np.asarray(_focus_pts, float)
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        ctr = (lo + hi) / 2.0

        # Aspect mode is "data", so camera coordinates are normalised against the
        # full scene span on each axis. Express the focus centre in that space and
        # bring the eye close along the standard viewing direction.
        all_pts = np.asarray([p for b in subsys_pts.values() for p in b], float)
        smin, smax = all_pts.min(axis=0), all_pts.max(axis=0)
        span = np.where((smax - smin) == 0, 1.0, (smax - smin))
        c_norm = (ctr - (smin + smax) / 2.0) / span  # centred, normalised

        # how big the part is relative to the whole car -> how hard we zoom.
        # A pickup point has zero span, so floor the fraction a bit higher than
        # for a body and lift the base distance, giving single points a readable
        # standoff instead of jamming the camera onto the vertex.
        part_span = (hi - lo)
        _is_point = bool(np.max(part_span) < 1e-6)
        frac = float(np.clip(np.max(part_span / span), 0.08 if _is_point else 0.04, 0.9))
        dist = (0.85 if _is_point else 0.55) + frac * 1.4

        dir_unit = np.array([1.0, -0.95, 0.6])
        dir_unit = dir_unit / np.linalg.norm(dir_unit)
        eye = c_norm + dir_unit * dist
        scene_camera = dict(
            center=dict(x=float(c_norm[0]), y=float(c_norm[1]), z=float(c_norm[2])),
            eye=dict(x=float(eye[0]), y=float(eye[1]), z=float(eye[2])))

    # Global scene extents (mm) over every accrued vertex — feeds the units-
    # aware axis tick generator so ticks land on round mm OR round inches.
    _ext_lo = np.array([np.nan] * 3)
    _ext_hi = np.array([np.nan] * 3)
    try:
        _all = [np.asarray(_p, float).reshape(-1, 3)
                for _p in subsys_pts.values() if _p]
        if _all:
            _all = np.vstack(_all)
            _ext_lo, _ext_hi = _all.min(axis=0), _all.max(axis=0)
    except Exception:
        pass

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        # Make rotating the car the primary mouse gesture: left-drag orbits the
        # scene (turntable keeps "up" sensible), scroll zooms, right-drag pans.
        dragmode="turntable",
        # Preserve the user's manual orbit/zoom across Streamlit reruns. Plotly
        # keeps the current camera as long as uirevision is unchanged; we only
        # bump it (via camera_revision) when we deliberately re-aim the camera
        # on a focus change, so a click-to-zoom still moves but ordinary reruns
        # (and the user's own rotation) don't snap the view back.
        uirevision=camera_revision,
        scene=dict(
            # Axis labels + tick text follow the active unit system (metric /
            # US) via _units_axis_cfg; geometry and tick POSITIONS stay in mm.
            xaxis=_units_axis_cfg("x (rear ←→ front)", _ext_lo[0], _ext_hi[0]),
            yaxis=_units_axis_cfg("y (right)", _ext_lo[1], _ext_hi[1]),
            zaxis=_units_axis_cfg("z (up)", _ext_lo[2], _ext_hi[2]),
            aspectmode="data", camera=scene_camera,
            dragmode="turntable"),
        font=dict(family="JetBrains Mono", color="#cdd6df", size=10),
        height=height, margin=dict(l=0, r=0, t=10, b=0),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(size=10), itemsizing="constant"))

    # Expose each subsystem's centroid (mean of all its accrued vertices) so a
    # caller can float a label on it — e.g. the role picker tags every lit
    # subteam by name at the centre of the parts it owns.
    _centroids = {}
    for _s, _pts in subsys_pts.items():
        if _pts:
            _arr = np.asarray(_pts, float).reshape(-1, 3)
            _centroids[_s] = _arr.mean(axis=0).tolist()
    # Attach as a plain Python attribute, bypassing plotly's Figure.__setattr__
    # guard via object.__setattr__. This avoids stuffing a nested dict into
    # layout.meta, which older plotly (5.x) validates strictly and can reject —
    # the previous cause of the picker silently failing to build. The attribute
    # is read in-process by the role picker and never serialised.
    try:
        object.__setattr__(fig, "_kk_subsys_centroids", _centroids)
    except Exception:
        pass
    # Expose the real drawn boxes (centre+size in car mm) so the CAD "fit to
    # area" tool can size a replacement to the ACTUAL placeholder box a part
    # occupies, not just the rough anchor. Same object.__setattr__ approach as
    # the centroids above, to bypass plotly's Figure.__setattr__ guard.
    try:
        object.__setattr__(fig, "_part_boxes", {
            nm: dict(
                centre=[float((lo[i] + hi[i]) / 2.0) for i in range(3)],
                size=[float(hi[i] - lo[i]) for i in range(3)])
            for nm, (lo, hi) in _part_boxes.items()})
    except Exception:
        try:
            object.__setattr__(fig, "_part_boxes", {})
        except Exception:
            pass
    # Per-INSTANCE boxes: cluster each name's per-call boxes by overlap (10 mm
    # slack), so a body drawn once per corner/side yields one box PER COPY —
    # "Tire" gives four wheel-sized boxes instead of one ring spanning the car.
    # This is what makes mesh-level fit / clearance forecasting honest: the
    # merged _part_boxes above stays for envelope fitting (its documented job),
    # instance boxes are for anything that reasons about a single body.
    def _cluster_instances(pieces, tol=10.0):
        n = len(pieces)
        parent = list(range(n))

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        for a in range(n):
            la, ha = pieces[a]
            for b in range(a + 1, n):
                lb, hb = pieces[b]
                if ((la - tol <= hb) & (lb - tol <= ha)).all():
                    ra, rb = find(a), find(b)
                    if ra != rb:
                        parent[rb] = ra
        groups: dict = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(i)
        out = []
        for idxs in groups.values():
            lo = np.min([pieces[i][0] for i in idxs], axis=0)
            hi = np.max([pieces[i][1] for i in idxs], axis=0)
            out.append(dict(
                centre=[float((lo[k] + hi[k]) / 2.0) for k in range(3)],
                size=[float(hi[k] - lo[k]) for k in range(3)]))
        return out

    try:
        object.__setattr__(fig, "_part_instance_boxes", {
            nm: _cluster_instances(pieces)
            for nm, pieces in _part_pieces.items() if pieces})
    except Exception:
        try:
            object.__setattr__(fig, "_part_instance_boxes", {})
        except Exception:
            pass
    return fig


def subsys_centroids(fig) -> dict:
    """Return the per-subsystem centroid dict attached by build_full_car_figure
    (mapping subsystem id -> [x, y, z]), or an empty dict if none is present."""
    return getattr(fig, "_kk_subsys_centroids", {}) or {}


# --------------------------------------------------------------------------- #
#  Part discovery — which clickable parts a built figure actually contains
# --------------------------------------------------------------------------- #
# A label nice enough to show on a button, for the internal part keys the
# renderer tags bodies with. Wishbone members are keyed by their group token;
# mesh bodies by their display name (already nice). Anything not listed falls
# back to the key with its first letter capitalised, so even an unforeseen
# topology member gets a readable button instead of a raw token.
_PART_LABELS = {
    "upper": "Upper wishbone", "lower": "Lower wishbone", "upright": "Upright",
    "tie": "Tie rod", "push": "Pushrod", "rocker": "Rocker",
    "spring": "Spring / damper", "wheel": "Wheel hub",
    "strut": "Strut", "trailing": "Trailing arm", "panhard": "Panhard rod",
    "track": "Track rod", "link": "Link", "radius": "Radius rod",
    "Brake disc": "Brake disc (×4)", "Sidepod (cooling)": "Sidepod",
}

# Corner ids in a stable, readable order, with friendly labels.
_CORNER_ORDER = ["FL", "FR", "RL", "RR"]
_CORNER_LABELS = {"FL": "Front-left", "FR": "Front-right",
                  "RL": "Rear-left", "RR": "Rear-right"}


def part_label(key: str) -> str:
    """Human-friendly button label for an internal part-focus key.

    A per-corner key ("Tire#FR") is shown as the base part's label with the
    corner appended ("Tire (×4) · Front-right"), so even if such a key surfaces
    it reads clearly.
    """
    base = str(key)
    corner_suffix = ""
    if "#" in base:
        base, cnr = base.split("#", 1)
        corner_suffix = " · " + _CORNER_LABELS.get(cnr, cnr)
    if base in _PART_LABELS:
        return _PART_LABELS[base] + corner_suffix
    # mesh bodies are already display names; group tokens are short lowercase.
    nice = base if (base[:1].isupper() or " " in base) else base.capitalize()
    return nice + corner_suffix


# Secondary/helper bodies that belong to a bigger part and shouldn't get their
# own button (they're still clickable on the model, just not listed separately):
# wing mounts, the motor's drive line, the driver's helmet.
_PART_HIDE = {"fw_mount", "rw_mount", "drive", "helmet",
              "Wheel centre", "Contact patch"}

# Inner suspension pickup points. These are real, individually focusable targets
# but they're finer-grained than the structural members, so the UI lists them in
# a separate, secondary "pickup points" group rather than mixed in with the arms.
_PICKUP_NAMES = {
    "Upper front pickup", "Upper rear pickup", "Lower front pickup",
    "Lower rear pickup", "Upper ball joint", "Lower ball joint",
    "Tie rod inner", "Tie rod outer", "Strut top", "Strut lower",
}


def is_pickup(part_key: str) -> bool:
    """True if a part key names an inner suspension pickup point."""
    base = str(part_key).split("#", 1)[0]
    return base in _PICKUP_NAMES


def available_parts(fig, include_pickups: bool = False) -> dict:
    """Map subsystem -> ordered list of (label, part_key) actually drawn.

    Reads the customdata [subsystem, part_key, corner] that every clickable body
    carries, so the part list always reflects THIS car (its topology, its visible
    layers). Order of first appearance is preserved so buttons read top-down
    sensibly. Helper sub-bodies in _PART_HIDE are skipped so the button row stays
    clean. Per-corner focus keys (those containing '#') are NOT listed here — the
    shared part key represents all corners, and part_corners() enumerates the
    individual corners for an optional drill-down.

    Inner pickup points are excluded by default (they're returned by
    pickup_parts() for a separate, secondary list); pass include_pickups=True to
    fold them in.
    """
    out: dict[str, list] = {}
    seen: dict[str, set] = {}
    for tr in fig.data:
        cd = getattr(tr, "customdata", None)
        if not cd:
            continue
        first = cd[0]
        if not (isinstance(first, (list, tuple)) and len(first) >= 2):
            continue
        sub, key = first[0], first[1]
        if not sub or not key or key in _PART_HIDE or "#" in str(key):
            continue
        if not include_pickups and is_pickup(key):
            continue
        bucket_seen = seen.setdefault(sub, set())
        if key in bucket_seen:
            continue
        bucket_seen.add(key)
        out.setdefault(sub, []).append((part_label(key), key))
    return out


def pickup_parts(fig) -> list:
    """Ordered list of (label, part_key) for the inner suspension pickup points
    actually present in this figure. Empty for topologies that don't expose
    named hardpoints. These are the finer drill targets shown as a secondary row.
    """
    out: list = []
    seen: set = set()
    for tr in fig.data:
        cd = getattr(tr, "customdata", None)
        if not cd:
            continue
        first = cd[0]
        if not (isinstance(first, (list, tuple)) and len(first) >= 2):
            continue
        key = first[1]
        if not key or "#" in str(key) or not is_pickup(key):
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append((part_label(key), key))
    return out


def part_corners(fig, part_key: str) -> list:
    """Corners a given part exists at, as ordered (corner_id, focus_key) pairs.

    A part drawn at all four wheels (tire, brake disc, a wishbone) reports
    [("FL","<part>#FL"), ("FR","<part>#FR"), …]; a one-off body (monocoque, a
    wing) reports []. The focus_key is what you hand build_full_car_figure as
    focus_part to frame that single corner.
    """
    present = set()
    pref = str(part_key) + "#"
    for tr in fig.data:
        cd = getattr(tr, "customdata", None)
        if not cd:
            continue
        first = cd[0]
        if not (isinstance(first, (list, tuple)) and len(first) >= 3):
            continue
        key, cnr = first[1], first[2]
        if key == part_key and cnr:
            present.add(cnr)
    return [(c, pref + c) for c in _CORNER_ORDER if c in present]


def corner_label(corner_id: str) -> str:
    """Friendly label for a corner id (FL/FR/RL/RR)."""
    return _CORNER_LABELS.get(corner_id, corner_id)


# --------------------------------------------------------------------------- #
#  Live influence summary
# --------------------------------------------------------------------------- #
def influence_summary(vp, ledger, topology_label: str | None = None) -> list:
    rows = []
    def add(sys, status, detail):
        rows.append(dict(subsystem=sys, status=status, detail=detail))

    aero = _iface(ledger, "aerodynamics")
    df = _g(aero, "downforce_n_at_v")
    add("aerodynamics", "sized" if df else "default",
        ("%.0f N @ %.0f m/s → wing span/chord" % (df[0], df[1]))
        if isinstance(df, (tuple, list)) and df else "no downforce → nominal wings")

    pt = _iface(ledger, "powertrain")
    pkw = _g(pt, "peak_power_kw")
    add("powertrain", "sized" if (pkw or _g(pt, "env_x_mm")) else "default",
        ("%.0f kW → motor + inverter size" % pkw) if pkw else "no power/envelope → nominal motor")

    cool = _iface(ledger, "cooling")
    af = _g(cool, "cooling_airflow_cms")
    add("cooling", "sized" if af else "default",
        ("%.2f m³/s → sidepod size" % af) if af else "no airflow → nominal sidepods")

    el = _iface(ledger, "electrics")
    em, ee = _g(el, "mass_kg"), _g(el, "env_x_mm")
    add("electrics", "shown" if (em or ee) else "hidden",
        "declared envelope → battery box" if ee else
        (("%.1f kg → battery box size" % em) if em else "no mass/envelope → not drawn"))

    br = _iface(ledger, "brakes")
    bt = _g(br, "brake_torque_nm")
    add("brakes", "sized" if bt else "default",
        ("%.0f N·m → brake-disc diameter" % bt) if bt else "no torque → nominal discs")

    _arch = (topology_label + " · ") if topology_label else ""
    add("suspension", "live",
        "%strack F/R %.0f/%.0f mm · wheelbase %.0f mm · spring %.0f N/mm" % (
            _arch, getattr(vp, "track_front", 0), getattr(vp, "track_rear", 0),
            getattr(vp, "wheelbase", 0), getattr(vp, "spring_rate_front", 0)))

    ch = _iface(ledger, "chassis")
    cm = _g(ch, "mass_kg")
    add("chassis", "live", ("%.1f kg monocoque" % cm) if cm else "monocoque (no mass declared)")

    if ledger is not None:
        try:
            roll = ledger.mass_rollup()
            add("ALL", "rollup",
                "declared %.1f kg vs target %.0f kg (Δ %+.1f kg)" % (
                    roll["total_kg"], roll["target_kg"], roll["delta_kg"])
                + ("; CG live" if roll.get("cg_mm") else "; CG needs all masses+positions"))
        except Exception:
            pass
    return rows


# Backward/forward-compatible alias. Some callers (older/newer streamlit_app
# builds) reference ``override_influence_summary``; it is identical to
# ``influence_summary`` — the influence read-out already reflects any active
# part overrides via the ledger it is handed.
override_influence_summary = influence_summary


def custom_part_fit(vp, part: dict) -> dict:
    """Plain-language fit check of a user-dropped part against the car envelope.

    Not a collision solver — that lives in the INTEGRATION tab against declared
    volumes. This is the quick "does my radiator even fit between the wheels and
    inside the floor-to-hoop height" read a sub-team wants the instant they drop
    a part on the car, expressed in real mm of clearance (negative = pokes out).

    Returns dict(status in {ok, tight, over}, messages:list[str], clearances:dict).
    """
    wb = float(getattr(vp, "wheelbase", 1550.0))
    tf = float(getattr(vp, "track_front", 1200.0))
    tr = float(getattr(vp, "track_rear", 1180.0))
    track = min(tf, tr)
    # A representative usable height: ground up to roughly the main-hoop top.
    z_ceiling = 1150.0

    l = float(part.get("l_mm", 0) or 0)
    w = float(part.get("w_mm", 0) or 0)
    h = float(part.get("h_mm", 0) or 0)
    cx = float(part.get("x_mm", 0) or 0)
    cy = float(part.get("y_mm", 0) or 0)
    cz = float(part.get("z_mm", 0) or 0)

    # Car spans x in [-wb/2, +wb/2] (mid-wheelbase origin), y in [-track/2, track/2].
    x_lo, x_hi = cx - l / 2, cx + l / 2
    y_lo, y_hi = cy - w / 2, cy + w / 2
    z_lo, z_hi = cz - h / 2, cz + h / 2

    clr = {
        "front of front axle": (wb / 2) - x_hi,
        "behind rear axle": x_lo - (-wb / 2),
        "right of track": (track / 2) - y_hi,
        "left of track": y_lo - (-track / 2),
        "above hoop height": z_ceiling - z_hi,
        "below ground": z_lo - 0.0,
    }
    msgs, status = [], "ok"
    for where, mm in clr.items():
        if mm < 0:
            status = "over"
            msgs.append("pokes out %s by %.0f mm" % (where, -mm))
        elif mm < 25:
            if status != "over":
                status = "tight"
            msgs.append("only %.0f mm clear %s" % (mm, where))
    if not msgs:
        msgs.append("sits inside the wheelbase, track and hoop-height envelope")
    return dict(status=status, messages=msgs, clearances=clr)


def part_dims_from_mesh(summary: dict) -> dict:
    """Pull a part's real L×W×H (mm) out of a loaded-CAD mesh summary.

    `summary` is what chassis.mesh_summary() returns (bbox_min/bbox_max/size_mm).
    The mesh sits in whatever frame it was exported in; we only take its overall
    bounding-box extents, mapped to the car's L(x)/W(y)/H(z) so a stand-in can be
    replaced by the part's true size the instant the CAD lands. Returns the size
    and the bbox so the caller can also recover a sensible default centre.
    """
    sz = summary.get("size_mm") or [0.0, 0.0, 0.0]
    lo = summary.get("bbox_min") or [0.0, 0.0, 0.0]
    hi = summary.get("bbox_max") or [0.0, 0.0, 0.0]
    l, w, h = (abs(float(sz[0])), abs(float(sz[1])), abs(float(sz[2])))
    ctr = [(float(lo[i]) + float(hi[i])) / 2.0 for i in range(3)]
    return dict(l_mm=l, w_mm=w, h_mm=h, centre_mm=ctr, size_mm=[l, w, h])


def reconcile_part(guess: dict, real: dict, tol_mm: float = 8.0,
                   tol_frac: float = 0.05) -> dict:
    """Compare a stand-in guess against the part that finally arrived.

    This is the catch for the exact handoff failure the team keeps hitting — the
    CAD that "is a mirror of the one I originally got", or that turns out a
    different size than everyone packaged around. We diff the three extents and
    also test for a swapped/mirrored aspect (the part's dimensions present but in
    a different order), and return a plain-language verdict so the dependent team
    learns BEFORE build that their guess was off.

    status: "match"      guess was right within tolerance
            "resize"     same part, different size — repackage around real dims
            "mirrored"   extents look transposed/swapped — likely a mirror/wrong
                         orientation handoff; check handedness before cutting
            "new"        nothing was packaged here yet (no guess to compare)
    """
    def trip(d):
        return [abs(float(d.get(k, 0) or 0)) for k in ("l_mm", "w_mm", "h_mm")]

    g, r = trip(guess or {}), trip(real or {})
    if max(g) <= 0:
        return dict(status="new", deltas=[r[0], r[1], r[2]],
                    messages=["No stand-in was here — placing the real part."])

    deltas = [r[i] - g[i] for i in range(3)]

    def within(a, b):
        return abs(a - b) <= max(tol_mm, tol_frac * max(a, b, 1.0))

    axiswise_ok = all(within(g[i], r[i]) for i in range(3))
    if axiswise_ok:
        return dict(status="match", deltas=deltas,
                    messages=["Real part matches your stand-in within tolerance "
                              "— your packaging holds."])

    # Same multiset of extents but assigned to different axes -> mirror/swap.
    if sorted(round(x, 1) for x in g) == sorted(round(x, 1) for x in r) and \
            not axiswise_ok:
        return dict(status="mirrored", deltas=deltas,
                    messages=["Same dimensions, different axes — this looks "
                              "mirrored or rotated vs your stand-in. Confirm "
                              "handedness/orientation before committing."])
    if sorted(within(gv, rv) for gv, rv in zip(sorted(g), sorted(r))) and \
            all(within(gv, rv) for gv, rv in zip(sorted(g), sorted(r))):
        return dict(status="mirrored", deltas=deltas,
                    messages=["Extents match but on different axes — likely a "
                              "mirror/orientation swap. Check before cutting."])

    msgs = []
    for ax, dv in zip(("L", "W", "H"), deltas):
        if abs(dv) > max(tol_mm, tol_frac * max(g[("L", "W", "H").index(ax)], 1.0)):
            msgs.append("%s %+.0f mm" % (ax, dv))
    return dict(status="resize", deltas=deltas,
                messages=["Real part differs from your stand-in: "
                          + ", ".join(msgs) + ". Repackage around the real size."])


def suggest_part_geometry(vp, subsys: str, ledger=None) -> dict:
    """Propose a TARGET size (x/y/z mm) + position for a part nobody has sized yet.

    The deeper version of the missing-part stall: a team is blocked not because a
    CAD is late but because they have *no idea* what the part should be, so they
    can't even guess. This gives them a number to design toward — dimensions the
    car can actually accommodate, using the same FSAE-typical proportions the
    full-car renderer already sizes each subsystem body with, scaled to THIS
    car's wheelbase / track / tyre size. It is an envelope to strive for, not a
    spec: "build it to roughly this and it will package."

    Any dimension the subsystem has already declared in the ledger (env_x/y/z) is
    honoured and passed straight back, so a partial declaration is completed
    rather than overwritten. Returns:

        l_mm, w_mm, h_mm     suggested extents (x, y, z)
        x_mm, y_mm, z_mm     a centre where that subsystem usually lives
        shape                "box" or "cylinder"
        basis                plain-language reason for each axis (what constrains it)
        from_declared        list of axes that came from the team's own declaration
    """
    wb = float(getattr(vp, "wheelbase", 1550.0))
    tf = float(getattr(vp, "track_front", 1200.0))
    tr = float(getattr(vp, "track_rear", 1180.0))
    # Approximate tyre radius and usable interior half-width the renderer uses.
    tire_r = 228.0
    interior_w = max(120.0, min(tf, tr) / 2.0 - 180.0 - 40.0) * 2.0  # full width
    x_front, x_rear = wb / 2.0, -wb / 2.0

    it = _iface(ledger, subsys) if ledger is not None else None
    dec = (_g(it, "env_x_mm"), _g(it, "env_y_mm"), _g(it, "env_z_mm"))

    # Per-subsystem TYPICAL envelope + home position, mirroring the renderer.
    S = subsys
    shape = "box"
    if S == "powertrain":
        l, w, h = wb * 0.16, min(interior_w, 240.0), tire_r * 0.9
        x, y, z = x_rear + tire_r * 1.05, 0.0, tire_r * 0.8
        shape = "cylinder"
        basis = ["L from wheelbase (rear motor bay)",
                 "W ≤ interior track width",
                 "H ~ tyre radius (sits low)"]
    elif S == "electrics":
        l, w, h = wb * 0.22, min(interior_w * 0.95, 340.0), tire_r * 1.1
        x, y, z = -wb * 0.02, 0.0, tire_r * 0.7
        basis = ["L from wheelbase (accumulator bay)",
                 "W ≤ interior track width",
                 "H to clear floor and stay under hoop"]
    elif S == "cooling":
        # A sidepod-mounted radiator: long in x, thin in y, moderate in z.
        l, w, h = wb * 0.30, 130.0, tire_r * 0.95
        x, y, z = -wb * 0.05, min(tf, tr) / 2.0 - 150.0, tire_r * 0.7
        basis = ["L from sidepod length",
                 "W ~ radiator core thickness",
                 "H to fit the duct"]
    elif S == "aerodynamics":
        # A wing assembly: full track wide, short chord, thin.
        l, w, h = tire_r * 0.65, tf * 0.98, 80.0
        x, y, z = x_front + tire_r * 1.6, 0.0, tire_r * 0.32
        basis = ["L = wing chord", "W ~ front track", "H = element stack"]
    elif S == "brakes":
        # A single disc + caliper package near a corner.
        l, w, h = 60.0, 280.0, 280.0
        x, y, z = x_front, tf / 2.0 - 60.0, tire_r * 0.9
        shape = "cylinder"
        basis = ["L = disc + caliper thickness",
                 "W/H = disc diameter (corner package)",
                 "at the wheel"]
    elif S == "data-acquisition":
        l, w, h = 160.0, 120.0, 80.0
        x, y, z = -wb * 0.16, 0.0, tire_r * 1.1
        basis = ["compact logger box", "fits beside the driver", "above the floor"]
    elif S == "chassis":
        l, w, h = wb * 0.5, min(interior_w * 1.1, 320.0), tire_r * 1.6
        x, y, z = -wb * 0.08, 0.0, tire_r * 0.9
        basis = ["L = central tub length", "W = interior width", "H = tub depth"]
    elif S == "suspension":
        l, w, h = 300.0, 200.0, 300.0
        x, y, z = x_front, tf / 2.0 - 120.0, tire_r
        basis = ["upright/linkage package", "inboard of the wheel", "corner height"]
    else:
        l, w, h = 200.0, 150.0, 120.0
        x, y, z = 0.0, 0.0, tire_r
        basis = ["generic packaging box", "centred", "mid-height"]

    # Honour anything the team already declared: complete, don't overwrite.
    from_declared = []
    out_l, out_w, out_h = float(l), float(w), float(h)
    if dec[0]:
        out_l = float(dec[0]); from_declared.append("L")
    if dec[1]:
        out_w = float(dec[1]); from_declared.append("W")
    if dec[2]:
        out_h = float(dec[2]); from_declared.append("H")

    return dict(l_mm=round(out_l, 0), w_mm=round(out_w, 0), h_mm=round(out_h, 0),
                x_mm=round(float(x), 0), y_mm=round(float(y), 0),
                z_mm=round(float(z), 0), shape=shape, basis=basis,
                from_declared=from_declared)


# Every body the car draws is individually replaceable by a CAD / sketch /
# estimate. This catalog maps each legend part to: the draw-name the renderer
# uses (so we can suppress exactly that body), its subsystem (colour + zoom),
# and an envelope+home so a dropped part auto-fits where THAT part belongs.
# `key` is a stable id used in session-state and suppression sets.
# SIMPLIFIED to seven subsystems. Each is replaceable as ONE unit by a CAD /
# sketch / estimate. Replacing a subsystem hides ALL of its procedural bodies
# (listed in `drawnames`) so only the real geometry shows there, while every
# OTHER subsystem stays on screen as a dummy suggestion — wheels, wings, driver,
# CG, etc. — so the user always sees their part in the context of a full car.
SUBSYSTEM_CATALOG = [
    # key            display name        draw-names this subsystem owns
    ("chassis",      "Chassis",          ["Spaceframe", "Roll hoop", "Driver"]),
    ("aerodynamics", "Aerodynamics",     ["Front wing", "Rear wing"]),
    ("cooling",      "Cooling",          ["Sidepod (cooling)", "Radiator core"]),
    ("powertrain",   "Powertrain",       ["Motor + inverter"]),
    ("electrics",    "Electrics",        ["Accumulator"]),
    ("suspension",   "Suspension",       ["Tire", "Upright", "Wheel hub",
                                          "Upper wishbone", "Lower wishbone",
                                          "Tie rod", "Pushrod", "Rocker",
                                          "Spring/damper"]),
    ("brakes",       "Brakes",           ["Brake disc"]),
]
SUBSYS_DRAWNAMES = {k: dn for k, _d, dn in SUBSYSTEM_CATALOG}
SUBSYS_DISPLAY = {k: d for k, d, _dn in SUBSYSTEM_CATALOG}

# Kept for the renderer's internal anchor/box lookups (per representative body).
PART_CATALOG = [
    ("front_wing",      "Front wing",          "aerodynamics", False),
    ("rear_wing",       "Rear wing",           "aerodynamics", False),
    ("monocoque",       "Spaceframe",          "chassis",      False),
    ("roll_hoop",       "Roll hoop",           "chassis",      False),
    ("driver",          "Driver",              "chassis",      False),
    ("sidepod",         "Sidepod (cooling)",   "cooling",      False),
    ("radiator",        "Radiator core",       "cooling",      False),
    ("motor",           "Motor + inverter",    "powertrain",   False),
    ("accumulator",     "Accumulator",         "electrics",    False),
    ("tire",            "Tire",                "suspension",   True),
    ("brake_disc",      "Brake disc",          "brakes",       True),
    ("upright",         "Upright",             "suspension",   True),
]
# draw-name -> key, for suppression (the renderer suppresses by draw-name).
PART_DRAWNAME_BY_KEY = {k: dn for k, dn, _s, _c in PART_CATALOG}
PART_KEY_BY_DRAWNAME = {dn: k for k, dn, _s, _c in PART_CATALOG}
PART_SUBSYS_BY_KEY = {k: s for k, _dn, s, _c in PART_CATALOG}

# A representative body per subsystem, used to size/anchor a replacement.
SUBSYS_ANCHOR_PART = {
    "chassis": "monocoque", "aerodynamics": "front_wing", "cooling": "radiator",
    "powertrain": "motor", "electrics": "accumulator", "suspension": "tire",
    "brakes": "brake_disc",
}


def subsystem_catalog():
    """Public list of (key, display_name, [draw-names]) for the simplified UI."""
    return list(SUBSYSTEM_CATALOG)


def part_catalog():
    """Public list of (key, display_name, subsystem, is_corner) — internal."""
    return list(PART_CATALOG)


def suggest_part_geometry_for(vp, part_key: str, ledger=None) -> dict:
    """Per-PART target envelope + home position (finer than per-subsystem).

    Falls back to the subsystem suggestion, then refines for parts that are
    smaller than their whole subsystem (a roll hoop is not the whole chassis).
    """
    sub = PART_SUBSYS_BY_KEY.get(part_key, "chassis")
    base = suggest_part_geometry(vp, sub, ledger=ledger)
    wb = float(getattr(vp, "wheelbase", 1550.0))
    tf = float(getattr(vp, "track_front", 1200.0))
    tr = float(getattr(vp, "track_rear", 1180.0))
    tire_r = 228.0
    x_front, x_rear = wb / 2.0, -wb / 2.0

    def out(l, w, h, x, y, z, shape="box", basis=None):
        return dict(l_mm=round(l, 0), w_mm=round(w, 0), h_mm=round(h, 0),
                    x_mm=round(x, 0), y_mm=round(y, 0), z_mm=round(z, 0),
                    shape=shape, basis=basis or base["basis"],
                    from_declared=base.get("from_declared", []))

    if part_key == "front_wing":
        return out(tire_r * 0.65, tf * 0.98, 80, x_front + tire_r * 1.6, 0, tire_r * 0.32,
                   basis=["chord", "≈ front track", "element stack"])
    if part_key == "rear_wing":
        return out(tire_r * 0.7, tr * 0.92, 120, x_rear - tire_r * 0.8, 0, tire_r * 1.15,
                   basis=["chord", "≈ rear track", "tall element stack"])
    if part_key == "roll_hoop":
        return out(60, min(tf, tr) * 0.55, tire_r * 1.7, x_rear + wb * 0.30, 0, tire_r * 1.2,
                   basis=["tube thickness", "shoulder width", "above the driver"])
    if part_key == "driver":
        return out(280, 300, 600, x_front - wb * 0.16, 0, tire_r * 1.4,
                   basis=["torso", "shoulders", "seated height"])
    if part_key == "data_logger":
        return out(160, 120, 80, -wb * 0.16, 0, tire_r * 1.1,
                   basis=["compact box", "beside driver", "above floor"])
    if part_key == "tire":
        return out(360, 200, 360, x_front, tf / 2.0, tire_r,
                   shape="cylinder", basis=["diameter", "section width", "diameter"])
    if part_key == "brake_disc":
        return out(40, 280, 280, x_front, tf / 2.0 - 40, tire_r * 0.9,
                   shape="cylinder", basis=["disc thickness", "Ø", "Ø at wheel"])
    if part_key == "upright":
        return out(180, 160, 260, x_front, tf / 2.0 - 110, tire_r,
                   basis=["upright depth", "width", "hub-to-arm"])
    # monocoque, sidepod, radiator, motor, accumulator: subsystem suggestion fits.
    return base


# Single source of truth for WHERE and HOW BIG each part is. Both the placeholder
# renderer (via _placeholder_box, drawn when a part has no replacement) and the
# replacement auto-fit read this, so a CAD/sketch/estimate lands in EXACTLY the
# same box its placeholder occupied — every part integrates on one coordinate
# system. Honours ledger env_* declarations through suggest_part_geometry_for.
def part_anchor(vp, part_key: str, ledger=None) -> dict:
    """Canonical (centre + size + shape) box for a catalog part, in car SAE mm."""
    return suggest_part_geometry_for(vp, part_key, ledger=ledger)


def dummy_body_footprint(fig, name: str) -> dict | None:
    """Measure a built-in body's real bounding box (mm) from a rendered figure.

    `suggest_part_geometry` gives a rough per-subsystem envelope, but a body like
    the monocoque actually spans ~2 m nose-to-tail — far bigger than the generic
    guess. When a user replaces a placeholder with real CAD we want the CAD to
    fill the SAME footprint the placeholder occupied, so it truly takes its
    place. This scans every trace drawn under `name` and returns the merged
    axis-aligned box:

        l_mm, w_mm, h_mm     extents in x, y, z
        x_mm, y_mm, z_mm     centre of that box

    Returns None if the body isn't present in the figure (e.g. already hidden).
    """
    xs_lo = ys_lo = zs_lo = float("inf")
    xs_hi = ys_hi = zs_hi = float("-inf")
    found = False
    for t in getattr(fig, "data", []):
        if getattr(t, "name", None) != name:
            continue
        x = getattr(t, "x", None)
        y = getattr(t, "y", None)
        z = getattr(t, "z", None)
        if x is None or y is None or z is None:
            continue
        try:
            xa = np.asarray(x, float); ya = np.asarray(y, float)
            za = np.asarray(z, float)
            xa = xa[np.isfinite(xa)]; ya = ya[np.isfinite(ya)]
            za = za[np.isfinite(za)]
            if not len(xa) or not len(ya) or not len(za):
                continue
        except Exception:
            continue
        xs_lo = min(xs_lo, xa.min()); xs_hi = max(xs_hi, xa.max())
        ys_lo = min(ys_lo, ya.min()); ys_hi = max(ys_hi, ya.max())
        zs_lo = min(zs_lo, za.min()); zs_hi = max(zs_hi, za.max())
        found = True
    if not found:
        return None
    return dict(
        l_mm=float(xs_hi - xs_lo), w_mm=float(ys_hi - ys_lo),
        h_mm=float(zs_hi - zs_lo),
        x_mm=float((xs_lo + xs_hi) / 2.0), y_mm=float((ys_lo + ys_hi) / 2.0),
        z_mm=float((zs_lo + zs_hi) / 2.0))
