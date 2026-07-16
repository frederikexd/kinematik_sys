# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
#
#  Module: cad_share — the compute behind "CHASSIS DOCS & SHARED CAD".
#
#  Three jobs, all pure/testable (no Streamlit here):
#    1. Hardpoints + frame  -> a hardpoints CSV and an ANSYS APDL (.inp) deck
#       of BEAM188 line bodies for a torsional-stiffness run.
#    2. SES location pack    -> a component-location coordinate table (CSV) from
#       the HV accumulator, LV battery and every placed part on the 3D model.
#    3. File-size guarding for the Team CAD library (embed vs link decision).
#
#  Coordinate system matches the rest of the package and fullcar3d: SAE axes,
#  millimetres — x rear, y right, z up. The APDL deck is written in metres
#  (ANSYS SI) with an explicit unit conversion so the recipe is dimensionally
#  clean; the section/material come from the Compliance tab and the tube table.
# ============================================================================

from __future__ import annotations

import csv
import io
import math
import datetime as _dt
from dataclasses import dataclass
from typing import Optional

# Embed cap for the CAD library: files at or under this go into the project
# document as base64; anything larger must be shared as a link (a full SLDASM
# or a fine STL blows past this fast, and we don't want to bloat project.json).
CAD_EMBED_LIMIT_BYTES = 10 * 1024 * 1024   # 10 MB


# --------------------------------------------------------------------------- #
#  File-size guard for the Team CAD library
# --------------------------------------------------------------------------- #
def within_embed_limit(size_bytes: int) -> bool:
    return 0 <= int(size_bytes) <= CAD_EMBED_LIMIT_BYTES


def human_size(size_bytes: int) -> str:
    n = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{size_bytes} B"


# --------------------------------------------------------------------------- #
#  Hardpoints CSV
# --------------------------------------------------------------------------- #
# The named 3D pickup points we export per corner. Order is stable so the CSV
# and the APDL node numbering are reproducible run to run.
_HARDPOINT_KEYS = [
    "upper_front_inner", "upper_rear_inner",
    "lower_front_inner", "lower_rear_inner",
    "upper_outer", "lower_outer",
    "tie_rod_inner", "tie_rod_outer",
    "wheel_center", "contact_patch",
    "pushrod_outer", "rocker_pivot", "rocker_pushrod",
    "rocker_spring", "spring_inner",
]


def _as_xyz(v) -> Optional[tuple]:
    """Coerce a hardpoint value (np.ndarray | list | tuple) to (x, y, z) floats,
    or None if it isn't a usable 3-vector (e.g. an unset optional pickup)."""
    if v is None:
        return None
    try:
        seq = list(v)
        if len(seq) < 3:
            return None
        return (float(seq[0]), float(seq[1]), float(seq[2]))
    except (TypeError, ValueError):
        return None


def hardpoints_csv(hp_dict: dict) -> str:
    """A tidy point,x_mm,y_mm,z_mm table from a Hardpoints.as_dict() mapping.
    Unset optional points (rocker/pushrod on a direct-acting corner) are
    skipped rather than written as blanks."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["point", "x_mm", "y_mm", "z_mm"])
    for k in _HARDPOINT_KEYS:
        xyz = _as_xyz(hp_dict.get(k))
        if xyz is None:
            continue
        w.writerow([k, f"{xyz[0]:.3f}", f"{xyz[1]:.3f}", f"{xyz[2]:.3f}"])
    return buf.getvalue()


# --------------------------------------------------------------------------- #
#  SES component-location pack
# --------------------------------------------------------------------------- #
@dataclass
class LocatedPart:
    """A named part with a 3D centroid (mm, SAE axes) for the SES pack."""
    name: str
    x_mm: float
    y_mm: float
    z_mm: float
    category: str = "part"     # "HV" | "LV" | "part" — SES cares about HV/LV most


def ses_location_rows(hv=None, lv=None, placed: Optional[dict] = None
                      ) -> list[LocatedPart]:
    """
    Assemble the SES location list. `hv` and `lv` are optional (x, y, z) mm
    tuples for the HV accumulator and LV battery — the two SES cares about most.
    `placed` is a mapping name -> (x, y, z) of every other part already placed
    on the 3D model (e.g. fullcar3d.subsys_centroids()).
    """
    out: list[LocatedPart] = []
    hv_xyz = _as_xyz(hv)
    if hv_xyz:
        out.append(LocatedPart("HV accumulator", *hv_xyz, category="HV"))
    lv_xyz = _as_xyz(lv)
    if lv_xyz:
        out.append(LocatedPart("LV battery", *lv_xyz, category="LV"))
    for name, xyz in sorted((placed or {}).items()):
        p = _as_xyz(xyz)
        if p:
            out.append(LocatedPart(str(name), *p, category="part"))
    return out


def ses_location_csv(rows: list[LocatedPart]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["component", "category", "x_mm", "y_mm", "z_mm"])
    for r in rows:
        w.writerow([r.name, r.category,
                    f"{r.x_mm:.1f}", f"{r.y_mm:.1f}", f"{r.z_mm:.1f}"])
    return buf.getvalue()


# --------------------------------------------------------------------------- #
#  ANSYS APDL export — BEAM188 line bodies for torsional stiffness
# --------------------------------------------------------------------------- #
@dataclass
class BeamMaterial:
    """Isotropic elastic material for the APDL deck (SI: Pa, kg/m³)."""
    name: str
    E_pa: float
    nu: float = 0.29
    rho_kg_m3: float = 7850.0

    @property
    def G_pa(self) -> float:
        return self.E_pa / (2.0 * (1.0 + self.nu))


@dataclass
class BeamSection:
    """Round tube section for BEAM188 (SECTYPE,,BEAM,CTUBE). Dimensions in m."""
    od_m: float
    wall_m: float

    @property
    def id_m(self) -> float:
        return max(0.0, self.od_m - 2.0 * self.wall_m)

    @property
    def ri_m(self) -> float:
        return self.id_m / 2.0

    @property
    def ro_m(self) -> float:
        return self.od_m / 2.0


def _round(x: float, nd: int = 6) -> float:
    return round(float(x), nd)


def build_apdl_deck(hp_dict: dict,
                    section: BeamSection,
                    material: BeamMaterial,
                    frame=None,
                    *,
                    title: str = "KinematiK torsional stiffness",
                    mesh_div: int = 4) -> str:
    """
    Generate an ANSYS Mechanical APDL (.inp) deck of BEAM188 line bodies for a
    torsional-stiffness study.

    Geometry:
      * Every defined suspension hardpoint becomes a keypoint; the physical
        links (wishbone legs, tie rod, pushrod, rocker arm, spring) become
        lines meshed with BEAM188.
      * If a Frame Planner `frame` (tubeframe.FrameGraph) is supplied, its nodes
        and tubes are added automatically with the same beam element, each tube
        carrying its own section from the frame's size table.

    The deck ends with a COMMENTED recipe (not executed constraints, so the
    engineer stays in control): constrain the rear hubs, apply a front couple,
    read the twist, K = T / theta.

    Units: written in SI (metres, Pa). Hardpoint coords are mm and converted.
    Returns the deck as text.
    """
    mm = 1.0e-3
    lines: list[str] = []
    A = lines.append

    ts = _dt.datetime.now().isoformat(timespec="seconds")
    A(f"! ======================================================================")
    A(f"! {title}")
    A(f"! Generated by KinematiK on {ts}")
    A(f"! Units: SI (m, Pa, kg). SAE axes as modelled (x rear, y right, z up).")
    A(f"! BEAM188 line bodies. Section + material from the Compliance tab.")
    A(f"! ======================================================================")
    A("/PREP7")
    A("ET,1,BEAM188")
    A("KEYOPT,1,3,3        ! quadratic transverse shear, cubic option")
    A("")

    # --- material -------------------------------------------------------- #
    A(f"! --- material: {material.name} ---")
    A(f"MP,EX,1,{material.E_pa:.6g}")
    A(f"MP,PRXY,1,{material.nu:.4g}")
    A(f"MP,DENS,1,{material.rho_kg_m3:.6g}")
    A("")

    # --- suspension tube section (section id 1) -------------------------- #
    A("! --- suspension link section (round tube) ---")
    A("SECTYPE,1,BEAM,CTUBE,susp_tube")
    A(f"SECDATA,{_round(section.ri_m)},{_round(section.ro_m)},8")
    A("")

    # --- keypoints from hardpoints --------------------------------------- #
    kp_of: dict[str, int] = {}
    kp = 0
    A("! --- suspension keypoints (from hardpoints, mm -> m) ---")
    for key in _HARDPOINT_KEYS:
        xyz = _as_xyz(hp_dict.get(key))
        if xyz is None:
            continue
        kp += 1
        kp_of[key] = kp
        A(f"K,{kp},{_round(xyz[0]*mm)},{_round(xyz[1]*mm)},{_round(xyz[2]*mm)}   ! {key}")
    A("")

    # --- suspension links (lines between keypoints) ---------------------- #
    # Each tuple: (label, endpoint_key_a, endpoint_key_b). Only emitted when
    # both endpoints exist, so a direct-acting corner (no rocker) still works.
    link_defs = [
        ("uca_front", "upper_outer", "upper_front_inner"),
        ("uca_rear",  "upper_outer", "upper_rear_inner"),
        ("lca_front", "lower_outer", "lower_front_inner"),
        ("lca_rear",  "lower_outer", "lower_rear_inner"),
        ("tie_rod",   "tie_rod_outer", "tie_rod_inner"),
        ("upright_u", "upper_outer", "wheel_center"),
        ("upright_l", "lower_outer", "wheel_center"),
        ("pushrod",   "pushrod_outer", "rocker_pushrod"),
        ("rocker_arm", "rocker_pivot", "rocker_pushrod"),
        ("rocker_spr", "rocker_pivot", "rocker_spring"),
        ("spring",    "rocker_spring", "spring_inner"),
    ]
    A("SECNUM,1")
    A("TYPE,1 $ MAT,1")
    A("! --- suspension links (BEAM188) ---")
    n_links = 0
    for label, a, b in link_defs:
        if a in kp_of and b in kp_of:
            A(f"L,{kp_of[a]},{kp_of[b]}   ! {label}")
            n_links += 1
    A("")

    # --- optional Frame Planner tubes ------------------------------------ #
    n_frame_tubes = 0
    if frame is not None and getattr(frame, "nodes", None):
        A("! ====================================================================")
        A("! Frame Planner tubes (auto-included: a frame is loaded)")
        A("! ====================================================================")
        node_kp: dict[str, int] = {}
        for nid, node in frame.nodes.items():
            xyz = _as_xyz(getattr(node, "xyz_mm", None))
            if xyz is None:
                continue
            kp += 1
            node_kp[str(nid)] = kp
            A(f"K,{kp},{_round(xyz[0]*mm)},{_round(xyz[1]*mm)},{_round(xyz[2]*mm)}   ! node {nid}")
        A("")

        # One BEAM section per distinct tube size in the frame's size table.
        size_table = getattr(frame, "size_table", {}) or {}
        sec_of_size: dict[str, int] = {}
        sec_id = 1
        for skey, spec in sorted(size_table.items()):
            sec_id += 1
            sec_of_size[skey] = sec_id
            ri = (float(spec.od_mm) - 2.0 * float(spec.wall_mm)) / 2.0 * mm
            ro = float(spec.od_mm) / 2.0 * mm
            A(f"SECTYPE,{sec_id},BEAM,CTUBE,frame_{skey}")
            A(f"SECDATA,{_round(ri)},{_round(ro)},8")
        A("")

        for tube in getattr(frame, "tubes", []):
            a, b = str(tube.a), str(tube.b)
            if a in node_kp and b in node_kp:
                sid = sec_of_size.get(getattr(tube, "size", ""), 1)
                A(f"SECNUM,{sid}")
                A(f"L,{node_kp[a]},{node_kp[b]}   ! frame tube {tube.name} (size {getattr(tube,'size','?')})")
                n_frame_tubes += 1
        A("")

    # --- mesh ------------------------------------------------------------ #
    A("! --- mesh all line bodies ---")
    A(f"LESIZE,ALL,,,{int(mesh_div)}")
    A("LMESH,ALL")
    A("FINISH")
    A("")

    # --- solution recipe (commented; the engineer applies real BCs) ------ #
    A("! ====================================================================")
    A("! TORSIONAL STIFFNESS RECIPE (uncomment / adapt node selections)")
    A("!   1. Constrain the REAR hub keypoints (fix all DOF).")
    A("!   2. Apply a couple across the FRONT hubs: equal & opposite Fz")
    A("!      a known track half-width apart  ->  torque T = F * track.")
    A("!   3. Solve, read vertical deflection dz at each front hub.")
    A("!   4. twist theta = atan((dz_left - dz_right) / track)   [rad]")
    A("!      K_torsion = T / theta   [N*m/rad]  (or /deg * pi/180).")
    A("! --------------------------------------------------------------------")
    A("! /SOLU")
    A("! ANTYPE,STATIC")
    A("! ! --- example: fix rear hubs ---")
    A("! ! NSEL,S,LOC,X,<rear_hub_x_m>   $ D,ALL,ALL,0   $ ALLSEL")
    A("! ! --- example: front couple ---")
    A("! ! F,<front_left_node>,FZ, 500")
    A("! ! F,<front_right_node>,FZ,-500")
    A("! SOLVE")
    A("! FINISH")
    A("! /POST1")
    A("! ! PRNSOL,U,Z   ! read front-hub vertical deflections")
    A("! ====================================================================")
    A(f"! summary: {n_links} suspension links"
      + (f" + {n_frame_tubes} frame tubes" if n_frame_tubes else "")
      + f", {kp} keypoints total.")
    A("")

    deck = "\n".join(lines)
    # Stash small counts on the string's behalf via a companion function isn't
    # possible; callers that want the counts use apdl_counts() below.
    return deck


def apdl_counts(hp_dict: dict, frame=None) -> dict:
    """How many links/tubes/keypoints build_apdl_deck will emit — for the UI's
    'generated N links + M tubes' confirmation without re-parsing the deck."""
    kp = sum(1 for k in _HARDPOINT_KEYS if _as_xyz(hp_dict.get(k)) is not None)
    link_pairs = [
        ("upper_outer", "upper_front_inner"), ("upper_outer", "upper_rear_inner"),
        ("lower_outer", "lower_front_inner"), ("lower_outer", "lower_rear_inner"),
        ("tie_rod_outer", "tie_rod_inner"),
        ("upper_outer", "wheel_center"), ("lower_outer", "wheel_center"),
        ("pushrod_outer", "rocker_pushrod"), ("rocker_pivot", "rocker_pushrod"),
        ("rocker_pivot", "rocker_spring"), ("rocker_spring", "spring_inner"),
    ]
    present = {k for k in _HARDPOINT_KEYS if _as_xyz(hp_dict.get(k)) is not None}
    n_links = sum(1 for a, b in link_pairs if a in present and b in present)
    n_frame = 0
    n_frame_nodes = 0
    if frame is not None and getattr(frame, "nodes", None):
        node_ok = {str(nid) for nid, node in frame.nodes.items()
                   if _as_xyz(getattr(node, "xyz_mm", None)) is not None}
        n_frame_nodes = len(node_ok)
        n_frame = sum(1 for t in getattr(frame, "tubes", [])
                      if str(t.a) in node_ok and str(t.b) in node_ok)
    return {"links": n_links, "frame_tubes": n_frame,
            "keypoints": kp + n_frame_nodes}
