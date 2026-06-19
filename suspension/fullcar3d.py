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

# --------------------------------------------------------------------------- #
#  Palette — matched to the app's dark instrument styling.
# --------------------------------------------------------------------------- #
COLORS = dict(
    upper="#37e0d0", lower="#ffb02e", upright="#ffffff", tie="#ff5a52",
    push="#9b8cff", rocker="#5ad17a", spring="#ff9f43",
    wheel="#6f7d8c", tire="#15181c", tire_edge="#3a434c", rim="#23282e",
    # Matte-black tub with a hint of the photo's red/yellow livery accent.
    monocoque="#1d2024", nose="#23262b", livery="#d63a2f",
    frame="#0d1013", hoop="#0d1013", helmet="#e9edf1", helmet_band="#d63a2f",
    halo="#10141a",
    wing="#202428", wing_edge="#3a414a", endplate="#16191d",
    sidepod="#23262b", radiator="#ff6b5a", inlet="#0e1216",
    engine="#3a3a3f", motor="#454a52", airbox="#33373d",
    battery="#1f2a20", batt_edge="#5ad17a",
    brake="#c2410c", logger="#33373d",
    point="#e7ecf1", floor="#0c1014", cg="#ffd166",
)


# --------------------------------------------------------------------------- #
#  Mesh primitives
# --------------------------------------------------------------------------- #
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
def _corner_transform(p, *, mirror_y, lateral_scale, x_shift, y_center_ref):
    if p is None:
        return None
    q = np.array(p, float).copy()
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
        markers = [pts[k] for k in (
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
    markers = [np.asarray(v, float) - drop for v in named.values()]
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
    highlight_subsystem: str | None = None,
    focus_subsystem: str | None = None,
    tire_width_mm: float = 180.0,
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

    def op(subsys, base):
        if highlight_subsystem is None:
            return base
        return base if subsys == highlight_subsystem else base * 0.16

    def edge_op(subsys):
        if highlight_subsystem is None or subsys is None:
            return 1.0
        return 1.0 if subsys == highlight_subsystem else 0.14

    legend_done = set()

    # Per-subsystem point accumulator. Every body that belongs to a clickable
    # subsystem feeds its vertices here, so afterwards we know the bounding box
    # of each subsystem and can frame the camera on whichever one is clicked.
    subsys_pts: dict[str, list] = {}

    def _accrue(subsys, pts):
        if not subsys or pts is None:
            return
        bucket = subsys_pts.setdefault(subsys, [])
        bucket.extend(np.asarray(pts, float).reshape(-1, 3).tolist())

    def seg(p, q, color, w=5, name=None, group=None, subsys=None):
        if p is None or q is None:
            return
        _accrue(subsys, [p, q])
        fig.add_trace(go.Scatter3d(
            x=[p[0], q[0]], y=[p[1], q[1]], z=[p[2], q[2]],
            mode="lines", line=dict(color=color, width=w),
            opacity=edge_op(subsys), name=name, legendgroup=group,
            showlegend=name is not None, hoverinfo="skip",
            customdata=[subsys, subsys] if subsys else None))

    def mesh(verts, i, j, k, color, name, subsys, base_op=0.6, hover=None):
        once = name not in legend_done
        legend_done.add(name)
        _accrue(subsys, verts)
        # customdata carries the clickable subsystem id on every vertex, so a
        # Streamlit selection event can read which part the user picked.
        cd = [subsys] * len(verts) if subsys else None
        fig.add_trace(go.Mesh3d(
            x=verts[:, 0], y=verts[:, 1], z=verts[:, 2], i=i, j=j, k=k,
            color=color, opacity=op(subsys, base_op), flatshading=True,
            name=name, showlegend=once, customdata=cd,
            hoverinfo="text" if hover else "skip", text=hover))

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
                                 x_shift=x_shift, y_center_ref=y_center)

    for axle, corner, lat_scale, x_shift, mirror in stations:
        # draw every member the topology reported
        for p, q, label, color, group in corner["segments"]:
            pT = _xform(p, mirror, lat_scale, x_shift)
            qT = _xform(q, mirror, lat_scale, x_shift)
            seg(pT, qT, color, 5, corner_name(label), group, "suspension")

        wc = _xform(corner["wheel_center"], mirror, lat_scale, x_shift)
        cp = _xform(corner["contact_patch"], mirror, lat_scale, x_shift)
        # wheel hub line
        seg(cp, wc, COLORS["wheel"], 3, corner_name("Wheel hub"), "wheel", "suspension")

        cam = np.deg2rad(corner["camber"])
        sign = -1.0 if mirror else 1.0
        axis = np.array([0.0, sign * np.cos(cam), np.sin(cam)])
        radius = abs(wc[2] - cp[2]) or 228.0
        if show_tires:
            tv, ti, tj, tk = _cylinder(wc, axis, radius, tire_width_mm, n=30)
            mesh(tv, ti, tj, tk, COLORS["tire"], "Tire", "suspension",
                 base_op=0.95)
            # Rim: a slightly inset, lighter disc so the wheel reads as a wheel,
            # not a black drum — sits at ~62% of tire radius on the outboard face.
            rim_r = radius * 0.62
            rv, ri, rj, rk = _cylinder(wc, axis, rim_r, tire_width_mm * 0.9, n=24)
            mesh(rv, ri, rj, rk, COLORS["rim"], "Tire", "suspension",
                 base_op=0.98)
        if show_brakes:
            disc_r = (radius * _clamp(0.62 + (brake_tq or 0) / 4000.0, 0.5, 0.85)
                      if brake_tq else radius * 0.62)
            dv, di, dj, dk = _cylinder(wc, axis, disc_r,
                                       max(8.0, tire_width_mm * 0.07), n=26)
            hv = ("Brake disc · r≈%.0f mm" % disc_r
                  + (" (sized from %.0f N·m)" % brake_tq if brake_tq else ""))
            mesh(dv, di, dj, dk, COLORS["brake"], "Brake disc", "brakes",
                 base_op=0.9, hover=hv)

        mk = [_xform(m, mirror, lat_scale, x_shift) for m in corner["markers"]]
        if mk:
            _accrue("suspension", mk)
            fig.add_trace(go.Scatter3d(
                x=[p[0] for p in mk], y=[p[1] for p in mk], z=[p[2] for p in mk],
                mode="markers", marker=dict(size=3, color=COLORS["point"]),
                opacity=edge_op("suspension"), showlegend=False, hoverinfo="skip",
                customdata=["suspension"] * len(mk)))

    # z-extent + tire radius derived from the extracted corners (any topology).
    z_all = []
    for corner in (front_corner, rear_corner):
        for p, q, *_ in corner["segments"]:
            z_all += [p[2], q[2]]
        z_all += [corner["wheel_center"][2], corner["contact_patch"][2]]
    z_lo, z_hi = (min(z_all), max(z_all)) if z_all else (0.0, 300.0)
    tire_r = abs(front_corner["wheel_center"][2]
                 - front_corner["contact_patch"][2]) or 228.0
    inner_y_f = tf / 2.0 - tire_width_mm - 40
    inner_y_r = tr / 2.0 - tire_width_mm - 40

    # ---- 2) chassis: FSAE monocoque + nosecone + roll hoops + driver ---- #
    #  Reshaped to read as a real Formula Student car (cf. the reference photo):
    #  a low, slim survival cell that tapers to a pointed nosecone at the front,
    #  an open cockpit, a curved MAIN roll hoop behind the driver's head and a
    #  smaller FRONT hoop ahead of the dash, plus the driver's helmet showing in
    #  the cockpit — not an F1 halo.
    if show_bodywork:
        ch_it = _iface(ledger, "chassis")
        tub_w = _clamp(min(inner_y_f, inner_y_r) * 1.1, 140, 320)
        # Sit the tub low like an FSAE car: floor near the ground, deck low.
        tub_bot = max(z_lo * 0.5, tire_r * 0.14)
        tub_top = tub_bot + _clamp(tire_r * 0.95, 180, 360)
        cz = (tub_top + tub_bot) / 2
        hzz = (tub_top - tub_bot) / 2
        prof = _ellipse_ring(24)

        # Lofted survival cell: pointed nose tip -> footwell -> cockpit bay ->
        # tapered tail. Stations run front (tip) to rear.
        nose_tip_x = x_front + tire_r * 1.9
        tail_x = x_rear + tire_r * 0.15
        xs = np.linspace(nose_tip_x, tail_x, 9)
        #             tip   nose  foot  dash  cockpit cock  belly tail   end
        widths  = np.array([0.05, 0.22, 0.55, 0.88, 1.00, 0.98, 0.86, 0.66, 0.5])
        heights = np.array([0.10, 0.30, 0.62, 0.85, 0.92, 0.92, 0.85, 0.74, 0.6])
        zoff    = np.array([0.55, 0.42, 0.18, 0.04, 0.00, 0.00, 0.02, 0.06, 0.1]) * hzz
        scales = [(tub_w / 2 * w, hzz * h, 0.0, cz + dz)
                  for w, h, dz in zip(widths, heights, zoff)]
        mv, mi, mj, mk = _prism_xsection(prof, xs, scales)
        hv = "Monocoque / survival cell"
        if _g(ch_it, "mass_kg"):
            hv += " · %.1f kg" % _g(ch_it, "mass_kg")
        mesh(mv, mi, mj, mk, COLORS["monocoque"], "Monocoque", "chassis",
             base_op=0.92, hover=hv)

        # A thin livery flash down the flank (the photo's red/yellow stripe).
        fv, fi, fj, fk = _prism_xsection(
            _ellipse_ring(24),
            np.linspace(nose_tip_x - tire_r * 0.3, tail_x, 6),
            [(tub_w / 2 * w * 1.005, hzz * h * 0.16, 0.0, cz + dz)
             for w, h, dz in zip(widths[2:8], heights[2:8], zoff[2:8])])
        mesh(fv, fi, fj, fk, COLORS["livery"], "Monocoque", "chassis", 0.9)

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
    if cg_h > 0:
        fig.add_trace(go.Scatter3d(
            x=[cg_x], y=[cg_y], z=[cg_h], mode="markers+text",
            marker=dict(size=8, color=COLORS["cg"], symbol="diamond"),
            text=[cg_label], textposition="top center",
            textfont=dict(color=COLORS["cg"], size=11),
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
    if focus_subsystem and subsys_pts.get(focus_subsystem):
        camera_revision = "focus:%s" % focus_subsystem
        pts = np.asarray(subsys_pts[focus_subsystem], float)
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        ctr = (lo + hi) / 2.0

        # Aspect mode is "data", so camera coordinates are normalised against the
        # full scene span on each axis. Express the focus centre in that space and
        # bring the eye close along the standard viewing direction.
        all_pts = np.asarray([p for b in subsys_pts.values() for p in b], float)
        smin, smax = all_pts.min(axis=0), all_pts.max(axis=0)
        span = np.where((smax - smin) == 0, 1.0, (smax - smin))
        c_norm = (ctr - (smin + smax) / 2.0) / span  # centred, normalised

        # how big the part is relative to the whole car -> how hard we zoom
        part_span = (hi - lo)
        frac = float(np.clip(np.max(part_span / span), 0.04, 0.9))
        dist = 0.55 + frac * 1.4  # closer for small parts, backed off for big

        dir_unit = np.array([1.0, -0.95, 0.6])
        dir_unit = dir_unit / np.linalg.norm(dir_unit)
        eye = c_norm + dir_unit * dist
        scene_camera = dict(
            center=dict(x=float(c_norm[0]), y=float(c_norm[1]), z=float(c_norm[2])),
            eye=dict(x=float(eye[0]), y=float(eye[1]), z=float(eye[2])))

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
            xaxis=dict(title="x (rear ←→ front)", backgroundcolor="#0e1216",
                       gridcolor="#1d242c", color="#8d99a6"),
            yaxis=dict(title="y (right)", backgroundcolor="#0e1216",
                       gridcolor="#1d242c", color="#8d99a6"),
            zaxis=dict(title="z (up)", backgroundcolor="#0e1216",
                       gridcolor="#1d242c", color="#8d99a6"),
            aspectmode="data", camera=scene_camera,
            dragmode="turntable"),
        font=dict(family="JetBrains Mono", color="#cdd6df", size=10),
        height=height, margin=dict(l=0, r=0, t=10, b=0),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(size=10), itemsizing="constant"))
    return fig


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
