# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================

"""
Frame Planner — the tube frame as a first-class, auditable object.

Three chassis-meeting pain points, three computable answers:

    TRIANGULATION / LOAD PATH
        "Main hoop support needs a triangulated load path to the lower side
        impact node. These tubes interrupting the load paths are illegal in
        2027."  The frame becomes a node/tube graph, and the graph is audited:
        which nodes are NOT part of any tube triangle, which quadrilateral bays
        have no diagonal, which tube ends land MID-SPAN on another tube instead
        of at a node (the classic load-path interruption), and — the question
        the meeting actually asked — is there a continuously-triangulated load
        path between two named nodes?  Every failure comes with the concrete
        fix: which diagonal to add, how long it is, what it weighs, what it
        costs in the spec the rules require.

    TUBE SIZING & SOURCING
        "Suppliers don't normally have our smallest tube size (Size C, 1.2 mm
        wall).  When 1.2 mm tubing is offered it is close to $10/ft.  We might
        have to increase Size C to match Size B (1.65 mm)."  That sentence is
        a trade study nobody had run.  The frame graph knows every tube's
        length and spec, so it rolls up a per-spec BOM (length, mass, cost,
        sourcing risk) and answers the consolidation question with numbers:
        merge C into B and the whole car gets ΔM kg heavier and Δ$ cheaper
        (or dearer), tube by tube.  An equivalency helper screens any
        alternative OD×wall against the rules baseline the way the rulebook
        frames it — bending stiffness (E·I) and bending strength must not
        decrease, wall must not fall below the absolute floor.

    PANELS & ATTACHMENTS
        The subteam to-dos — seat & harness mounting, quick-release chassis
        floor, quick-release firewall, aero panel attachment ("how close
        together do the mounting points need to be to keep bodywork stable,
        how strong do attachment brackets need to be") — all reduce to one
        calculation: a panel of known size/material/thickness, fastened on a
        pitch, under a pressure + inertial load.  Per-fastener load, panel
        deflection between fasteners, the maximum pitch that still meets a
        stiffness target, and a screening verdict for each quick-release
        fastener family.  Harness loads are resolved per attachment point at a
        chosen deceleration and belt geometry, formatted to drop straight into
        the existing bolt & bracket FoS screen.

HONESTY RULES (same as everywhere else in KinematiK)
    · Rules-minimum tables encode the classic FSAE steel-tube size classes and
      are labelled with the year they were transcribed from.  ALWAYS verify
      against the rulebook year you will compete under — rules move, and the
      meeting itself says 2027 moves them again.
    · Fastener capacities are screening figures from typical published vendor
      data, tagged `judgement`.  Confirm against the actual part's datasheet
      before anything flies.
    · This is pre-validation: it finds the missing diagonal and sizes the
      bracket load.  ANSYS confirms the frame; a pull test confirms the tab.

Units: mm, N, kg, MPa throughout (same SAE frame as the rest of the package:
x rear, y right, z up).  Costs in USD; tube pricing is quoted per foot because
that is how US suppliers quote it (1 ft = 304.8 mm).
"""

from __future__ import annotations

import io
import csv
import math
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple

import numpy as np


# --------------------------------------------------------------------------- #
#  Constants
# --------------------------------------------------------------------------- #
STEEL_RHO_KG_M3 = 7850.0          # 4130 / mild steel — close enough for both
STEEL_E_MPA = 205000.0
MM_PER_FT = 304.8

# Absolute wall-thickness floors for steel alternative tubing (FSAE F.3.4-style):
# hoops & harness attachment may never go below 2.0 mm, everything else 1.2 mm.
MIN_WALL_HOOP_MM = 2.0
MIN_WALL_OTHER_MM = 1.2

RULES_YEAR_TRANSCRIBED = "FSAE 2024-25 (F.3.2 baseline classes)"
RULES_DISCLAIMER = (
    "Size classes transcribed from the {yr} rulebook. Verify against the "
    "rulebook year you compete under.".format(yr=RULES_YEAR_TRANSCRIBED))


# --------------------------------------------------------------------------- #
#  Tube specs & size classes
# --------------------------------------------------------------------------- #
@dataclass
class TubeSpec:
    """One purchasable round-steel tube section.

    cost_per_ft_usd and sourcing_risk are team-editable procurement facts, not
    physics — they default from the 06/29 meeting notes and are labelled
    judgement until the team overwrites them with a real quote.
    """
    key: str                      # "A", "B", "C", or a custom label
    od_mm: float
    wall_mm: float
    cost_per_ft_usd: float = 0.0
    sourcing_risk: str = ""       # free text, e.g. "seamless 1.2 mm is scarce"
    cost_is_estimate: bool = True

    # -- section properties -------------------------------------------------- #
    @property
    def id_mm(self) -> float:
        return self.od_mm - 2.0 * self.wall_mm

    @property
    def area_mm2(self) -> float:
        return math.pi / 4.0 * (self.od_mm ** 2 - self.id_mm ** 2)

    @property
    def I_mm4(self) -> float:
        """Second moment of area about a diameter (the bending axis)."""
        return math.pi / 64.0 * (self.od_mm ** 4 - self.id_mm ** 4)

    @property
    def S_mm3(self) -> float:
        """Elastic section modulus I / c."""
        return self.I_mm4 / (self.od_mm / 2.0)

    @property
    def EI_Nmm2(self) -> float:
        return STEEL_E_MPA * self.I_mm4

    @property
    def mass_per_m_kg(self) -> float:
        return self.area_mm2 * 1e-6 * STEEL_RHO_KG_M3

    def mass_kg(self, length_mm: float) -> float:
        return self.mass_per_m_kg * length_mm / 1000.0

    def cost_usd(self, length_mm: float) -> float:
        return self.cost_per_ft_usd * length_mm / MM_PER_FT

    def as_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d) -> "TubeSpec":
        valid = TubeSpec.__dataclass_fields__.keys()
        return TubeSpec(**{k: v for k, v in dict(d).items() if k in valid})


def default_size_table() -> Dict[str, TubeSpec]:
    """The team's three size classes, priced from the 06/29 meeting.

    A = 25.4 × 2.4  (hoops / shoulder-harness bar class)
    B = 25.4 × 1.65 (side impact / bulkhead / bracing / harness class)
    C = 25.4 × 1.2  (bulkhead support / bracing support class)

    Meeting note verbatim: "When 1.2 mm tubing is offered, it is close to
    $10/ft" and it must be SEAMLESS, which is what makes it scarce.  A and B
    prices are placeholder judgement figures — overwrite with real quotes
    (Motivo / CSULA's supplier).
    """
    return {
        "A": TubeSpec("A", 25.4, 2.40, cost_per_ft_usd=6.50,
                      sourcing_risk="", cost_is_estimate=True),
        "B": TubeSpec("B", 25.4, 1.65, cost_per_ft_usd=5.50,
                      sourcing_risk="", cost_is_estimate=True),
        "C": TubeSpec("C", 25.4, 1.20, cost_per_ft_usd=10.00,
                      sourcing_risk="seamless 1.2 mm wall is hard to source "
                                    "(most suppliers stock welded, not "
                                    "drawn-over-mandrel seamless)",
                      cost_is_estimate=True),
    }


# Member class -> minimum size-class key. Transcribed from the classic FSAE
# steel-tube table; ALWAYS re-verify against the competition-year rulebook.
MEMBER_CLASS_MIN_SIZE: Dict[str, str] = {
    "main_hoop":                 "A",
    "front_hoop":                "A",
    "shoulder_harness_bar":      "A",
    "side_impact":               "B",
    "front_bulkhead":            "B",
    "roll_hoop_bracing":         "B",
    "harness_attachment":        "B",
    "accumulator_protection":    "B",   # EV
    "front_bulkhead_support":    "C",
    "main_hoop_bracing_support": "C",
    "ts_component_protection":   "C",   # EV
    "non_structural":            "C",   # bodywork tabs etc. — no rules floor,
                                        # C used as the practical minimum stock
}

MEMBER_CLASS_LABELS: Dict[str, str] = {
    "main_hoop":                 "Main hoop",
    "front_hoop":                "Front hoop",
    "shoulder_harness_bar":      "Shoulder harness bar",
    "side_impact":               "Side impact structure",
    "front_bulkhead":            "Front bulkhead",
    "roll_hoop_bracing":         "Roll hoop bracing",
    "harness_attachment":        "Harness attachment (non-shoulder)",
    "accumulator_protection":    "Accumulator protection (EV)",
    "front_bulkhead_support":    "Front bulkhead support",
    "main_hoop_bracing_support": "Main hoop bracing support",
    "ts_component_protection":   "TS component protection (EV)",
    "non_structural":            "Non-structural / bodywork",
}

_HOOP_CLASSES = {"main_hoop", "front_hoop"}


def size_meets_minimum(spec: TubeSpec, member_class: str,
                       table: Optional[Dict[str, TubeSpec]] = None) -> bool:
    """True if `spec` is at or above the member class's minimum size class.

    Same-OD comparison: wall must not be thinner than the class minimum and OD
    must not be smaller.  Different-OD alternatives go through
    `equivalency_check` instead — this is the fast path for the A/B/C family.
    """
    table = table or default_size_table()
    min_key = MEMBER_CLASS_MIN_SIZE.get(member_class, "C")
    ref = table.get(min_key) or default_size_table()[min_key]
    return spec.wall_mm >= ref.wall_mm - 1e-9 and spec.od_mm >= ref.od_mm - 1e-9


def equivalency_check(candidate: TubeSpec, member_class: str,
                      table: Optional[Dict[str, TubeSpec]] = None) -> dict:
    """Screen an alternative OD × wall against the rules baseline for a class.

    Rulebook framing (alternative steel tubing): the candidate must have
    bending stiffness E·I and bending strength NOT LESS than the specified
    baseline, and wall thickness not below the absolute floor (2.0 mm for
    hoops / harness attachment, 1.2 mm elsewhere).  Bending strength is
    compared as section modulus S (same steel both sides, so yield cancels).

    Pre-validation screen only — a rules-officer question ends at the actual
    rulebook text, not here.
    """
    table = table or default_size_table()
    min_key = MEMBER_CLASS_MIN_SIZE.get(member_class, "C")
    base = table.get(min_key) or default_size_table()[min_key]

    floor = (MIN_WALL_HOOP_MM
             if (member_class in _HOOP_CLASSES
                 or member_class in ("shoulder_harness_bar",
                                     "harness_attachment"))
             else MIN_WALL_OTHER_MM)

    ei_ratio = candidate.EI_Nmm2 / base.EI_Nmm2 if base.EI_Nmm2 else float("inf")
    s_ratio = candidate.S_mm3 / base.S_mm3 if base.S_mm3 else float("inf")
    wall_ok = candidate.wall_mm >= floor - 1e-9
    passes = ei_ratio >= 1.0 - 1e-9 and s_ratio >= 1.0 - 1e-9 and wall_ok

    return {
        "member_class": member_class,
        "baseline": base.as_dict(),
        "candidate": candidate.as_dict(),
        "EI_ratio": ei_ratio,
        "bending_strength_ratio": s_ratio,
        "wall_floor_mm": floor,
        "wall_ok": wall_ok,
        "mass_per_m_delta_kg": candidate.mass_per_m_kg - base.mass_per_m_kg,
        "passes": passes,
        "note": RULES_DISCLAIMER,
    }


# --------------------------------------------------------------------------- #
#  Frame graph
# --------------------------------------------------------------------------- #
@dataclass
class FrameNode:
    nid: str
    xyz_mm: Tuple[float, float, float]
    label: str = ""

    def as_dict(self):
        return {"nid": self.nid, "xyz_mm": list(self.xyz_mm),
                "label": self.label}

    @staticmethod
    def from_dict(d) -> "FrameNode":
        return FrameNode(str(d["nid"]), tuple(float(v) for v in d["xyz_mm"]),
                         str(d.get("label", "")))


@dataclass
class FrameTube:
    name: str
    a: str                        # node id
    b: str                        # node id
    member_class: str = "non_structural"
    size: str = "C"               # key into the size table
    is_primary: bool = True       # counts for triangulation / load-path audits

    def as_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d) -> "FrameTube":
        valid = FrameTube.__dataclass_fields__.keys()
        return FrameTube(**{k: v for k, v in dict(d).items() if k in valid})


class FrameGraph:
    """The space frame as nodes + tubes, with the audits the meeting asked for."""

    def __init__(self, size_table: Optional[Dict[str, TubeSpec]] = None):
        self.nodes: Dict[str, FrameNode] = {}
        self.tubes: List[FrameTube] = []
        self.size_table: Dict[str, TubeSpec] = size_table or default_size_table()

    # ---- construction ---------------------------------------------------- #
    def add_node(self, nid: str, xyz_mm, label: str = "") -> FrameNode:
        n = FrameNode(str(nid), tuple(float(v) for v in xyz_mm), label)
        self.nodes[n.nid] = n
        return n

    def add_tube(self, name: str, a: str, b: str, *,
                 member_class: str = "non_structural", size: str = "C",
                 is_primary: bool = True) -> FrameTube:
        if a not in self.nodes or b not in self.nodes:
            raise KeyError(f"tube {name!r}: unknown node ({a!r} or {b!r})")
        if a == b:
            raise ValueError(f"tube {name!r}: both ends on node {a!r}")
        t = FrameTube(str(name), str(a), str(b), member_class, size, is_primary)
        self.tubes.append(t)
        return t

    # ---- geometry --------------------------------------------------------- #
    def p(self, nid: str) -> np.ndarray:
        return np.asarray(self.nodes[nid].xyz_mm, float)

    def length_mm(self, tube: FrameTube) -> float:
        return float(np.linalg.norm(self.p(tube.b) - self.p(tube.a)))

    def spec_of(self, tube: FrameTube) -> TubeSpec:
        try:
            return self.size_table[tube.size]
        except KeyError:
            raise KeyError(f"tube {tube.name!r} uses unknown size "
                           f"{tube.size!r}; known: {sorted(self.size_table)}")

    # ---- serialisation ---------------------------------------------------- #
    def as_dict(self):
        return {
            "nodes": [n.as_dict() for n in self.nodes.values()],
            "tubes": [t.as_dict() for t in self.tubes],
            "size_table": {k: v.as_dict() for k, v in self.size_table.items()},
        }

    @staticmethod
    def from_dict(d) -> "FrameGraph":
        st = {k: TubeSpec.from_dict(v)
              for k, v in (d.get("size_table") or {}).items()}
        g = FrameGraph(size_table=st or None)
        for nd in d.get("nodes", []):
            n = FrameNode.from_dict(nd)
            g.nodes[n.nid] = n
        for td in d.get("tubes", []):
            g.tubes.append(FrameTube.from_dict(td))
        return g

    @staticmethod
    def from_csv(nodes_csv: str, tubes_csv: str,
                 size_table: Optional[Dict[str, TubeSpec]] = None) -> "FrameGraph":
        """Build a frame from two CSV texts.

        nodes CSV columns: id, x, y, z[, label]
        tubes CSV columns: name, a, b[, class][, size][, primary]

        Column names are case-insensitive; `class` accepts either the key
        ("side_impact") or the label ("Side impact structure").
        """
        g = FrameGraph(size_table=size_table)
        label_to_key = {v.lower(): k for k, v in MEMBER_CLASS_LABELS.items()}

        for row in csv.DictReader(io.StringIO(nodes_csv.strip())):
            row = {(k or "").strip().lower(): (v or "").strip()
                   for k, v in row.items()}
            g.add_node(row["id"], (float(row["x"]), float(row["y"]),
                                   float(row["z"])), row.get("label", ""))

        for row in csv.DictReader(io.StringIO(tubes_csv.strip())):
            row = {(k or "").strip().lower(): (v or "").strip()
                   for k, v in row.items()}
            cls = row.get("class", "non_structural")
            cls = label_to_key.get(cls.lower(), cls.lower().replace(" ", "_"))
            if cls not in MEMBER_CLASS_MIN_SIZE:
                cls = "non_structural"
            prim = row.get("primary", "1").lower() not in ("0", "false", "no", "n")
            g.add_tube(row["name"], row["a"], row["b"], member_class=cls,
                       size=row.get("size", "C") or "C", is_primary=prim)
        return g

    # ---- graph helpers ----------------------------------------------------- #
    def _adjacency(self, primary_only: bool = True) -> Dict[str, set]:
        adj: Dict[str, set] = {nid: set() for nid in self.nodes}
        for t in self.tubes:
            if primary_only and not t.is_primary:
                continue
            adj[t.a].add(t.b)
            adj[t.b].add(t.a)
        return adj

    def triangles(self, primary_only: bool = True) -> List[Tuple[str, str, str]]:
        """All 3-cycles of tubes (each triangle reported once, sorted)."""
        adj = self._adjacency(primary_only)
        tris = set()
        for a in adj:
            for b in adj[a]:
                if b <= a:
                    continue
                for c in adj[a] & adj[b]:
                    if c > b:
                        tris.add((a, b, c))
        return sorted(tris)

    def triangulated_nodes(self, primary_only: bool = True) -> set:
        out = set()
        for tri in self.triangles(primary_only):
            out.update(tri)
        return out

    # ---- audit 1: mid-span landings ---------------------------------------- #
    def midspan_landings(self, tol_mm: float = 8.0,
                         exempt_hoop_hosts: bool = True) -> List[dict]:
        """Tube ends that land on the INTERIOR of another tube, not at a node.

        This is the geometry behind "these tubes interrupting the load paths
        are illegal": a member that T-bones a STRAIGHT member mid-span puts
        bending into a tube the rules (and statics) want loaded node-to-node.
        Hoop-class hosts (main/front hoop) are exempt by default: a hoop is a
        continuous bent tube and nodes welded along its length are normal.
        One finding per (node, host) pair, listing every tube arriving there —
        the fix is to move the joint to a node or split the host at a new,
        properly-triangulated node.
        """
        node_pts = {nid: self.p(nid) for nid in self.nodes}
        grouped: Dict[Tuple[str, str], dict] = {}
        for t in self.tubes:
            for end_node in (t.a, t.b):
                pe = node_pts[end_node]
                for host in self.tubes:
                    if host is t or end_node in (host.a, host.b):
                        continue        # shares a real node — fine
                    if exempt_hoop_hosts and host.member_class in _HOOP_CLASSES:
                        continue        # continuous bent tube: nodes mid-length OK
                    pa, pb = node_pts[host.a], node_pts[host.b]
                    ab = pb - pa
                    L2 = float(ab @ ab)
                    if L2 <= 0.0:
                        continue
                    s = float((pe - pa) @ ab) / L2
                    if s <= 0.0 or s >= 1.0:
                        continue
                    d = float(np.linalg.norm(pe - (pa + s * ab)))
                    L = math.sqrt(L2)
                    # interior only: stay clear of the host's own end nodes
                    if d <= tol_mm and tol_mm < s * L < L - tol_mm:
                        key = (end_node, host.name)
                        f = grouped.setdefault(key, {
                            "end_node": end_node,
                            "host_tube": host.name,
                            "host_fraction": round(s, 3),
                            "offset_mm": round(d, 2),
                            "tubes": [],
                            "why": ("tube end lands mid-span on the host "
                                    "instead of at a shared node — this "
                                    "interrupts the host's load path"),
                        })
                        if t.name not in f["tubes"]:
                            f["tubes"].append(t.name)
        return [grouped[k] for k in sorted(grouped)]

    # ---- audit 2: untriangulated quads -------------------------------------- #
    def untriangulated_quads(self, planarity_tol_mm: float = 60.0,
                             primary_only: bool = True) -> List[dict]:
        """4-node cycles of primary tubes with NO diagonal — the open bays.

        Near-planar quads only (an open bay you'd actually brace with one
        diagonal); strongly non-planar 4-cycles are skeleton artefacts, not
        bays.  Each finding proposes the shorter diagonal, its length, and the
        mass/cost of adding it in the cheapest spec that satisfies the
        strictest member class already touching the bay.
        """
        adj = self._adjacency(primary_only)
        edge_class: Dict[frozenset, str] = {}
        for t in self.tubes:
            if primary_only and not t.is_primary:
                continue
            edge_class[frozenset((t.a, t.b))] = t.member_class

        quads = set()
        nodes = sorted(adj)
        for a in nodes:
            for b in adj[a]:
                for c in adj[b]:
                    if c in (a,) or c in adj[a]:
                        continue          # c adjacent to a ⇒ triangle, skip
                    for dn in adj[c] & adj[a]:
                        if dn in (a, b, c) or dn in adj[b]:
                            continue      # d adjacent to b ⇒ diagonal exists
                        quads.add(tuple(sorted((a, b, c, dn))))

        findings = []
        for q in sorted(quads):
            # recover the cycle order a-b-c-d for this unordered quad
            a, b, c, dn = q
            order = None
            for mid1, mid2 in ((b, c), (b, dn), (c, dn)):
                rest = [x for x in (b, c, dn) if x not in (mid1, mid2)][0]
                if (mid1 in adj[a] and mid2 in adj[a]
                        and rest in adj[mid1] and rest in adj[mid2]):
                    order = (a, mid1, rest, mid2)
                    break
            if order is None:
                continue
            pts = np.array([self.p(n) for n in order])
            centroid = pts.mean(axis=0)
            # planarity: distance of points from the best-fit plane
            _, _, vt = np.linalg.svd(pts - centroid)
            normal = vt[-1]
            dev = float(np.max(np.abs((pts - centroid) @ normal)))
            if dev > planarity_tol_mm:
                continue

            d1 = (order[0], order[2])
            d2 = (order[1], order[3])
            L1 = float(np.linalg.norm(self.p(d1[0]) - self.p(d1[1])))
            L2 = float(np.linalg.norm(self.p(d2[0]) - self.p(d2[1])))
            diag, Ld = (d1, L1) if L1 <= L2 else (d2, L2)

            # strictest class on the bay's own edges decides the diagonal spec
            classes = []
            ring = list(order) + [order[0]]
            for i in range(4):
                cls = edge_class.get(frozenset((ring[i], ring[i + 1])))
                if cls:
                    classes.append(cls)
            strict = min(classes, key=lambda c: MEMBER_CLASS_MIN_SIZE[c]) \
                if classes else "main_hoop_bracing_support"
            spec_key = MEMBER_CLASS_MIN_SIZE[strict]
            spec = self.size_table.get(spec_key, default_size_table()[spec_key])

            findings.append({
                "bay_nodes": list(order),
                "planarity_dev_mm": round(dev, 1),
                "suggested_diagonal": list(diag),
                "diagonal_length_mm": round(Ld, 1),
                "diagonal_size": spec_key,
                "diagonal_mass_kg": round(spec.mass_kg(Ld), 3),
                "diagonal_cost_usd": round(spec.cost_usd(Ld), 2),
                "governing_class": strict,
            })
        return findings

    # ---- audit 3: the meeting's actual question ----------------------------- #
    def load_path_audit(self, from_node: str, to_node: str,
                        midspan_tol_mm: float = 8.0) -> dict:
        """Is there a continuously-TRIANGULATED load path from A to B?

        Verbatim requirement from the 06/29 deck: "main hoop support needs a
        triangulated load path to the lower side impact node."  We find the
        shortest primary-tube path, then hold every node on it to the
        standard: the node participates in at least one tube triangle, and no
        traversed tube is interrupted by a mid-span landing.  Failures name
        the weak node/tube and attach the concrete fix (the missing diagonal,
        with length / spec / mass / cost) when one exists.
        """
        if from_node not in self.nodes or to_node not in self.nodes:
            raise KeyError("unknown node id")

        adj = self._adjacency(primary_only=True)

        # Dijkstra by physical length — the path load actually takes is short.
        import heapq
        dist = {from_node: 0.0}
        prev: Dict[str, str] = {}
        pq = [(0.0, from_node)]
        seen = set()
        while pq:
            d, u = heapq.heappop(pq)
            if u in seen:
                continue
            seen.add(u)
            if u == to_node:
                break
            for v in adj[u]:
                nd = d + float(np.linalg.norm(self.p(v) - self.p(u)))
                if nd < dist.get(v, float("inf")):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))

        if to_node not in seen:
            return {"connected": False, "path": [], "ok": False,
                    "weak_nodes": [], "interrupted_tubes": [],
                    "verdict": "attention",
                    "summary": (f"No primary-tube path connects "
                                f"{from_node} to {to_node} at all — the load "
                                f"has no route before triangulation is even "
                                f"the question.")}

        path = [to_node]
        while path[-1] != from_node:
            path.append(prev[path[-1]])
        path.reverse()

        tri_nodes = self.triangulated_nodes(primary_only=True)
        quads = self.untriangulated_quads()
        quad_by_node: Dict[str, dict] = {}
        for q in quads:
            for n in q["bay_nodes"]:
                quad_by_node.setdefault(n, q)

        weak = []
        for n in path:
            if n not in tri_nodes:
                fix = quad_by_node.get(n)
                weak.append({
                    "node": n,
                    "label": self.nodes[n].label,
                    "why": "node is not part of any tube triangle",
                    "fix": (dict(fix) if fix else
                            {"note": "no single open bay found at this node — "
                                     "it may need a new node or member, not "
                                     "just a diagonal"}),
                })

        # tubes actually traversed by the path
        path_edges = {frozenset((path[i], path[i + 1]))
                      for i in range(len(path) - 1)}
        landings = self.midspan_landings(tol_mm=midspan_tol_mm)
        hosts_hit = {f["host_tube"] for f in landings}  # grouped per (node, host)
        interrupted = []
        for t in self.tubes:
            if frozenset((t.a, t.b)) in path_edges and t.name in hosts_hit:
                interrupted.append({
                    "tube": t.name,
                    "why": ("a tube on the load path is T-boned mid-span — "
                            "the joint must move to a triangulated node"),
                })

        ok = not weak and not interrupted
        verdict = "works" if ok else ("look closer" if len(weak) +
                                      len(interrupted) == 1 else "attention")
        n_len = sum(float(np.linalg.norm(self.p(path[i + 1]) - self.p(path[i])))
                    for i in range(len(path) - 1))
        summary = (f"Path {' → '.join(path)} ({n_len:.0f} mm): "
                   + ("fully triangulated, no interruptions."
                      if ok else
                      f"{len(weak)} untriangulated node(s), "
                      f"{len(interrupted)} interrupted tube(s)."))
        return {"connected": True, "path": path, "path_length_mm": n_len,
                "weak_nodes": weak, "interrupted_tubes": interrupted,
                "ok": ok, "verdict": verdict, "summary": summary}

    # ---- BOM & the sourcing trade study -------------------------------------- #
    def bom_by_spec(self) -> dict:
        """Roll up length / mass / cost / tube count per size class."""
        rows: Dict[str, dict] = {}
        for t in self.tubes:
            spec = self.spec_of(t)
            L = self.length_mm(t)
            r = rows.setdefault(t.size, {
                "size": t.size, "od_mm": spec.od_mm, "wall_mm": spec.wall_mm,
                "n_tubes": 0, "length_mm": 0.0, "length_ft": 0.0,
                "mass_kg": 0.0, "cost_usd": 0.0,
                "cost_per_ft_usd": spec.cost_per_ft_usd,
                "cost_is_estimate": spec.cost_is_estimate,
                "sourcing_risk": spec.sourcing_risk,
            })
            r["n_tubes"] += 1
            r["length_mm"] += L
            r["length_ft"] += L / MM_PER_FT
            r["mass_kg"] += spec.mass_kg(L)
            r["cost_usd"] += spec.cost_usd(L)
        total = {
            "n_tubes": sum(r["n_tubes"] for r in rows.values()),
            "length_mm": sum(r["length_mm"] for r in rows.values()),
            "mass_kg": sum(r["mass_kg"] for r in rows.values()),
            "cost_usd": sum(r["cost_usd"] for r in rows.values()),
        }
        return {"by_spec": [rows[k] for k in sorted(rows)], "total": total}

    def consolidate_spec(self, from_key: str, to_key: str) -> dict:
        """The meeting's exact what-if: "increase Size C to match Size B."

        Re-specs every tube currently on `from_key` to `to_key` and reports
        the deltas.  Refuses nothing — but flags any tube whose member class
        would end up BELOW its rules minimum (only possible when consolidating
        downward, e.g. B → C), so an upward merge is always clean and a
        downward one is honest about what it breaks.
        """
        if to_key not in self.size_table:
            raise KeyError(f"unknown target size {to_key!r}")
        to_spec = self.size_table[to_key]
        changed, illegal = [], []
        d_mass = d_cost = d_len = 0.0
        for t in self.tubes:
            if t.size != from_key:
                continue
            old = self.spec_of(t)
            L = self.length_mm(t)
            dm = to_spec.mass_kg(L) - old.mass_kg(L)
            dc = to_spec.cost_usd(L) - old.cost_usd(L)
            d_mass += dm
            d_cost += dc
            d_len += L
            row = {"tube": t.name, "length_mm": round(L, 1),
                   "d_mass_kg": round(dm, 3), "d_cost_usd": round(dc, 2)}
            changed.append(row)
            if not size_meets_minimum(to_spec, t.member_class, self.size_table):
                illegal.append({**row, "member_class": t.member_class,
                                "min_size": MEMBER_CLASS_MIN_SIZE[t.member_class]})
        return {
            "from": from_key, "to": to_key,
            "n_tubes": len(changed), "length_mm": d_len,
            "delta_mass_kg": d_mass, "delta_cost_usd": d_cost,
            "tubes": changed,
            "rules_violations": illegal,
            "eliminates_spec": (len(changed) > 0 and not any(
                t.size == from_key for t in self.tubes) or
                len(changed) == sum(1 for t in self.tubes
                                    if t.size == from_key)),
            "note": ("One fewer tube spec to source is itself a procurement "
                     "win — one supplier line, one stock, no seamless-1.2 mm "
                     "hunt. " + RULES_DISCLAIMER),
        }

    def apply_consolidation(self, from_key: str, to_key: str) -> int:
        """Actually re-spec the tubes (after the team reads the trade)."""
        n = 0
        for t in self.tubes:
            if t.size == from_key:
                t.size = to_key
                n += 1
        return n

    # ---- informational per-tube structure ------------------------------------ #
    def euler_buckling_kN(self, tube: FrameTube) -> float:
        """Pinned-pinned Euler critical load — screening, not a frame FEA."""
        spec = self.spec_of(tube)
        L = self.length_mm(tube)
        if L <= 0:
            return float("inf")
        return math.pi ** 2 * spec.EI_Nmm2 / (L ** 2) / 1000.0

    def tube_table(self) -> List[dict]:
        rows = []
        for t in self.tubes:
            spec = self.spec_of(t)
            L = self.length_mm(t)
            rows.append({
                "tube": t.name, "a": t.a, "b": t.b,
                "class": MEMBER_CLASS_LABELS.get(t.member_class,
                                                 t.member_class),
                "size": t.size, "length_mm": round(L, 1),
                "mass_kg": round(spec.mass_kg(L), 3),
                "cost_usd": round(spec.cost_usd(L), 2),
                "euler_Pcr_kN": round(self.euler_buckling_kN(t), 1),
                "meets_min": size_meets_minimum(spec, t.member_class,
                                                self.size_table),
            })
        return rows


# --------------------------------------------------------------------------- #
#  Demo frame — the meeting's exact situation, in tube form
# --------------------------------------------------------------------------- #
def demo_frame() -> FrameGraph:
    """A recognisable half-car side structure with slide 4's exact defects.

    Front bulkhead → front hoop → main hoop side elevation with upper/lower
    side-impact members, main hoop bracing, and — deliberately — (1) an OPEN
    BAY between the main-hoop-support node and the lower side-impact node
    (no diagonal: the illegal-in-2027 condition) and (2) one support tube
    that T-bones the upper side-impact member mid-span.  Run the audits and
    both findings surface with their fixes; add the suggested diagonal and
    the load-path check goes green.  Left side only — audits are per-side.
    """
    g = FrameGraph()
    y = -280.0  # left side plane
    # x rear, z up (mm) — proportions in the ballpark of a real FSAE frame
    g.add_node("FBL", (0, y, 60),    "Front bulkhead lower")
    g.add_node("FBU", (0, y, 380),   "Front bulkhead upper")
    g.add_node("FHL", (620, y, 60),  "Front hoop lower")
    g.add_node("FHU", (620, y, 700), "Front hoop top (side)")
    g.add_node("SIL", (1180, y, 60), "Lower side impact node")     # ← slide 4
    g.add_node("SIU", (1180, y, 330),"Upper side impact node")
    g.add_node("MHL", (1560, y, 60), "Main hoop lower")
    g.add_node("MHS", (1560, y, 330),"Main hoop support node")     # ← slide 4
    g.add_node("MHU", (1560, y, 1120),"Main hoop top (side)")
    g.add_node("MBR", (2020, y, 620),"Main hoop brace rear")
    # the mid-span offender: a tab tube landing on the interior of SIU–MHS
    g.add_node("BAD", (1370, y, 330),"Mid-span landing (illegal)")
    g.add_node("BADT",(1370, y, 620),"Support tube top")

    A = dict(member_class="front_bulkhead", size="B")
    g.add_tube("fb_lower",  "FBL", "FBU", **A)
    g.add_tube("fbs_low",   "FBL", "FHL", member_class="front_bulkhead_support", size="C")
    g.add_tube("fbs_up",    "FBU", "FHU", member_class="front_bulkhead_support", size="C")
    g.add_tube("fbs_diag",  "FBU", "FHL", member_class="front_bulkhead_support", size="C")
    g.add_tube("fh_side",   "FHL", "FHU", member_class="front_hoop", size="A")
    g.add_tube("si_low_f",  "FHL", "SIL", member_class="side_impact", size="B")
    g.add_tube("si_up_f",   "FHL", "SIU", member_class="side_impact", size="B")  # diagonal, triangulates front bay with si_low_f? needs FHL-SIU + SIL-SIU
    g.add_tube("si_vert",   "SIL", "SIU", member_class="side_impact", size="B")
    g.add_tube("si_low_r",  "SIL", "MHL", member_class="side_impact", size="B")
    # upper side impact runs SIU → MHS in two pieces via the BAD landing zone:
    g.add_tube("si_up_r",   "SIU", "MHS", member_class="side_impact", size="B")
    g.add_tube("mh_side",   "MHL", "MHU", member_class="main_hoop", size="A")
    g.add_tube("mh_low_tie","MHL", "MHS", member_class="side_impact", size="B")
    g.add_tube("mh_brace",  "MHU", "MBR", member_class="roll_hoop_bracing", size="B")
    g.add_tube("mh_brace_s","MBR", "MHS", member_class="main_hoop_bracing_support", size="C")
    # the offender: BAD sits ON si_up_r's interior; this tube T-bones it
    g.add_tube("bad_support","BAD", "BADT", member_class="main_hoop_bracing_support", size="C")
    # NOTE the open bay: SIL–SIU–MHS–MHL has NO diagonal → MHS is on no
    # triangle on the rear bay side, and the MHS → SIL load path fails.
    return g


DEMO_PATH_FROM = "MHS"   # main hoop support node
DEMO_PATH_TO = "SIL"     # lower side impact node


# --------------------------------------------------------------------------- #
#  Panels & attachments — the four subteams' to-do list, computed
# --------------------------------------------------------------------------- #
@dataclass
class PanelMaterial:
    name: str
    E_MPa: float
    rho_kg_m3: float


PANEL_MATERIALS: Dict[str, PanelMaterial] = {
    "Aluminium sheet (6061)":      PanelMaterial("Aluminium sheet (6061)", 69000.0, 2700.0),
    "Carbon laminate (quasi-iso)": PanelMaterial("Carbon laminate (quasi-iso)", 45000.0, 1550.0),
    "GFRP / fibreglass":           PanelMaterial("GFRP / fibreglass", 20000.0, 1850.0),
    "Steel sheet (firewall)":      PanelMaterial("Steel sheet (firewall)", 205000.0, 7850.0),
    "Polycarbonate":               PanelMaterial("Polycarbonate", 2300.0, 1200.0),
}

PANEL_KIND_NOTES: Dict[str, str] = {
    "aero": ("Aero panel / bodywork: no rules load case, but bodywork must "
             "stay attached and stable at top speed — the pitch answer below "
             "IS aero's 'how close together do the mounting points need to "
             "be'. Quick-release is legal and normal here."),
    "floor": ("Chassis floor closeout: must cover the cockpit floor per the "
              "cockpit rules; quick-release is fine but the panel may not "
              "gap open under driver + inertial load. Check the panel also "
              "against the driver's heels standing on it."),
    "firewall": ("Firewall: must SEAL — non-permeable, no gaps or unsealed "
                 "pass-throughs between driver and accumulator/TS per the "
                 "rulebook. A quick-release firewall must still land "
                 "compressed on its sealing flange at every fastener; that "
                 "is what sets the pitch, not strength. Baja's firewall is "
                 "the Wednesday reference — same physics."),
    "seat": ("Seat panel/shell: keep it removable (meeting requirement). "
             "The seat mount carries driver inertial load; the HARNESS — not "
             "the seat — takes the crash deceleration, so size seat mounts "
             "for the g-cases below and harness tabs separately."),
}

# Screening capacities for the fastener families the subteams shortlisted.
# TYPICAL published single-fastener values — judgement figures. Confirm
# against the vendor datasheet for the exact part before manufacture.
FASTENER_OPTIONS: List[dict] = [
    {"name": "Quarter-turn (Dzus-type), steel",
     "shear_N": 1500.0, "tension_N": 900.0, "quick_release": True,
     "note": "The classic bodywork fastener; needs a spring + receptacle "
             "riveted to the frame side."},
    {"name": "Camloc 1/4-turn, aerospace",
     "shear_N": 2200.0, "tension_N": 1300.0, "quick_release": True,
     "note": "Stronger and more expensive than Dzus; positive retention."},
    {"name": "M5 8.8 bolt + rivnut (Al)",
     "shear_N": 5200.0, "tension_N": 2500.0, "quick_release": False,
     "note": "Tension limited by rivnut thread pull-out in aluminium, not "
             "the bolt. Tooled removal — not quick-release."},
    {"name": "M6 8.8 bolt + welded nutplate",
     "shear_N": 7400.0, "tension_N": 6000.0, "quick_release": False,
     "note": "The harness/seat-grade option; nutplate welds to the tube."},
    {"name": "Rubber-buffered pin + lanyard (aero)",
     "shear_N": 600.0, "tension_N": 300.0, "quick_release": True,
     "note": "Trailing-edge/secondary retention only — never primary."},
]


@dataclass
class PanelPlan:
    kind: str
    n_fasteners: int
    pitch_mm: float
    perimeter_mm: float
    pressure_kPa: float
    panel_mass_kg: float
    load_per_fastener_N: float
    deflection_mm: float
    deflection_limit_mm: float
    max_pitch_mm: float
    options: List[dict] = field(default_factory=list)
    verdict: str = "look closer"
    notes: str = ""

    def as_dict(self):
        return asdict(self)


def dynamic_pressure_kPa(speed_kph: float, cp: float = 1.0,
                         rho_air: float = 1.225) -> float:
    v = speed_kph / 3.6
    return 0.5 * rho_air * v * v * cp / 1000.0


def plan_panel_attachment(kind: str, width_mm: float, height_mm: float,
                          thickness_mm: float, material: str,
                          pitch_mm: float,
                          pressure_kPa: Optional[float] = None,
                          speed_kph: Optional[float] = None,
                          cp: float = 1.2,
                          g_load: float = 3.0,
                          deflection_limit_mm: Optional[float] = None,
                          fos_target: float = 3.0) -> PanelPlan:
    """Answer the aero-panels to-do with numbers, for any panel kind.

    Loads: a uniform pressure (given directly, or computed from top speed via
    q = ½ρv²·Cp for bodywork — Cp defaults conservatively to 1.2 for a stag-
    nation-ish worst patch) PLUS the panel's own inertial load at `g_load`
    (road vibration / kerb strikes; 3 g is a common screening figure —
    judgement, not a rulebook number).

    Fasteners: distributed around the perimeter at `pitch_mm` (min 4).  Per-
    fastener load is the total divided evenly — a screening idealisation; the
    corner fasteners of a stiff panel see more, which is what `fos_target`
    (default 3) is for.

    Stiffness between fasteners: the panel strip between two adjacent
    fasteners is modelled as a simply-supported beam of unit width under the
    pressure, w = 5qL⁴/(384EI), I = t³/12 per unit width.  `max_pitch_mm`
    inverts that at the deflection limit (default span/100, floored at 1 mm)
    — this is the direct answer to "how close together do the mounting points
    need to be to keep bodywork stable."
    """
    if material not in PANEL_MATERIALS:
        raise KeyError(f"unknown panel material {material!r}")
    mat = PANEL_MATERIALS[material]

    if pressure_kPa is None:
        pressure_kPa = (dynamic_pressure_kPa(speed_kph, cp)
                        if speed_kph else 0.0)
    q_Pa = pressure_kPa * 1000.0

    area_m2 = (width_mm / 1000.0) * (height_mm / 1000.0)
    panel_mass = area_m2 * thickness_mm / 1000.0 * mat.rho_kg_m3

    perimeter = 2.0 * (width_mm + height_mm)
    n = max(4, int(math.ceil(perimeter / max(pitch_mm, 1.0))))

    F_pressure = q_Pa * area_m2
    F_inertial = panel_mass * 9.81 * g_load
    per_fastener = (F_pressure + F_inertial) / n

    # strip deflection between adjacent fasteners
    I_per_mm = thickness_mm ** 3 / 12.0                # mm^4 per mm width
    q_per_mm = q_Pa * 1e-6 + panel_mass * 9.81 * g_load / max(area_m2, 1e-9) * 1e-6
    # q in N/mm per mm-wide strip (pressure + smeared inertial, N/mm²·mm)
    L = pitch_mm
    w = 5.0 * q_per_mm * L ** 4 / (384.0 * mat.E_MPa * I_per_mm) \
        if q_per_mm > 0 else 0.0

    limit = deflection_limit_mm if deflection_limit_mm is not None \
        else max(1.0, pitch_mm / 100.0)
    if q_per_mm > 0:
        max_pitch = (384.0 * mat.E_MPa * I_per_mm * limit
                     / (5.0 * q_per_mm)) ** 0.25
    else:
        max_pitch = float("inf")

    # fastener screening — tension governs bodywork under suction/pressure;
    # use the smaller of shear/tension capacity for a direction-agnostic screen
    options = []
    for opt in FASTENER_OPTIONS:
        cap = min(opt["shear_N"], opt["tension_N"])
        fos = cap / per_fastener if per_fastener > 0 else float("inf")
        options.append({
            **opt,
            "capacity_N": cap,
            "fos": round(fos, 2) if math.isfinite(fos) else float("inf"),
            "ok": fos >= fos_target,
            "capacity_is_judgement": True,
        })

    stiff_ok = w <= limit + 1e-9
    any_ok = any(o["ok"] for o in options)
    verdict = "works" if (stiff_ok and any_ok) else (
        "look closer" if (stiff_ok or any_ok) else "attention")

    return PanelPlan(
        kind=kind, n_fasteners=n, pitch_mm=pitch_mm, perimeter_mm=perimeter,
        pressure_kPa=pressure_kPa, panel_mass_kg=panel_mass,
        load_per_fastener_N=per_fastener, deflection_mm=w,
        deflection_limit_mm=limit, max_pitch_mm=max_pitch,
        options=options, verdict=verdict,
        notes=PANEL_KIND_NOTES.get(kind, ""))


# ---- seat & harness ---------------------------------------------------------- #
def harness_attachment_loads(driver_mass_kg: float = 77.0,
                             decel_g: float = 20.0,
                             torso_fraction: float = 0.60,
                             shoulder_angle_deg: float = 10.0,
                             lap_angle_deg: float = 55.0,
                             n_shoulder: int = 2, n_lap: int = 2,
                             n_antisub: int = 2) -> dict:
    """Per-attachment-point belt loads at a frontal deceleration.

    The screening statics the seat & harness subteam needs before CAD: at
    `decel_g` the driver's inertial force m·g·decel splits between the
    shoulder straps (torso share) and lap straps (pelvis share), each acting
    along its belt line, so the ATTACHMENT sees the belt tension =
    share / (n straps · cos(belt angle from horizontal)).  Anti-submarine
    straps are sized to a fraction of lap tension (judgement: 0.5).

    Numbers to treat as judgement and verify: 20 g is a common screening
    deceleration for FSAE-scale frontal cases, 77 kg is a mid-percentile
    driver + gear, torso fraction 0.6.  The rulebook governs WHERE these
    mount (shoulder straps to the shoulder-harness-bar class tube; lap and
    antisub to harness-attachment class structure) — this function sizes the
    tab, the rulebook sites it.

    Output includes `bracket_ready`: per-point loads shaped for the Brakes
    tab's bolt & bracket FoS screen (`suspension.bracket_fos.Bracket.P_N`).
    """
    F_total = driver_mass_kg * 9.81 * decel_g
    F_torso = F_total * torso_fraction
    F_pelvis = F_total * (1.0 - torso_fraction)

    T_shoulder = F_torso / (n_shoulder * max(math.cos(
        math.radians(shoulder_angle_deg)), 1e-6))
    T_lap = F_pelvis / (n_lap * max(math.cos(
        math.radians(lap_angle_deg)), 1e-6))
    T_antisub = 0.5 * T_lap if n_antisub else 0.0

    points = [
        {"point": "Shoulder strap (each)", "n": n_shoulder,
         "belt_tension_N": T_shoulder,
         "mounts_to": "shoulder harness bar (size-A class tube)"},
        {"point": "Lap belt (each)", "n": n_lap,
         "belt_tension_N": T_lap,
         "mounts_to": "harness-attachment class structure"},
    ]
    if n_antisub:
        points.append({"point": "Anti-submarine (each)", "n": n_antisub,
                       "belt_tension_N": T_antisub,
                       "mounts_to": "harness-attachment class structure"})

    return {
        "decel_g": decel_g, "driver_mass_kg": driver_mass_kg,
        "F_total_N": F_total,
        "points": points,
        "bracket_ready": [{"name": p["point"], "P_N": round(
            p["belt_tension_N"], 0), "load_is_shear": False}
            for p in points],
        "note": ("Screening statics with judgement inputs — the rulebook "
                 "sites the mounts and may require attachment-strength "
                 "proof; run each tab through the bolt & bracket FoS screen "
                 "with these P_N values, then confirm per the rulebook."),
    }


def seat_mount_check(seat_mass_kg: float, driver_mass_kg: float,
                     n_mounts: int = 4,
                     g_vertical: float = 3.0, g_lateral: float = 2.0,
                     g_longitudinal: float = 2.0,
                     fastener: str = "M6 8.8 bolt + welded nutplate",
                     fos_target: float = 3.0) -> dict:
    """Removable-seat mount screening — per-mount load and fastener verdict.

    The seat carries driver + seat inertial loads at everyday g-levels (the
    harness, not the seat, takes the crash case — see
    `harness_attachment_loads`).  g defaults are screening judgement figures.
    Quick-release seat hardware trades capacity for removability; the verdict
    tells the subteam whether the shortlisted fastener keeps the seat
    removable AND strong enough, which is the meeting's exact brief.
    """
    m = seat_mass_kg + driver_mass_kg
    F_res = m * 9.81 * math.sqrt(g_vertical ** 2 + g_lateral ** 2
                                 + g_longitudinal ** 2)
    per_mount = F_res / max(n_mounts, 1)

    opt = next((o for o in FASTENER_OPTIONS if o["name"] == fastener), None)
    rows = []
    for o in FASTENER_OPTIONS:
        cap = min(o["shear_N"], o["tension_N"])
        fos = cap / per_mount if per_mount > 0 else float("inf")
        rows.append({**o, "capacity_N": cap, "fos": round(fos, 2),
                     "ok": fos >= fos_target})
    chosen = next((r for r in rows if r["name"] == fastener), None)
    verdict = "works" if (chosen and chosen["ok"]) else (
        "look closer" if any(r["ok"] for r in rows) else "attention")

    return {
        "combined_mass_kg": m, "resultant_g": math.sqrt(
            g_vertical ** 2 + g_lateral ** 2 + g_longitudinal ** 2),
        "F_resultant_N": F_res, "n_mounts": n_mounts,
        "load_per_mount_N": per_mount,
        "chosen": chosen, "options": rows, "verdict": verdict,
        "note": ("Everyday-g seat mount screen; crash load lives in the "
                 "harness tabs, not here. Capacities are judgement — "
                 "verify with vendor data. Keep at least one quick-release "
                 "option green to satisfy 'keep seat removable'."),
    }


# --------------------------------------------------------------------------- #
#  Integration-ledger hook
# --------------------------------------------------------------------------- #
def frame_summary_for_ledger(g: FrameGraph) -> dict:
    """What the chassis subteam declares from this tool: frame tube mass."""
    bom = g.bom_by_spec()
    return {
        "frame_tube_mass_kg": round(bom["total"]["mass_kg"], 2),
        "frame_tube_cost_usd": round(bom["total"]["cost_usd"], 2),
        "n_tubes": bom["total"]["n_tubes"],
        "is_estimate": any(r["cost_is_estimate"] for r in bom["by_spec"]),
        "source": "Chassis tab — Frame Planner BOM",
    }
