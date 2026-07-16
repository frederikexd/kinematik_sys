# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""Tests for suspension/tubeframe.py — Frame Planner backend."""

import math
import pytest

from suspension import tubeframe as tf


# ---------------------------------------------------------------- specs ---- #
def test_spec_section_properties():
    c = tf.default_size_table()["C"]
    assert c.od_mm == 25.4 and c.wall_mm == 1.2
    assert c.id_mm == pytest.approx(23.0)
    area = math.pi / 4 * (25.4 ** 2 - 23.0 ** 2)
    assert c.area_mm2 == pytest.approx(area)
    assert c.mass_per_m_kg == pytest.approx(area * 1e-6 * tf.STEEL_RHO_KG_M3)
    # a foot of tube costs its per-ft price
    assert c.cost_usd(tf.MM_PER_FT) == pytest.approx(c.cost_per_ft_usd)


def test_size_meets_minimum_family():
    t = tf.default_size_table()
    assert tf.size_meets_minimum(t["A"], "main_hoop")
    assert not tf.size_meets_minimum(t["B"], "main_hoop")
    assert tf.size_meets_minimum(t["B"], "side_impact")
    assert not tf.size_meets_minimum(t["C"], "side_impact")
    assert tf.size_meets_minimum(t["B"], "main_hoop_bracing_support")  # upward ok


def test_equivalency_check_directions():
    # bigger OD, thinner wall: EI/S up, wall floor still respected for non-hoop
    ok = tf.equivalency_check(tf.TubeSpec("X", 28.0, 1.6), "side_impact")
    assert ok["passes"] and ok["EI_ratio"] > 1.0 and ok["wall_ok"]
    # same OD thinner wall than baseline: fails strength/stiffness
    bad = tf.equivalency_check(tf.TubeSpec("Y", 25.4, 1.4), "side_impact")
    assert not bad["passes"]
    # hoop wall floor is 2.0 mm even if EI passes via big OD
    hoop = tf.equivalency_check(tf.TubeSpec("Z", 45.0, 1.6), "main_hoop")
    assert not hoop["wall_ok"] and not hoop["passes"]


# ---------------------------------------------------------------- graph ---- #
def _square(diag=False):
    g = tf.FrameGraph()
    for nid, xyz in [("a", (0, 0, 0)), ("b", (500, 0, 0)),
                     ("c", (500, 0, 500)), ("d", (0, 0, 500))]:
        g.add_node(nid, xyz)
    for i, (u, v) in enumerate([("a", "b"), ("b", "c"), ("c", "d"), ("d", "a")]):
        g.add_tube(f"e{i}", u, v, member_class="side_impact", size="B")
    if diag:
        g.add_tube("diag", "a", "c", member_class="side_impact", size="B")
    return g


def test_untriangulated_quad_found_and_fixed():
    g = _square(diag=False)
    q = g.untriangulated_quads()
    assert len(q) == 1
    assert sorted(q[0]["bay_nodes"]) == ["a", "b", "c", "d"]
    # suggested diagonal is one of the two, with length ≈ 500√2
    assert q[0]["diagonal_length_mm"] == pytest.approx(500 * math.sqrt(2), rel=1e-3)
    assert q[0]["diagonal_size"] == "B"  # governed by side_impact edges
    g2 = _square(diag=True)
    assert g2.untriangulated_quads() == []
    assert {"a", "b", "c", "d"} <= g2.triangulated_nodes()


def test_load_path_audit_square():
    g = _square(diag=False)
    r = g.load_path_audit("a", "c")
    assert r["connected"] and not r["ok"]
    assert {w["node"] for w in r["weak_nodes"]} >= {"a", "c"} - set()
    g.add_tube("diag", "b", "d", member_class="side_impact", size="B")
    r2 = g.load_path_audit("a", "c")
    assert r2["ok"] and r2["verdict"] == "works"


def test_load_path_disconnected():
    g = _square()
    g.add_node("island", (9000, 0, 0))
    g.add_node("island2", (9500, 0, 0))
    g.add_tube("iso", "island", "island2")
    r = g.load_path_audit("a", "island")
    assert not r["connected"] and not r["ok"]


def test_midspan_landing_detection_and_hoop_exemption():
    g = tf.FrameGraph()
    g.add_node("a", (0, 0, 0)); g.add_node("b", (1000, 0, 0))
    g.add_node("m", (400, 0, 0)); g.add_node("t", (400, 0, 300))
    g.add_tube("host", "a", "b", member_class="side_impact", size="B")
    g.add_tube("tbone", "m", "t", member_class="main_hoop_bracing_support")
    f = g.midspan_landings()
    assert len(f) == 1 and f[0]["host_tube"] == "host"
    assert f[0]["end_node"] == "m" and f[0]["tubes"] == ["tbone"]
    # a hoop host is a continuous bent tube — exempt by default
    g.tubes[0].member_class = "main_hoop"
    assert g.midspan_landings() == []
    assert len(g.midspan_landings(exempt_hoop_hosts=False)) == 1


def test_demo_frame_reproduces_slide4():
    g = tf.demo_frame()
    landings = g.midspan_landings()
    assert [(f["end_node"], f["host_tube"]) for f in landings] == \
        [("BAD", "si_up_r")]
    audit = g.load_path_audit(tf.DEMO_PATH_FROM, tf.DEMO_PATH_TO)
    assert not audit["ok"] and audit["verdict"] in ("look closer", "attention")
    weak = {w["node"] for w in audit["weak_nodes"]}
    assert "MHS" in weak                    # the slide's exact node
    # apply the suggested fix on the MHS bay → path goes green
    fix = next(w["fix"] for w in audit["weak_nodes"] if w["node"] == "MHS")
    d = fix["suggested_diagonal"]
    g.add_tube("fix", d[0], d[1], member_class="side_impact", size="B")
    assert g.load_path_audit("MHS", "SIL")["ok"]


# ------------------------------------------------------------------ BOM ---- #
def test_bom_and_consolidation():
    g = tf.demo_frame()
    bom = g.bom_by_spec()
    assert bom["total"]["n_tubes"] == len(g.tubes)
    mass_sum = sum(r["mass_kg"] for r in bom["by_spec"])
    assert bom["total"]["mass_kg"] == pytest.approx(mass_sum)
    trade = g.consolidate_spec("C", "B")
    assert trade["n_tubes"] == sum(1 for t in g.tubes if t.size == "C")
    assert trade["delta_mass_kg"] > 0          # thicker wall = heavier
    assert trade["rules_violations"] == []     # upward merge always legal
    # per-tube deltas sum to totals
    assert sum(r["d_mass_kg"] for r in trade["tubes"]) == \
        pytest.approx(trade["delta_mass_kg"], abs=0.01)
    # downward merge flags every rules-floored tube
    down = g.consolidate_spec("B", "C")
    assert down["delta_mass_kg"] < 0
    assert any(v["member_class"] == "side_impact"
               for v in down["rules_violations"])
    n = g.apply_consolidation("C", "B")
    assert n == trade["n_tubes"] and not any(t.size == "C" for t in g.tubes)


def test_csv_roundtrip():
    nodes = "id,x,y,z,label\na,0,0,0,A\nb,500,0,0,B\nc,500,0,500,C\n"
    tubes = ("name,a,b,class,size,primary\n"
             "e0,a,b,Side impact structure,B,1\n"
             "e1,b,c,side_impact,B,yes\n")
    g = tf.FrameGraph.from_csv(nodes, tubes)
    assert len(g.nodes) == 3 and len(g.tubes) == 2
    assert all(t.member_class == "side_impact" for t in g.tubes)
    g2 = tf.FrameGraph.from_dict(g.as_dict())
    assert g2.as_dict() == g.as_dict()


def test_euler_buckling_scaling():
    g = _square()
    p500 = g.euler_buckling_kN(g.tubes[0])
    # halve the length → 4× critical load
    g.nodes["b"].xyz_mm = (250, 0, 0)
    assert g.euler_buckling_kN(g.tubes[0]) == pytest.approx(4 * p500, rel=1e-6)


# --------------------------------------------------------------- panels ---- #
def test_panel_plan_physics():
    p = tf.plan_panel_attachment("aero", 900, 450, 2.0,
                                 "Carbon laminate (quasi-iso)", 150.0,
                                 speed_kph=110)
    assert p.pressure_kPa == pytest.approx(
        tf.dynamic_pressure_kPa(110, 1.2), rel=1e-9)
    assert p.n_fasteners == math.ceil(2 * (900 + 450) / 150.0)
    # deflection at max_pitch equals the limit (strip model self-consistency)
    p2 = tf.plan_panel_attachment("aero", 900, 450, 2.0,
                                  "Carbon laminate (quasi-iso)",
                                  p.max_pitch_mm, speed_kph=110,
                                  deflection_limit_mm=p.deflection_limit_mm)
    assert p2.deflection_mm == pytest.approx(p.deflection_limit_mm, rel=1e-6)
    # thicker panel → smaller deflection, larger allowable pitch (t^3, t^0.75)
    p3 = tf.plan_panel_attachment("aero", 900, 450, 3.0,
                                  "Carbon laminate (quasi-iso)", 150.0,
                                  speed_kph=110)
    assert p3.deflection_mm < p.deflection_mm
    assert p3.max_pitch_mm > p.max_pitch_mm


def test_panel_plan_verdicts_and_options():
    hurricane = tf.plan_panel_attachment("aero", 1200, 600, 1.0,
                                         "Polycarbonate", 400.0,
                                         pressure_kPa=30.0)
    assert hurricane.verdict in ("look closer", "attention")
    gentle = tf.plan_panel_attachment("floor", 600, 400, 2.0,
                                      "Aluminium sheet (6061)", 120.0,
                                      pressure_kPa=0.5)
    assert gentle.verdict == "works"
    assert any(o["quick_release"] and o["ok"] for o in gentle.options)
    assert all(o["capacity_is_judgement"] for o in gentle.options)


def test_harness_loads_statics():
    h = tf.harness_attachment_loads(driver_mass_kg=77, decel_g=20,
                                    torso_fraction=0.6,
                                    shoulder_angle_deg=0.0, n_shoulder=2)
    F = 77 * 9.81 * 20
    shoulder = next(p for p in h["points"] if "Shoulder" in p["point"])
    assert shoulder["belt_tension_N"] == pytest.approx(F * 0.6 / 2)
    assert {b["name"] for b in h["bracket_ready"]} == \
        {p["point"] for p in h["points"]}
    assert all(b["P_N"] > 0 and b["load_is_shear"] is False
               for b in h["bracket_ready"])


def test_seat_mount_check():
    s = tf.seat_mount_check(4.0, 77.0, n_mounts=4)
    F = 81 * 9.81 * math.sqrt(3 ** 2 + 2 ** 2 + 2 ** 2)
    assert s["load_per_mount_N"] == pytest.approx(F / 4)
    assert s["verdict"] == "works" and s["chosen"]["ok"]
    # 1 mount of the weakest hardware should not screen green
    weak = tf.seat_mount_check(4.0, 77.0, n_mounts=1,
                               fastener="Rubber-buffered pin + lanyard (aero)")
    assert weak["chosen"]["ok"] is False


def test_ledger_summary():
    g = tf.demo_frame()
    led = tf.frame_summary_for_ledger(g)
    assert led["n_tubes"] == len(g.tubes) and led["is_estimate"] is True
    assert led["frame_tube_mass_kg"] == pytest.approx(
        g.bom_by_spec()["total"]["mass_kg"], abs=0.01)
