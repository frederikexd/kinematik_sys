# --------------------------------------------------------------------------- #
#  fit_forecast.py — "will it assemble?" prediction for CAD library parts.
#
#  Instead of (or before) drawing a part on the 3D model, this module answers
#  the question the team actually has: *when I drop this into the SolidWorks
#  master assembly, what happens?* It fuses four mesh-level analyses into one
#  JSON-safe report:
#
#    1. mesh_report()          — geometry health: watertightness, open edges,
#                                degenerate triangles, principal (PCA) extents,
#                                enclosed volume, y-mirror symmetry.
#    2. slot_fit()             — the part's true size against the slot envelope
#                                it must occupy on THIS car, plus clearance /
#                                interference against every neighbouring body.
#                                The UI assembles the car headlessly in the
#                                background (same builder as the 3D view) each
#                                run, so these boxes are always fresh — no 3D
#                                tab visit required.
#    3. solidworks_forecast()  — the mesh + fit findings translated into the
#                                concrete messages SolidWorks will produce
#                                (Interference Detection hits, over-constrained
#                                mates, surface-body imports, mirrored parts).
#    4. performance_forecast() — mass & car-CG shift from enclosed volume ×
#                                material density, per-mount fastener
#                                utilisation under a chosen load case, and a
#                                subsystem-relevant capacity proxy (frontal
#                                area → drag; core area → heat rejection).
#
#  Everything is a *forecast*, not a mate solver: mesh-level, millimetre
#  fidelity, run in milliseconds. Provenance strings are attached wherever a
#  number is a proxy rather than a measurement, in keeping with the rest of
#  KinematiK — direction and go/no-go now, CAD/FEA for sign-off.
#
#  Pure numpy; no trimesh/scipy so it runs on a stock deployment. Inputs are
#  the same JSON-safe payload chassis.load_part_mesh() already produces
#  (verts recentred on bbox centre, mm) and the {name: {centre,size}} part
#  boxes the full-car renderer exposes.
# --------------------------------------------------------------------------- #
from __future__ import annotations

import numpy as np

__all__ = [
    "MATERIAL_DENSITY", "LOAD_CASES",
    "mesh_report", "resolve_slot", "slot_fit", "solidworks_forecast",
    "performance_forecast", "realism_score", "full_forecast",
]

# Densities (kg/m³) for the mass estimate. Deliberately short: the materials a
# FSAE part is actually machined/laid-up from. Anything exotic -> pick closest.
MATERIAL_DENSITY = {
    "Steel 4130":            7850.0,
    "Aluminium 6061/7075":   2760.0,
    "Titanium Ti-6Al-4V":    4430.0,
    "Carbon fibre laminate": 1550.0,
    "GFRP / fibreglass":     1900.0,
    "Nylon / printed":       1150.0,
}

# Quasi-static design load cases (g on each axis, SAE: x fore-aft, y lateral,
# z vertical). The classic FSAE bracket cases; per-mount fastener utilisation
# is checked against these.
LOAD_CASES = {
    "1.5 g cornering":       (0.0, 1.5, 1.0),
    "1.5 g braking":         (1.5, 0.0, 1.0),
    "3 g vertical bump":     (0.0, 0.0, 3.0),
    "2 g brake + 1 g bump":  (2.0, 0.0, 2.0),
}

# Single-shear allowable for one M6 8.8 fastener, newtons. 0.6·Rm·As =
# 0.6 · 800 MPa · 20.1 mm² ≈ 9.6 kN ultimate; /1.6 design factor -> ~6 kN.
_M6_ALLOW_N = 6000.0
_G = 9.81


# --------------------------------------------------------------------------- #
#  1 · mesh health
# --------------------------------------------------------------------------- #
def mesh_report(payload: dict, sample_cap: int = 1500) -> dict:
    """Geometry-health report from a load_part_mesh() payload.

    Returns bbox/PCA extents (mm), watertightness, open/non-manifold edge
    counts, degenerate-triangle count, enclosed volume (valid when closed),
    solidity (volume / bbox volume) and a 0..1 y-mirror symmetry score.
    """
    V = np.asarray(payload["verts"], float).reshape(-1, 3)
    F = np.asarray(payload["faces"], int).reshape(-1, 3)
    lo, hi = V.min(axis=0), V.max(axis=0)
    bbox = (hi - lo)
    diag = float(np.linalg.norm(bbox)) or 1.0

    # ---- edge manifold audit: every edge of a closed solid appears exactly
    # twice. Once (open/boundary) or 3+ (non-manifold junk) both mean STEP
    # import trouble in SolidWorks.
    E = np.sort(np.vstack([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]]), axis=1)
    _, counts = np.unique(E, axis=0, return_counts=True)
    open_edges = int((counts == 1).sum())
    nonmanifold_edges = int((counts > 2).sum())
    watertight = (open_edges == 0 and nonmanifold_edges == 0)

    # ---- degenerate triangles (zero-area slivers).
    e1 = V[F[:, 1]] - V[F[:, 0]]
    e2 = V[F[:, 2]] - V[F[:, 0]]
    areas2 = np.linalg.norm(np.cross(e1, e2), axis=1)          # 2 × area
    degenerate = int((areas2 < 1e-9 * diag * diag).sum())

    # ---- enclosed volume by signed tetrahedra (exact for a closed, oriented
    # mesh; a shell/open mesh gives a partial number — flagged by watertight).
    vol_mm3 = float(abs(np.einsum(
        "ij,ij->i", V[F[:, 0]], np.cross(V[F[:, 1]], V[F[:, 2]])).sum()) / 6.0)
    bbox_vol = float(bbox[0] * bbox[1] * bbox[2]) or 1.0
    solidity = float(np.clip(vol_mm3 / bbox_vol, 0.0, 1.0))

    # ---- PCA principal extents: the part's TRUE size along its own axes.
    # Catches a part exported rotated 30° whose axis-aligned bbox lies.
    C = V - V.mean(axis=0)
    if len(C) >= 3:
        _, _, VT = np.linalg.svd(C[:: max(1, len(C) // 4000)], full_matrices=False)
        P = C @ VT.T
        pca = np.sort(P.max(axis=0) - P.min(axis=0))[::-1]
    else:
        pca = np.sort(bbox)[::-1]

    # ---- y-mirror symmetry: sample points, mirror about y=0 (part is bbox-
    # centred so its own mid-plane is y=0), score by mean nearest-neighbour
    # distance normalised to the diagonal. 1.0 = perfectly handed-symmetric.
    idx = np.linspace(0, len(V) - 1, min(sample_cap, len(V))).astype(int)
    S = V[idx]
    M = S * np.array([1.0, -1.0, 1.0])
    d2 = ((S[:, None, :] - M[None, :, :]) ** 2).sum(axis=2)
    mean_nn = float(np.sqrt(d2.min(axis=1)).mean())
    symmetry = float(np.clip(1.0 - (mean_nn / (0.05 * diag)), 0.0, 1.0))

    return dict(
        bbox_mm=[float(b) for b in bbox],
        pca_mm=[float(p) for p in pca],
        diag_mm=diag,
        triangles=int(len(F)),
        watertight=bool(watertight),
        open_edges=open_edges,
        nonmanifold_edges=nonmanifold_edges,
        degenerate_tris=degenerate,
        volume_mm3=vol_mm3,
        solidity=solidity,
        symmetry=symmetry,
    )


# --------------------------------------------------------------------------- #
#  2 · fit against the slot + clearance to neighbours
# --------------------------------------------------------------------------- #
def _iter_instances(neighbor_boxes: dict | None):
    """Yield (name, centre, half) per INSTANCE. Values may be a single
    {centre,size} dict (legacy merged box) or a list of them (per-instance
    boxes from fig._part_instance_boxes)."""
    for nm, bx in (neighbor_boxes or {}).items():
        seq = bx if isinstance(bx, (list, tuple)) else [bx]
        for i, b in enumerate(seq):
            try:
                yield (nm, np.asarray(b["centre"], float),
                       np.asarray(b["size"], float) / 2.0)
            except Exception:
                continue


def resolve_slot(dummy_name: str | None, instance_boxes: dict | None,
                 fallback_env=None, fallback_centre=None) -> dict:
    """The honest envelope a part must fit on THIS car.

    Uses the dummy's per-instance box (one copy, not the merged multi-corner
    ring). When that dummy is a thin plate stand-in (min dimension < 25 mm —
    e.g. the 8 mm radiator core, the brake disc), the reserved space is really
    the smallest body that CONTAINS it (sidepod interior, wheel interior), so
    the container's box is returned instead and named, to be excluded from
    interference. Falls back to the supplied envelope when instances are
    unavailable.
    """
    env = np.asarray(fallback_env, float) if fallback_env is not None else None
    ctr = (np.asarray(fallback_centre, float)
           if fallback_centre is not None else np.zeros(3))
    container = None
    inst = (instance_boxes or {}).get(dummy_name or "", None)
    if inst:
        seq = inst if isinstance(inst, (list, tuple)) else [inst]
        # prefer the +y copy of a mirrored pair — they're identical anyway
        pick = max(seq, key=lambda b: (float(b["centre"][1]) >= 0,
                                       float(np.prod(b["size"]))))
        ctr = np.asarray(pick["centre"], float)
        env = np.asarray(pick["size"], float)
        if env.min() < 25.0:            # plate-like stand-in -> use container
            best = None
            for nm, c, h in _iter_instances(instance_boxes):
                if nm == dummy_name:
                    continue
                if (np.abs(ctr - c) <= h + 1.0).all():          # contains ctr
                    vol = float(np.prod(2 * h))
                    if vol > float(np.prod(env)) and (
                            best is None or vol < best[0]):
                        best = (vol, nm, c, h)
            if best is not None:
                _, container, c, h = best
                ctr, env = c, 2 * h
    return dict(env=[float(v) for v in (env if env is not None else (1, 1, 1))],
                centre=[float(v) for v in ctr], container=container)


def slot_fit(rep: dict, slot_env, slot_centre, neighbor_boxes: dict | None = None,
             exclude: tuple = ()) -> dict:
    """True-size fit of the part against its slot on THIS car.

    slot_env/slot_centre : the envelope the part must occupy (mm).
    neighbor_boxes       : {name: {centre,size}} merged boxes, or
                           {name: [{centre,size}, ...]} per-instance boxes
                           (fig._part_instance_boxes — much more accurate).
    exclude              : names to skip (the dummy being replaced, the slot's
                           container, the part itself if already on the car).

    The part's PCA extents are matched to the slot's sorted extents (best
    axis-aligned orientation), so 'oversize' means oversize in ANY orientation.
    Neighbour overlap where the neighbour essentially ENCLOSES the part
    (>=60% of the part's box inside a body >3x its volume) is classified as
    containment — by-design nesting like a radiator in a sidepod — and listed
    under `contained_in`, not `interferences`.
    """
    part = np.sort(np.asarray(rep["pca_mm"], float))[::-1]     # a >= b >= c
    slot = np.asarray(slot_env, float)
    order = np.argsort(slot)[::-1]                             # slot axes big->small
    slot_sorted = slot[order]
    ratios = part / np.where(slot_sorted <= 1e-6, 1e-6, slot_sorted)
    overhang = np.maximum(0.0, part - slot_sorted)

    # oriented part size laid back onto the car's x/y/z axes
    placed = np.empty(3)
    placed[order] = part
    fits = bool((ratios <= 1.0 + 1e-9).all())
    fill = float(np.clip(np.max(ratios), 0.0, 10.0))

    # unit-mixup smell: the part is drastically off-scale for its space in
    # EVERY orientation — ≥5× too big or ≤0.2× too small can't be a design
    # choice. Name the likely conversion factor when one fits (cm→mm 10×,
    # inch→mm 25.4×, m→mm 1000×, and their inverses, ±40%).
    r0 = max(float(ratios[0]), 1e-9)
    unit_suspect = bool(r0 >= 5.0 or r0 <= 0.2)

    part_box_vol = float(np.prod(np.maximum(placed, 1e-6)))
    c0 = np.asarray(slot_centre, float)
    half = placed / 2.0
    clear_by, interf, contained = {}, [], []
    for nm, cn, hn in _iter_instances(neighbor_boxes):
        if nm in exclude:
            continue
        gap_ax = np.abs(cn - c0) - (half + hn)                 # per-axis gap
        if (gap_ax < 0).all():                                 # boxes overlap
            ov = np.minimum(half + hn - np.abs(cn - c0),
                            2 * np.minimum(half, hn))
            ov_vol = float(np.prod(np.maximum(ov, 0.0)))
            nb_vol = float(np.prod(2 * hn))
            if ov_vol >= 0.6 * part_box_vol and nb_vol > 3.0 * part_box_vol:
                if nm not in contained:
                    contained.append(nm)                       # nested by design
            else:
                interf.append((nm, ov_vol / 1000.0))           # cm³
        else:
            g = float(gap_ax.max())                            # separating gap
            clear_by[nm] = min(g, clear_by.get(nm, g))         # min over copies
    clear = sorted(clear_by.items(), key=lambda t: t[1])
    interf.sort(key=lambda t: -t[1])

    return dict(
        fits=fits, fill=fill,
        placed_mm=[float(p) for p in placed],
        slot_mm=[float(s) for s in slot],
        overhang_mm=[float(o) for o in overhang],
        ratios=[float(r) for r in ratios],
        unit_suspect=bool(unit_suspect),
        scale_ratio=float(r0),
        tightest_neighbors=clear[:5],          # (name, gap mm), closest first
        interferences=interf,                  # (name, overlap cm³)
        contained_in=contained,                # nesting, by design — no penalty
    )


# --------------------------------------------------------------------------- #
#  3 · translate findings into what SolidWorks will actually say
# --------------------------------------------------------------------------- #
def solidworks_forecast(rep: dict, fit: dict) -> list[dict]:
    """Concrete, SolidWorks-flavoured predictions. Each item:
    {severity: 'stop'|'warn'|'ok', text: str}."""
    out = []

    if fit.get("unit_suspect"):
        _r = fit.get("scale_ratio", 0.0)
        _dirn = (f"~{_r:.0f}× too big" if _r >= 1.0
                 else f"~{1.0/max(_r,1e-9):.0f}× too small")
        out.append(dict(severity="stop", text=(
            f"Scale is off: {_dirn} for its space in every orientation. "
            "That magnitude is almost always a unit-conversion mixup "
            "(mm / cm / inch / m) in the export — fix the units before "
            "reading anything else in this forecast.")))

    if not fit["fits"]:
        ov = fit["overhang_mm"]
        worst = max(ov)
        out.append(dict(severity="stop", text=(
            "Oversize in every orientation: overhangs its space by up to "
            f"{worst:.0f} mm (per sorted axis: {ov[0]:.0f}/{ov[1]:.0f}/"
            f"{ov[2]:.0f} mm). Mates will solve, but Interference Detection "
            "will flag it against whatever borders that space — the part "
            "needs a redesign or the neighbours need to move.")))
    elif fit["fill"] > 0.94:
        out.append(dict(severity="warn", text=(
            f"Fills {fit['fill']*100:.0f}% of its space in the best "
            "orientation — assembles, but with almost no tolerance stack "
            "margin. One weld distortion or bracket shim and it touches.")))

    for nm, cc in fit.get("interferences", [])[:4]:
        out.append(dict(severity="stop", text=(
            f"Interference Detection: expect a hit against “{nm}” "
            f"(~{cc:.0f} cm³ of box-level overlap at the slot centre).")))
    for nm, g in fit.get("tightest_neighbors", [])[:3]:
        if 0 <= g < 15:
            out.append(dict(severity="warn", text=(
                f"Only {g:.0f} mm to “{nm}” — inside typical FSAE minimum "
                "clearance. Clearance Verification will flag it; decide the "
                "gap deliberately (heat shield? service access?).")))

    if not rep["watertight"]:
        out.append(dict(severity="warn", text=(
            f"Not a closed solid: {rep['open_edges']} open and "
            f"{rep['nonmanifold_edges']} non-manifold edges. SolidWorks will "
            "import this as SURFACE bodies — Knit + Thicken before mating, "
            "and treat the mass estimate below as a lower bound.")))
    if rep["degenerate_tris"] > 0:
        out.append(dict(severity="warn", text=(
            f"{rep['degenerate_tris']} sliver/zero-area facets — usually a "
            "too-coarse export tolerance; harmless visually, but re-export "
            "if the STEP won't knit.")))

    if rep["symmetry"] < 0.5:
        out.append(dict(severity="warn", text=(
            "Strongly handed part (y-mirror symmetry "
            f"{rep['symmetry']*100:.0f}%). Confirm it's the correct SIDE — "
            "mirrored handoffs are the classic silent assembly killer; the "
            "left and right versions mate without complaint and are both "
            "wrong on one corner.")))

    for nm in fit.get("contained_in", [])[:2]:
        out.append(dict(severity="ok", text=(
            f"Sits inside “{nm}” — nested by design (Interference Detection "
            "will report the overlap; mark that pair as ignored/coincident "
            "in the assembly rather than chasing it).")))

    if not out:
        out.append(dict(severity="ok", text=(
            "No red flags: closed solid, fits its space with margin, no "
            "predicted interference. Expect clean mates on the first insert.")))
    return out


# --------------------------------------------------------------------------- #
#  4 · performance under conditions
# --------------------------------------------------------------------------- #
def performance_forecast(rep: dict, *, material: str = "Aluminium 6061/7075",
                         load_case: str = "1.5 g cornering", n_mounts: int = 4,
                         base_mass_kg: float = 220.0, base_cg_mm=(0.0, 0.0, 280.0),
                         part_centre_mm=(0.0, 0.0, 250.0),
                         subsys: str | None = None) -> dict:
    """Mass, whole-car CG shift, fastener utilisation for the load case, and a
    subsystem-relevant capacity proxy. Every number carries its method."""
    rho = float(MATERIAL_DENSITY.get(material, 2760.0))
    vol_m3 = rep["volume_mm3"] * 1e-9
    mass = vol_m3 * rho
    mass_note = ("enclosed volume × density (closed solid — good estimate)"
                 if rep["watertight"] else
                 "enclosed volume × density on an OPEN mesh — lower bound; "
                 "knit the model or weigh the part for a real number")

    # whole-car CG shift when this mass is added at the part centre
    c = np.asarray(part_centre_mm, float)
    cg0 = np.asarray(base_cg_mm, float)
    tot = base_mass_kg + mass
    cg1 = (cg0 * base_mass_kg + c * mass) / (tot if tot > 0 else 1.0)
    dcg = (cg1 - cg0)

    # fastener utilisation under the chosen quasi-static case
    g = LOAD_CASES.get(load_case, (0.0, 1.5, 1.0))
    F = mass * _G * float(np.linalg.norm(g))
    per_mount = F / max(1, int(n_mounts))
    util = per_mount / _M6_ALLOW_N

    proxy = None
    bx = sorted(rep["bbox_mm"])[::-1]
    if subsys in ("aerodynamics", "aero", "bodywork"):
        area_m2 = (bx[0] * bx[1]) * 1e-6 * max(0.3, rep["solidity"])
        drag_n = 0.5 * 1.225 * (25.0 ** 2) * 0.9 * area_m2
        proxy = dict(label="Drag at 25 m/s (flat-plate Cd 0.9 on projected area)",
                     value=f"{drag_n:.0f} N",
                     note="direction-only surrogate — panel method / tunnel for real numbers")
    elif subsys in ("cooling",):
        core_m2 = (bx[0] * bx[1]) * 1e-6
        q_kw = core_m2 * 90.0        # ~90 kW/m² FSAE radiator core at speed
        proxy = dict(label="Heat rejection capacity (core face × 90 kW/m²)",
                     value=f"{q_kw:.1f} kW",
                     note="rule-of-thumb core flux — duct design changes this a lot")
    elif subsys in ("chassis",):
        proxy = dict(label="Torsional stiffness",
                     value="run the Frame Planner audit + APDL deck",
                     note="a mesh bbox can't predict frame stiffness — the "
                          "BEAM188 torsion deck in Integration exports it properly")

    # lap-time feel for the added mass (heuristic; stated as such)
    dlap = mass * 0.035

    return dict(
        material=material, density_kg_m3=rho,
        mass_kg=float(mass), mass_note=mass_note,
        cg_shift_mm=[float(v) for v in dcg],
        load_case=load_case, g_vector=list(g),
        inertial_force_n=float(F), n_mounts=int(n_mounts),
        per_mount_n=float(per_mount),
        fastener_util=float(util),
        fastener_note="vs one M6 8.8 in single shear @ 6 kN design allowable",
        lap_delta_s=float(dlap),
        lap_note="~0.035 s/lap per kg — FSAE autocross rule of thumb, not a sim",
        capacity_proxy=proxy,
    )


# --------------------------------------------------------------------------- #
#  5 · one number the team can argue about
# --------------------------------------------------------------------------- #
def realism_score(rep: dict, fit: dict) -> dict:
    """0–100 'assembles-as-is' realism, with the deductions itemised."""
    score, why = 100.0, []

    def hit(pts, reason):
        nonlocal score
        if pts > 0:
            score -= pts
            why.append((round(pts), reason))

    if fit.get("unit_suspect"):
        hit(45, "probable unit (inch/mm) mixup")
    if not fit["fits"]:
        hit(min(35.0, 100.0 * (fit["fill"] - 1.0) * 1.2 + 10.0),
            "oversize for its space in every orientation")
    elif fit["fill"] > 0.94:
        hit(6, "fits with <6% margin — tolerance-stack risk")
    ic = sum(cc for _, cc in fit.get("interferences", []))
    if ic > 0:
        hit(min(25.0, 8.0 + ic / 25.0), "predicted interference with neighbours")
    tight = [g for _, g in fit.get("tightest_neighbors", []) if 0 <= g < 15]
    if tight:
        hit(5, f"{len(tight)} neighbour(s) inside 15 mm clearance")
    if not rep["watertight"]:
        hit(min(18.0, 6.0 + rep["open_edges"] / 60.0),
            "open / non-manifold mesh — surface-body import likely")
    if rep["degenerate_tris"]:
        hit(3, "sliver facets in the tessellation")
    if rep["symmetry"] < 0.5:
        hit(6, "strongly handed — mirrored-part risk on handoff")

    score = float(np.clip(score, 0.0, 100.0))
    grade = ("assembles cleanly" if score >= 85 else
             "assembles with rework" if score >= 65 else
             "will fight you in SolidWorks")
    return dict(score=score, grade=grade, deductions=why)


def full_forecast(payload: dict, slot_env, slot_centre,
                  neighbor_boxes: dict | None = None, exclude: tuple = (),
                  **perf_kwargs) -> dict:
    """Convenience wrapper: run everything, return one JSON-safe dict."""
    rep = mesh_report(payload)
    fit = slot_fit(rep, slot_env, slot_centre, neighbor_boxes, exclude=exclude)
    return dict(
        mesh=rep, fit=fit,
        solidworks=solidworks_forecast(rep, fit),
        performance=performance_forecast(
            rep, part_centre_mm=tuple(slot_centre), **perf_kwargs),
        realism=realism_score(rep, fit),
    )


# --------------------------------------------------------------------------- #
#  self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # closed unit cube, scaled to 200×150×100 mm, centred
    v = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], float)
    v = (v - 0.5) * np.array([200, 150, 100])
    f = np.array([[0,1,3],[0,3,2],[4,6,7],[4,7,5],[0,4,5],[0,5,1],
                  [2,3,7],[2,7,6],[0,2,6],[0,6,4],[1,5,7],[1,7,3]])
    pay = dict(verts=v.tolist(), faces=f.tolist(), size_mm=[200, 150, 100])
    rep = mesh_report(pay)
    assert rep["watertight"], rep
    assert abs(rep["volume_mm3"] - 200*150*100) < 1.0, rep["volume_mm3"]
    assert rep["symmetry"] > 0.95
    fit = slot_fit(rep, (250, 180, 120), (0, 0, 250),
                   {"Radiator core": dict(centre=[0, 0, 250], size=[100, 80, 60]),
                    "Rear wing": dict(centre=[900, 0, 800], size=[300, 900, 400])})
    assert fit["fits"] and fit["interferences"][0][0] == "Radiator core"
    # per-instance neighbour list: two tire copies, min gap wins
    fit_i = slot_fit(rep, (250, 180, 120), (0, 0, 250),
                     {"Tire": [dict(centre=[0, 500, 250], size=[400, 200, 400]),
                               dict(centre=[0, -450, 250], size=[400, 200, 400])]})
    assert dict(fit_i["tightest_neighbors"])["Tire"] == min(
        500 - 75 - 100, 450 - 75 - 100)
    # containment: part nested inside a much bigger body -> no interference
    fit_c = slot_fit(rep, (250, 180, 120), (0, 0, 250),
                     {"Sidepod": dict(centre=[0, 0, 250], size=[600, 400, 350])})
    assert fit_c["contained_in"] == ["Sidepod"] and not fit_c["interferences"]
    # resolve_slot: thin plate dummy -> its container's box becomes the slot
    inst = {"Radiator core": [dict(centre=[100, 300, 200], size=[8, 90, 130])],
            "Sidepod": [dict(centre=[90, 300, 210], size=[520, 110, 160])]}
    rs2 = resolve_slot("Radiator core", inst)
    assert rs2["container"] == "Sidepod" and rs2["env"][0] == 520.0
    fc = solidworks_forecast(rep, fit)
    assert any(x["severity"] == "stop" for x in fc)
    perf = performance_forecast(rep, material="Aluminium 6061/7075")
    assert abs(perf["mass_kg"] - 0.003 * 2760.0) < 1e-6
    rs = realism_score(rep, fit)
    assert 0 <= rs["score"] < 100
    # oversize part vs tiny slot
    fit2 = slot_fit(rep, (50, 40, 30), (0, 0, 0))
    assert not fit2["fits"]
    assert realism_score(rep, fit2)["score"] < realism_score(rep, fit)["score"] + 100
    # open mesh (remove two faces)
    pay_open = dict(pay, faces=f[:-2].tolist())
    rep_o = mesh_report(pay_open)
    assert not rep_o["watertight"] and rep_o["open_edges"] > 0
    print("fit_forecast self-test OK:",
          f"score={rs['score']:.0f} ({rs['grade']}),",
          f"mass={perf['mass_kg']:.2f} kg, msgs={len(fc)}")
