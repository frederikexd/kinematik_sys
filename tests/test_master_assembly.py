# Tests for suspension/master_assembly.py + the units-aware 3D axes.
import numpy as np
import pytest

from suspension import master_assembly as ma


# ---------------------------------------------------------------- frames ----
def test_slot_frame_orthonormal_right_handed():
    a = np.array([[0, 0, 300.0], [20, 10, 80.0], [0, 250, 190.0]])
    c, R, err = ma.slot_frame(a, axis_pair=(0, 1), secondary=2)
    assert err is None
    assert np.allclose(R.T @ R, np.eye(3), atol=1e-9)
    assert np.linalg.det(R) == pytest.approx(1.0, abs=1e-9)
    # primary axis is the normalised anchor difference
    u = (a[0] - a[1]) / np.linalg.norm(a[0] - a[1])
    assert np.allclose(R[:, 0], u)


def test_slot_frame_degenerate_collinear():
    a = np.array([[0, 0, 0.0], [0, 0, 100.0], [0, 0, 50.0]])  # collinear
    _, R, err = ma.slot_frame(a)
    assert err == "degenerate_frame"
    assert np.allclose(R, np.eye(3))          # renderable fallback, flagged


# ---------------------------------------------------------------- dummies ---
def _upright_slot():
    return ma.SlotDef("susp.fl.upright", "suspension",
                      ("upper_outer", "lower_outer", "wheel_center"))


def test_envelope_dummy_extents_and_margin():
    sd = _upright_slot()
    a = np.array([[0, 0, 300.0], [0, 0, 80.0], [0, 40, 190.0]])
    f = ma.fit_envelope_dummy(a, sd)
    # kingpin span 220 mm + 2*margin along the primary axis
    assert f.size[0] == pytest.approx(220 + 2 * sd.margin_mm)
    # lateral span 40 mm (wheel_center offset) + margins... projected in w
    assert f.size[2] >= 40.0
    # empty axis: raw extent 0 + 2*margin = 16 mm > min_dim 15 → no clamp
    assert f.size[1] == pytest.approx(2 * sd.margin_mm)
    assert not any(fl.kind == "scale_clamped" for fl in f.flags)
    assert np.linalg.det(f.rotation) == pytest.approx(1.0, abs=1e-9)
    # shrink the margin so the raw extent dips under min_dim → clamp + flag
    sd2 = _upright_slot()
    sd2.margin_mm = 2.0
    f2 = ma.fit_envelope_dummy(a, sd2)
    assert f2.size[1] == pytest.approx(sd2.min_dim_mm)
    assert any(fl.kind == "scale_clamped" for fl in f2.flags)


def test_envelope_dummy_tracks_hardpoint_edit():
    sd = _upright_slot()
    a = np.array([[0, 0, 300.0], [0, 0, 80.0], [0, 40, 190.0]])
    f0 = ma.fit_envelope_dummy(a, sd)
    a2 = a.copy()
    a2[0, 2] += 50.0                           # raise upper_outer 50 mm
    f1 = ma.fit_envelope_dummy(a2, sd)
    assert f1.size[0] - f0.size[0] == pytest.approx(50.0, abs=1e-6)


def test_bridge_dummy_spans_gap_exactly():
    sd = ma.SlotDef("susp.fl.pushrod", "suspension",
                    ("pushrod_outer", "rocker_pushrod"), kind="bridge",
                    bridge_radius_mm=9.0)
    p0, p1 = np.array([0, 500, 150.0]), np.array([100, 200, 400.0])
    f = ma.fit_bridge_dummy(p0, p1, sd)
    ell = np.linalg.norm(p1 - p0)
    assert f.size[2] == pytest.approx(ell)
    assert np.allclose(f.center, (p0 + p1) / 2)
    # local +z maps onto the span direction
    assert np.allclose(f.rotation @ np.array([0, 0, 1.0]),
                       (p1 - p0) / ell, atol=1e-9)


def test_bridge_dummy_antiparallel_guard():
    sd = ma.SlotDef("s", "suspension", ("a", "b"), kind="bridge")
    f = ma.fit_bridge_dummy([0, 0, 100.0], [0, 0, 0.0], sd)  # straight down
    assert np.allclose(f.rotation @ np.array([0, 0, 1.0]), [0, 0, -1],
                       atol=1e-9)
    assert np.linalg.det(f.rotation) == pytest.approx(1.0, abs=1e-9)


# ------------------------------------------------------------ registration --
def test_kabsch_recovers_known_rigid_transform():
    rng = np.random.default_rng(7)
    q = rng.uniform(-100, 100, (5, 3))
    th = 0.7
    Rz = np.array([[np.cos(th), -np.sin(th), 0],
                   [np.sin(th), np.cos(th), 0], [0, 0, 1.0]])
    t = np.array([12.0, -400.0, 55.0])
    p = q @ Rz.T + t
    reg = ma.register_part(q, p, unit_scale=1.0)
    assert reg.residual_mm < 1e-9
    assert reg.confidence == "solved"
    assert np.allclose(reg.rotation, Rz, atol=1e-9)
    assert np.allclose(reg.apply(q), p, atol=1e-9)


def test_kabsch_unit_scale_gltf_metres():
    q_m = np.array([[0, 0, 0.0], [0.1, 0, 0], [0, 0.2, 0]])   # metres
    p_mm = 1000.0 * q_m + np.array([5.0, 5.0, 5.0])
    reg = ma.register_part(q_m, p_mm, unit_scale=1000.0)
    assert reg.residual_mm < 1e-6
    assert reg.uniform_scale == 1000.0


def test_kabsch_rejects_reflection():
    q = np.array([[1, 0, 0.0], [0, 1, 0], [0, 0, 1], [1, 1, 1]])
    p = q.copy()
    p[:, 0] *= -1                               # mirrored correspondences
    reg = ma.register_part(q, p)
    assert np.linalg.det(reg.rotation) == pytest.approx(1.0, abs=1e-9)


def test_misfit_flag_names_worst_anchor():
    sd = _upright_slot()
    q = np.array([[0, 0, 0.0], [0, 0, -220.0], [0, 40, -110.0]])
    p = q + np.array([0, 0, 300.0])            # perfect fit ...
    p[2] += np.array([0, 0, 4.2])              # ... except wheel_center, +4.2 z
    reg = ma.register_part(q, p)
    fl = ma.check_registration(reg, sd, anchor_names=sd.anchor_keys)
    assert fl is not None and fl.kind == "hardpoint_mismatch"
    assert fl.detail["worst_anchor"] == "wheel_center"
    assert fl.detail["residual_mm"] > sd.fit_tol_mm


def test_within_tolerance_no_flag():
    sd = _upright_slot()
    q = np.array([[0, 0, 0.0], [0, 0, -220.0], [0, 40, -110.0]])
    reg = ma.register_part(q, q + np.array([0, 0, 300.0]))
    assert ma.check_registration(reg, sd) is None


# --------------------------------------------------------------------- FSM --
def test_fsm_happy_path_and_quarantine():
    s = ma.DUMMY
    for ev in ("upload_start", "upload_ok", "register_ok", "validate_pass"):
        s = ma.advance(s, ev)
    assert s == ma.TRUE_CAD
    s = ma.advance(s, "anchors_out_of_tol")
    assert s == ma.MISFIT
    s = ma.advance(s, "anchors_in_tol")
    assert s == ma.TRUE_CAD
    assert ma.advance(ma.REGISTERING, "register_fail") == ma.QUARANTINED
    assert ma.advance(ma.QUARANTINED, "revert") == ma.DUMMY
    # stray events are no-ops
    assert ma.advance(ma.DUMMY, "validate_pass") == ma.DUMMY


# --------------------------------------------------------------------- ACI --
def test_aci_volume_weighted_with_kappa():
    rows = [
        dict(slot_key="a", volume_mm3=1000.0, criticality=1.0,
             state=ma.TRUE_CAD, reg_confidence="solved"),
        dict(slot_key="b", volume_mm3=1000.0, criticality=1.0,
             state=ma.DUMMY),
        dict(slot_key="c", volume_mm3=2000.0, criticality=1.0,
             state=ma.TRUE_CAD, reg_confidence="low_confidence"),
    ]
    r = ma.assembly_completion_index(rows)
    assert r["aci"] == pytest.approx((1000 * 1.0 + 2000 * 0.75) / 4000.0)
    assert r["n_cad"] == 2 and r["n_slots"] == 3


def test_aci_empty_is_zero_not_nan():
    r = ma.assembly_completion_index([])
    assert r["aci"] == 0.0


def test_aci_from_part_boxes_bridge():
    boxes = {"dummy radiator": dict(centre=[0, 0, 0], size=[100, 100, 100]),
             "dummy battery": dict(centre=[0, 0, 0], size=[100, 100, 100])}
    parts = [dict(name="real radiator", l_mm=100, w_mm=100, h_mm=100,
                  fit_ok=True, replaces_drawnames=["dummy radiator"])]
    r = ma.aci_from_part_boxes(boxes, parts)
    assert r["aci"] == pytest.approx(0.5)     # half the volume is true CAD
    assert r["n_slots"] == 2


# ------------------------------------------------------------ interference --
def test_aabb_overlap_and_allowlist():
    f1 = ma.SlotFit("a", "envelope", np.zeros(3), np.eye(3),
                    np.array([100.0, 100, 100]))
    f2 = ma.SlotFit("b", "envelope", np.array([50.0, 0, 0]), np.eye(3),
                    np.array([100.0, 100, 100]))
    f3 = ma.SlotFit("c", "envelope", np.array([500.0, 0, 0]), np.eye(3),
                    np.array([100.0, 100, 100]))
    flags = ma.aabb_overlaps([f1, f2, f3])
    assert [fl for fl in flags if fl.detail["other_slot"] in ("b",)]
    assert not [fl for fl in flags
                if "c" in (fl.slot_key, fl.detail["other_slot"])]
    assert ma.aabb_overlaps([f1, f2, f3], allow_pairs=[("a", "b")]) == []


# --------------------------------------------------------------- commits ----
def test_commit_hash_stable_and_order_invariant():
    e1 = [dict(slot_key="a", occupancy="cad", part_sha256="deadbeef"),
          dict(slot_key="b", occupancy="dummy",
               dummy_params=dict(l_mm=1, w_mm=2, h_mm=3))]
    h1 = ma.commit_hash(e1)
    h2 = ma.commit_hash(list(reversed(e1)))
    assert h1 == h2 and len(h1) == 64
    e2 = [dict(e1[0], part_sha256="cafebabe"), e1[1]]
    assert ma.commit_hash(e2) != h1


# --------------------------------------------------- corner catalog / eval --
def _corner_points():
    return {
        "upper_front_inner": (250, 220, 300), "upper_rear_inner": (-250, 220, 300),
        "upper_outer": (0, 560, 320), "lower_front_inner": (250, 200, 120),
        "lower_rear_inner": (-250, 200, 120), "lower_outer": (0, 580, 130),
        "wheel_center": (0, 610, 228), "tie_rod_inner": (80, 210, 180),
        "tie_rod_outer": (60, 570, 190), "pushrod_outer": (0, 540, 150),
        "rocker_pushrod": (0, 260, 420), "rocker_pivot": (0, 230, 380),
        "rocker_spring": (0, 180, 430), "spring_inner": (0, 40, 400),
    }


def test_evaluate_corner_full_and_partial():
    fits, flags = ma.evaluate_corner("fl", _corner_points())
    assert len(fits) == 7                      # all catalog slots fitted
    assert not [f for f in flags if f.severity == "block"]
    # a partial table degrades to a partial dummy set, never an error
    pts = _corner_points()
    del pts["rocker_pivot"]
    fits2, _ = ma.evaluate_corner("fl", pts)
    assert len(fits2) == 6
    assert "susp.fl.rocker" not in [f.slot_key for f in fits2]


# --------------------------------------------- imperial axes in fullcar3d ---
def test_units_axis_cfg_metric_vs_imperial(monkeypatch):
    from suspension import fullcar3d as fc
    from suspension import units
    import streamlit as st

    st.session_state["unit_system"] = units.METRIC
    m = fc._units_axis_cfg("x (rear ←→ front)", 0.0, 3000.0)
    assert "[mm]" in m["title"]
    assert m["tickvals"] == m and False or m["tickvals"]  # non-empty
    assert m["tickvals"] == [float(t) for t in m["tickvals"]]

    st.session_state["unit_system"] = units.US
    us = fc._units_axis_cfg("x (rear ←→ front)", 0.0, 3000.0)
    assert "[in]" in us["title"]
    # tick POSITIONS stay in mm; labels are round inches
    ratios = [tv / 25.4 for tv in us["tickvals"] if tv != 0]
    for r, txt in zip(ratios, [t for v, t in zip(us["tickvals"],
                                                 us["ticktext"]) if v != 0]):
        assert float(txt) == pytest.approx(r, abs=1e-6)
    st.session_state["unit_system"] = units.METRIC


def test_units_axis_cfg_degenerate_extent_falls_back():
    from suspension import fullcar3d as fc
    cfg = fc._units_axis_cfg("z (up)", float("nan"), float("nan"))
    assert "title" in cfg and "tickvals" not in cfg
