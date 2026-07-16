# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================

"""
Tests for the electrics/cooling features driven by the FSAE Electrics meeting #3
slides:

  * tractive_system — the precharge R-C transient (and the slide-5 "switch shorts
    the resistor after ~2 s" experiment), the discharge bleed gate, and the
    shutdown chain / TSAL / BSPD validators.
  * pcm_cooling — the latent-heat "liquid wax" buffer on the 140s3p pack, its
    melt-out hold time, and the inverse wax-sizing helper.

Every model is analytic or a deterministic post-process, so the asserts are exact.
"""

import warnings
import numpy as np

from suspension.interfaces import Severity
from suspension.tractive_system import (
    Rules, PrechargeCircuit, simulate_precharge, check_precharge,
    ShutdownNode, ShutdownChain, check_shutdown_chain,
    TSAL, check_tsal, BSPD, check_bspd, check_tractive_system,
)
from suspension.pack_thermal import (
    default_cell_params, PackLayout, PackThermalModel, AirflowParams,
)
from suspension.pcm_cooling import (
    default_pcm, PCMAllocation, evaluate_pcm_buffer, size_pcm_for_hold, check_pcm,
)


def _sev(findings, check):
    return next(f.severity for f in findings if f.check == check)


# --------------------------------------------------------------------------- #
#  Precharge (slides 4 & 5)
# --------------------------------------------------------------------------- #
def test_precharge_time_constant_and_energy():
    pc = PrechargeCircuit(pack_voltage_v=400.0, link_capacitance_f=600e-6,
                          precharge_r_ohm=50.0, discharge_r_ohm=8000.0)
    # tau = R*C
    assert abs(pc.tau_precharge_s - 0.03) < 1e-9
    # pulse energy is 1/2 C V^2 regardless of R
    assert abs(pc.precharge_pulse_energy_j - 0.5 * 600e-6 * 400.0**2) < 1e-6
    # time to 90% = -tau ln(0.1)
    assert abs(pc.time_to_fraction_s(0.90) + pc.tau_precharge_s * np.log(0.1)) < 1e-9


def test_precharge_short_switch_snaps_to_source():
    # slide-5 experiment: switch shorts R at t=2s, V_cap -> V_pack
    pc = PrechargeCircuit(pack_voltage_v=300.0, link_capacitance_f=1e-3,
                          precharge_r_ohm=100.0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        tr = simulate_precharge(pc, t_switch_s=2.0)
    assert tr.ok
    i_sw = int(np.searchsorted(tr.time_s, 2.0))
    # before the switch it is still charging (well under V)
    assert tr.v_cap_v[i_sw - 1] < 300.0
    # after the short it reaches the source
    assert tr.v_cap_v[-1] > 299.0
    # resistor current is zeroed once bypassed
    assert tr.i_a[-1] == 0.0


def test_precharge_too_slow_fails():
    # huge R -> precharge takes far longer than the 5s rule -> FAIL
    pc = PrechargeCircuit(pack_voltage_v=400.0, link_capacitance_f=600e-6,
                          precharge_r_ohm=100000.0, discharge_r_ohm=8000.0)
    assert _sev(check_precharge(pc), "precharge-time") == Severity.FAIL


def test_discharge_missing_when_no_bleed_resistor():
    pc = PrechargeCircuit(pack_voltage_v=400.0, link_capacitance_f=600e-6,
                          precharge_r_ohm=50.0, discharge_r_ohm=None)
    assert _sev(check_precharge(pc), "discharge-time") == Severity.MISSING


def test_discharge_too_slow_fails():
    # 15k bleed on 600uF -> tau=9s, way over the 5s safe-discharge limit
    pc = PrechargeCircuit(pack_voltage_v=400.0, link_capacitance_f=600e-6,
                          precharge_r_ohm=50.0, discharge_r_ohm=15000.0)
    assert _sev(check_precharge(pc), "discharge-time") == Severity.FAIL


def test_resistor_energy_gate():
    pc = PrechargeCircuit(pack_voltage_v=400.0, link_capacitance_f=600e-6,
                          precharge_r_ohm=50.0, discharge_r_ohm=4000.0,
                          resistor_energy_rating_j=10.0)  # pulse is 48J > 10J
    assert _sev(check_precharge(pc), "precharge-resistor-energy") == Severity.FAIL


# --------------------------------------------------------------------------- #
#  Shutdown chain / TSAL / BSPD (slides 3 & 8)
# --------------------------------------------------------------------------- #
def _full_chain():
    c = ShutdownChain()
    for k in ("master_switch", "bspd", "ams", "imd", "interlock", "inertia", "estop"):
        c.add(ShutdownNode(name=k, kind=k, normally_closed=True, is_estimate=False))
    return c


def test_complete_failsafe_chain_passes():
    findings = check_shutdown_chain(_full_chain())
    assert _sev(findings, "shutdown-completeness") == Severity.OK
    assert _sev(findings, "shutdown-failsafe") == Severity.OK
    assert _sev(findings, "shutdown-single-fault") == Severity.OK


def test_missing_required_node_fails():
    c = ShutdownChain(nodes=[n for n in _full_chain().nodes if n.kind != "imd"])
    f = check_shutdown_chain(c)
    assert _sev(f, "shutdown-completeness") == Severity.FAIL


def test_normally_open_node_is_not_failsafe():
    c = _full_chain()
    c.nodes[1].normally_closed = False
    assert _sev(check_shutdown_chain(c), "shutdown-failsafe") == Severity.FAIL


def test_single_fault_opens_chain():
    c = _full_chain()
    # any single node opening must open the loop
    assert c.is_closed() is True
    assert c.is_closed(open_nodes=["imd"]) is False


def test_tsal_band_and_threshold():
    good = check_tsal(TSAL(flash_hz=3.0, safe_threshold_v=55.0))
    assert _sev(good, "tsal-flash") == Severity.OK
    assert _sev(good, "tsal-threshold") == Severity.OK
    bad = check_tsal(TSAL(flash_hz=8.0, safe_threshold_v=90.0))
    assert _sev(bad, "tsal-flash") == Severity.FAIL
    assert _sev(bad, "tsal-threshold") == Severity.FAIL


def test_bspd_reaction_gate():
    assert _sev(check_bspd(BSPD(10, 5000, 0.3)), "bspd-reaction") == Severity.OK
    assert _sev(check_bspd(BSPD(10, 5000, 0.9)), "bspd-reaction") == Severity.FAIL


def test_bspd_trips_logic():
    b = BSPD(brake_threshold=10.0, power_threshold_w=5000.0, reaction_time_s=0.2)
    assert b.trips(brake_signal=20.0, tractive_power_w=8000.0) is True
    assert b.trips(brake_signal=2.0, tractive_power_w=8000.0) is False
    assert b.trips(brake_signal=20.0, tractive_power_w=100.0) is False


def test_full_gate_summary_and_hard_fail():
    pc = PrechargeCircuit(pack_voltage_v=400.0, link_capacitance_f=600e-6,
                          precharge_r_ohm=50.0, discharge_r_ohm=15000.0)
    res = check_tractive_system(
        precharge=pc, chain=_full_chain(),
        tsal=TSAL(flash_hz=8.0, safe_threshold_v=90.0),
        bspd=BSPD(10, 5000, 0.9))
    assert res.has_hard_fail()
    assert "fail" in res.summary().lower()


def test_nothing_declared_warns_not_crashes():
    res = check_tractive_system()
    assert res.findings == []
    assert res.warnings


# --------------------------------------------------------------------------- #
#  PCM cooling (slides 3 & 7)
# --------------------------------------------------------------------------- #
def _bare_pack_result(peak_target_hot=True):
    cell = default_cell_params()
    layout = PackLayout(rows=20, cols=21, series=140, parallel=3, cell=cell,
                        ambient_c=35.0)
    t = np.linspace(0, 120, 1200)
    cur = 120 + 240 * np.clip(np.sin(2 * np.pi * t / 8), 0, 1)
    model = PackThermalModel(layout=layout, fans=[], airflow=AirflowParams())
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = model.simulate(t, cur, n_laps=3)
    return res, layout


def test_pcm_140s3p_layout_is_420_cells():
    _, layout = _bare_pack_result()
    assert layout.n_cells == 420


def test_more_wax_holds_longer():
    res, layout = _bare_pack_result()
    pcm = default_pcm()
    holds = []
    for mass in (5.0, 15.0, 40.0):
        pr = evaluate_pcm_buffer(res, layout,
                                 PCMAllocation(material=pcm, mass_per_cell_g=mass))
        assert pr.ok
        holds.append(pr.hold_time_s if pr.hold_time_s is not None else 1e9)
    # monotonic: more wax -> longer (or equal) hold
    assert holds[0] <= holds[1] <= holds[2]


def test_zero_wax_has_no_buffer():
    res, layout = _bare_pack_result()
    pr = evaluate_pcm_buffer(res, layout,
                             PCMAllocation(material=default_pcm(), mass_per_cell_g=0.0))
    assert pr.latent_buffer_j_per_cell == 0.0
    assert pr.fully_melted is True


def test_pcm_buffer_energy_is_mass_times_latent():
    pcm = default_pcm()
    alloc = PCMAllocation(material=pcm, mass_per_cell_g=20.0)
    assert abs(alloc.latent_buffer_j_per_cell() - 20.0 * pcm.latent_heat_j_per_g) < 1e-9


def test_uncalibrated_pcm_is_flagged_synthesized():
    res, layout = _bare_pack_result()
    pr = evaluate_pcm_buffer(res, layout,
                             PCMAllocation(material=default_pcm(), mass_per_cell_g=15.0))
    assert pr.synthesized is True
    assert "SYNTHESIZED" in pr.provenance


def test_inverse_sizing_returns_mass_and_volume():
    res, layout = _bare_pack_result()
    sz = size_pcm_for_hold(res, layout, default_pcm(), hold_time_s=120.0)
    assert sz["ok"]
    assert sz["pcm_mass_per_cell_g"] > 0
    assert sz["pack_pcm_mass_kg"] > 0
    assert sz["n_cells"] == 420


def test_check_pcm_emits_findings():
    res, layout = _bare_pack_result()
    pr = evaluate_pcm_buffer(res, layout,
                             PCMAllocation(material=default_pcm(), mass_per_cell_g=15.0))
    findings = check_pcm(pr, endurance_time_s=300.0)
    assert findings
    assert any(f.check == "pcm-hold" for f in findings)


def test_effective_cp_inflates_in_melt_window():
    pcm = default_pcm()
    cp_cold = pcm.effective_cp_j_per_gk(pcm.t_melt_c - 10)
    cp_melt = pcm.effective_cp_j_per_gk(pcm.t_melt_c)
    # inside the window the apparent cp is much larger (the latent slug)
    assert cp_melt > 5 * cp_cold
