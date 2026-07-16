"""Tests for the cross-discipline risk propagation module.

Verifies the module binds correctly to KinematiK's real interfaces.py and
dfmea.py, that propagation directions and confidence tags are honest, and that
the DFMEA-delta mapping recomputes RPN through dfmea.compute_rpn (never hand-
typed).
"""
import pytest

from suspension.interfaces import (
    SubsystemInterface, IntegrationLedger, blank_ledger,
)
from suspension import dfmea
from suspension.risk_propagation import (
    propagate_change, propagate_interface_edit, dfmea_deltas,
    build_propagation_markdown, coupling_catalog,
    Direction, Confidence, COUPLINGS,
)


def _ledger_with_limits():
    led = blank_ledger()
    led.driveline_torque_limit_nm = 200.0
    led.upright_design_load_n = 8000.0
    led.total_cooling_airflow_cms = 0.20
    led.lv_supply_capacity_w = 600.0
    led.set(SubsystemInterface(name="suspension", mount_load_n=7000.0))
    return led


# --------------------------------------------------------------------------- #
def test_mass_increase_is_worse_and_measured():
    led = _ledger_with_limits()
    effects = propagate_change(led, "chassis", "mass_kg", 30.0, 35.0)
    assert effects, "mass change should propagate"
    e = effects[0]
    assert e.direction == Direction.WORSE
    assert e.confidence == Confidence.MEASURED
    # 0.03 s/lap/kg * +5 kg
    assert e.delta_value == pytest.approx(0.15, rel=1e-6)
    assert e.delta_unit == "s/lap"


def test_mass_decrease_flips_to_better():
    led = _ledger_with_limits()
    effects = propagate_change(led, "chassis", "mass_kg", 35.0, 30.0)
    assert effects[0].direction == Direction.BETTER
    assert effects[0].delta_value == pytest.approx(-0.15, rel=1e-6)


def test_torque_over_driveline_limit_is_worse():
    led = _ledger_with_limits()  # limit 200
    effects = propagate_change(led, "powertrain", "peak_torque_nm", 150.0, 230.0)
    # find the driveline self-edge
    dl = [e for e in effects if e.target_subsystem == "powertrain"
          and "Driveshaft" in " ".join(e.dfmea_failure_modes)]
    assert dl, "expected a driveline coupling"
    assert dl[0].direction == Direction.WORSE
    assert dl[0].delta_value == pytest.approx(-30.0)  # 200 - 230


def test_torque_under_limit_is_safe_margin():
    led = _ledger_with_limits()
    effects = propagate_change(led, "powertrain", "peak_torque_nm", 100.0, 150.0)
    dl = [e for e in effects if e.target_subsystem == "powertrain"][0]
    assert dl.delta_value == pytest.approx(50.0)  # positive margin


def test_solver_demotes_to_coupled_when_data_missing():
    led = blank_ledger()  # no driveline limit declared
    effects = propagate_change(led, "powertrain", "peak_torque_nm", 100.0, 150.0)
    dl = [e for e in effects if e.target_subsystem == "powertrain"][0]
    # solver couldn't run -> must NOT claim MEASURED
    assert dl.confidence == Confidence.COUPLED
    assert "backing data" in dl.detail.get("note", "")


def test_downforce_is_the_rare_better_edge():
    led = _ledger_with_limits()
    led.set(SubsystemInterface(name="aerodynamics",
                               downforce_n_at_v=(500.0, 15.0)))
    effects = propagate_change(led, "aerodynamics", "downforce_n_at_v",
                               (400.0, 15.0), (500.0, 15.0))
    grip = [e for e in effects if "grip" in e.mechanism.lower()]
    assert grip and grip[0].direction == Direction.BETTER


def test_unknown_channel_propagates_nothing():
    led = _ledger_with_limits()
    assert propagate_change(led, "powertrain", "notes", "a", "b") == []


def test_propagate_interface_edit_diffs_and_chains():
    led = _ledger_with_limits()
    old = SubsystemInterface(name="powertrain", peak_torque_nm=150.0, mass_kg=25.0)
    new = SubsystemInterface(name="powertrain", peak_torque_nm=230.0, mass_kg=28.0)
    report = propagate_interface_edit(led, old, new)
    assert len(report.changes) == 2
    assert report.effects
    s = report.summary()
    assert s["worse"] >= 1
    assert s["worst_direction"] == "worse"


def test_dfmea_delta_recomputes_rpn_through_dfmea():
    led = _ledger_with_limits()
    # a DFMEA log containing a row whose Failure Mode a coupling touches
    records = [{
        "Failure Mode": "Driveshaft / CV failure",
        "Item / Component": "CV joint",
        "Severity": 9, "Occurrence": 4, "Detection": 6,
        "Owner": "Powertrain",
    }]
    report = propagate_interface_edit(
        led,
        SubsystemInterface(name="powertrain", peak_torque_nm=150.0),
        SubsystemInterface(name="powertrain", peak_torque_nm=230.0),
    )
    sugg = dfmea_deltas(report, records)
    assert sugg, "expected a DFMEA suggestion"
    s = sugg[0]
    # occurrence nudged up by 1, RPN recomputed by dfmea.compute_rpn
    assert s["occurrence_suggested"] == 5
    assert s["rpn_old"] == dfmea.compute_rpn(9, 4, 6)
    assert s["rpn_suggested"] == dfmea.compute_rpn(9, 5, 6)
    assert s["rpn_suggested"] > s["rpn_old"]


def test_markdown_shows_confidence_tags():
    led = _ledger_with_limits()
    report = propagate_interface_edit(
        led,
        SubsystemInterface(name="chassis", mass_kg=30.0),
        SubsystemInterface(name="chassis", mass_kg=34.0),
    )
    md = build_propagation_markdown(report, team_name="Elbee Racing", season="2026")
    assert "Elbee Racing" in md
    assert "measured" in md.lower()
    assert "Mechanism" in md


def test_catalog_covers_all_couplings():
    cat = coupling_catalog()
    assert len(cat) == len(COUPLINGS)
    # every edge names a real mechanism and a target subsystem
    for c in cat:
        assert c["mechanism"] and c["target"]


def test_every_coupling_source_channel_has_a_label():
    from suspension.risk_propagation import CHANNEL_LABELS
    for c in COUPLINGS:
        assert c.source_channel in CHANNEL_LABELS, c.source_channel
