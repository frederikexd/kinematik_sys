# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""Tests for suspension/release_gate.py — deterministic verdicts, fail-on-
missing-evidence, single-defect veto, slotted-K torque windows, and the
IFF contract: the clipboard PDF exists exactly when the gate is green.
Run: python tests/test_release_gate.py"""

import copy
import importlib
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _load(name):
    # Import through the real (lazy) package so pytest and standalone runs see
    # the same module objects — no stub `suspension` in sys.modules.
    return importlib.import_module(f"suspension.{name}")


BJ = _load("bolted_joint")
RE = _load("risk_engine")
RG = _load("release_gate")

_PASS, _FAIL = [], []


def check(name, cond):
    (_PASS if cond else _FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


slotted = RE.analyze_slotted_joint(
    RE.SlottedHoleJoint(fastener=BJ.Fastener(grade="10.9", nominal_d_mm=6.0),
                        slot_width_mm=6.6, slot_length_mm=20.0, washer_od_mm=18.0),
    assembly_torque_Nm=16.0)


def green_inputs():
    return RG.GateInputs(
        chassis_quads=[], chassis_loadpath_findings=[],
        manifold_dp_kpa={"radiator_in": 14.0, "motor_branch": 22.0,
                         "inverter_branch": 9.0},
        pump_head_kpa=55.0, inlet_margin_kpa=38.0,
        brake_fos={"caliper bracket": 2.1, "rotor thermal": 1.9,
                   "pedal-box base": 2.4},
        pedal_joint=slotted,
        pedal_slip_demand_N=0.35 * slotted.F_clamp_at_torque_N / 1.5,
        torque_specs=[
            RG.TorqueSpec("caliper bracket bolts", "brakes/FL", 8.0, "10.9",
                          spec_torque_Nm=26.0, K=0.20, qty=2),
            RG.TorqueSpec("pedal tab (slotted)", "brakes/pedalbox", 6.0, "10.9",
                          spec_torque_Nm=16.0, slotted=slotted, qty=2,
                          thread_locker="243"),
            RG.TorqueSpec("motor mount", "powertrain/mount", 10.0, "12.9",
                          spec_torque_Nm=52.0, K=0.18, qty=4),
        ],
        required_fastener_locations=["brakes/FL", "brakes/pedalbox",
                                     "powertrain/mount"],
        team="Elbee Racing", car="EB-26", event="FSAE Michigan")


# --- green car releases, deterministically ----------------------------------- #
gi = green_inputs()
rep = RG.run_gate(gi)
check("fully green ledger releases", rep.released, )
check("verdict deterministic", RG.run_gate(green_inputs()).as_dict()
      == rep.as_dict())
check("all check ids unique", len({c.check_id for c in rep.checks})
      == len(rep.checks))

# --- IFF: any single defect vetoes ------------------------------------------- #
def mutate(fn):
    g = green_inputs()
    fn(g)
    return RG.run_gate(g)


check("open quad vetoes", not mutate(lambda g: g.chassis_quads.append(
    {"nodes": ("n1", "n2", "n3", "n4")})).released)
check("load-path defect vetoes", not mutate(
    lambda g: g.chassis_loadpath_findings.append("untriangulated node n7")).released)
check("manifold ΔP breach vetoes", not mutate(
    lambda g: g.manifold_dp_kpa.__setitem__("motor_branch", 48.0)).released)
check("pump budget breach vetoes", not mutate(
    lambda g: setattr(g, "pump_head_kpa", 40.0)).released)
check("thin vapor margin vetoes", not mutate(
    lambda g: setattr(g, "inlet_margin_kpa", 8.0)).released)
check("brake FoS breach vetoes", not mutate(
    lambda g: g.brake_fos.__setitem__("caliper bracket", 1.3)).released)
check("under-clamped pedal tab vetoes", not mutate(
    lambda g: setattr(g, "pedal_slip_demand_N",
                      slotted.F_clamp_at_torque_N)).released)
check("uncovered fastener location vetoes", not mutate(
    lambda g: g.required_fastener_locations.append("chassis/hoop")).released)
check("over-torqued spec vetoes", not mutate(
    lambda g: setattr(g.torque_specs[0], "spec_torque_Nm", 60.0)).released)
check("zero torque spec vetoes", not mutate(
    lambda g: setattr(g.torque_specs[2], "spec_torque_Nm", 0.0)).released)

# --- missing evidence fails (never silently passes) --------------------------- #
empty = RG.run_gate(RG.GateInputs())
check("empty ledger does NOT release", not empty.released)
check("missing chassis audit is a failure",
      any(c.check_id == "CH-01" and not c.passed for c in empty.checks))
check("missing pedal joint analysis is a failure",
      any(c.check_id == "BR-02" and not c.passed for c in empty.checks))

# --- slotted torque windows use K_eff + bearing cap ---------------------------- #
ts = green_inputs().torque_specs[1]
lo, hi = ts.torque_window_Nm()
plain_hi = 0.78 * BJ.BOLT_GRADES["10.9"].proof_MPa * BJ.METRIC_COARSE[6.0][1] \
    * slotted.K_eff * 6.0 / 1e3
check("slotted window ceiling ≤ bolt-only ceiling", hi <= plain_hi + 1e-9)
check("slotted window uses K_eff",
      abs(lo - slotted.K_eff * 0.50 * BJ.BOLT_GRADES["10.9"].proof_MPa
          * BJ.METRIC_COARSE[6.0][1] * 6.0 / 1e3) < 1e-6)
check("unknown grade fails its torque line", not RG.run_gate(
    RG.GateInputs(chassis_quads=[], chassis_loadpath_findings=[],
                  manifold_dp_kpa={"a": 5.0}, pump_head_kpa=55.0,
                  inlet_margin_kpa=30.0, brake_fos={"x": 2.0},
                  pedal_joint=slotted, pedal_slip_demand_N=10.0,
                  torque_specs=[RG.TorqueSpec("mystery", "x", 6.0, "9.9",
                                              spec_torque_Nm=9.0)])).released)

# --- clipboard: exists IFF released ------------------------------------------- #
try:
    RG.build_clipboard(RG.run_gate(RG.GateInputs()), RG.GateInputs())
    check("clipboard refused on red gate", False)
except RG.GateNotPassed:
    check("clipboard refused on red gate", True)

clip = RG.build_clipboard(rep, gi)
check("clipboard has all four sections + torque rows",
      len(clip.sections) == 3 and len(clip.torque_rows) == 3
      and len(clip.gate_summary) == len(rep.checks))
check("slotted row flagged in torque table",
      any("slotted" in r["K"] for r in clip.torque_rows))

with tempfile.TemporaryDirectory() as td:
    pdf_ok = True
    try:
        path = os.path.join(td, "clipboard.pdf")
        RG.render_clipboard_pdf(clip, path)
        with open(path, "rb") as f:
            head = f.read(5)
        check("pdf rendered", head == b"%PDF-")
        check("pdf non-trivial", os.path.getsize(path) > 2000)
    except ImportError:
        print("  SKIP  reportlab not installed — pdf render skipped")
    r2, p2 = RG.release_and_print(green_inputs(), os.path.join(td, "c2.pdf"))
    check("release_and_print green ⇒ pdf path", r2.released and p2
          and os.path.exists(p2))
    r3, p3 = RG.release_and_print(RG.GateInputs(), os.path.join(td, "c3.pdf"))
    check("release_and_print red ⇒ no pdf", not r3.released and p3 is None
          and not os.path.exists(os.path.join(td, "c3.pdf")))

print(f"\n{len(_PASS)} passed, {len(_FAIL)} failed")


# --- pytest bridge: expose every module-level check as a test case ---------- #
import pytest  # noqa: E402


@pytest.mark.parametrize("name", _PASS + _FAIL)
def test_check(name):
    assert name not in _FAIL, f"check failed: {name}"


if __name__ == "__main__":
    sys.exit(1 if _FAIL else 0)
