# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
# ============================================================================
"""Tests for the flutter aero-damping co-sim seam.

Locks in the honesty contract: the reference backend is labelled trends-only and
uncorrelated, a real backend refuses to fabricate a solve, unconverged results are
flagged unusable, and a computed derivative feeds the flutter screen correctly.
"""
import json
import os
import tempfile
import pytest

from suspension.throttle_flutter_cosim import (
    OscillationCase, FlutterDerivative, FlutterProvenance, FlutterFidelity,
    FlutterSolver, QuasiSteadyFlutterModel, ExternalCFDFlutterBackend,
    SolverUnavailable, extract_flutter_derivative)
from suspension.throttle_dynamics import screen_plate_flutter, FlutterParams
from suspension.throttle_return import ThrottleInertia
from suspension.interfaces import Severity


def _case(**kw):
    base = dict(mean_angle_deg=45, amplitude_deg=2, frequency_hz=50,
                intake_speed_ms=40, plate_radius_m=0.02, plate_area_m2=1e-3)
    base.update(kw)
    return OscillationCase(**base)


# --------------------------------------------------------------------------- #
#  Protocol conformance
# --------------------------------------------------------------------------- #
def test_backends_conform_to_protocol():
    assert isinstance(QuasiSteadyFlutterModel(), FlutterSolver)
    assert isinstance(ExternalCFDFlutterBackend(), FlutterSolver)


# --------------------------------------------------------------------------- #
#  Reference backend — runnable but honestly labelled
# --------------------------------------------------------------------------- #
def test_reference_backend_runs_and_is_labelled_trends_only():
    d = extract_flutter_derivative(_case())
    assert d.c_aero_Nms is not None
    assert d.provenance.fidelity == FlutterFidelity.QUASI_STEADY
    assert d.provenance.is_correlated is False       # never claims correlation
    assert any(f.severity == Severity.WARN for f in d.findings)


def test_reference_damping_scales_and_signs_correctly():
    slow = extract_flutter_derivative(_case(intake_speed_ms=20))
    fast = extract_flutter_derivative(_case(intake_speed_ms=60))
    # quasi-steady damping magnitude grows with speed
    assert abs(fast.c_aero_Nms) > abs(slow.c_aero_Nms)
    # default layout is stabilising (positive damping)
    assert slow.c_aero_Nms > 0


def test_destabilising_layout_gives_negative_damping():
    d = extract_flutter_derivative(
        _case(), backend=QuasiSteadyFlutterModel(reduces_stability=True))
    assert d.c_aero_Nms < 0


# --------------------------------------------------------------------------- #
#  Feeding the flutter screen
# --------------------------------------------------------------------------- #
def test_derivative_feeds_flutter_screen():
    d = extract_flutter_derivative(_case())
    fp = d.to_flutter_params(k_theta_Nm_per_rad=2.0)
    assert isinstance(fp, FlutterParams)
    assert fp.c_aero_Nms == d.c_aero_Nms
    r = screen_plate_flutter(ThrottleInertia(5e-4, is_estimate=False), fp,
                             intake_speed_ms=40)
    assert r.aero_modelled is True          # screen now uses a real (co-sim) number


def test_to_flutter_params_refuses_when_no_derivative():
    d = FlutterDerivative(c_aero_Nms=None, ref_speed_ms=40)
    with pytest.raises(ValueError):
        d.to_flutter_params(k_theta_Nm_per_rad=2.0)


# --------------------------------------------------------------------------- #
#  External backend — writes input, refuses to fake a solve
# --------------------------------------------------------------------------- #
def test_external_backend_writes_case_and_refuses_to_solve():
    ext = ExternalCFDFlutterBackend(name="fluent-urans")
    wd = tempfile.mkdtemp()
    path = ext.write_case(_case(), wd)
    assert os.path.exists(path)
    with pytest.raises(SolverUnavailable):
        ext.run_case(_case(), wd)


def test_external_read_before_run_is_actionable_error():
    ext = ExternalCFDFlutterBackend(name="starccm")
    wd = tempfile.mkdtemp()
    ext.write_case(_case(), wd)
    with pytest.raises(SolverUnavailable):
        ext.read_result(_case(), wd)


def test_external_parses_converged_result_with_provenance():
    ext = ExternalCFDFlutterBackend(name="fluent-urans")
    wd = tempfile.mkdtemp()
    ext.write_case(_case(), wd)
    with open(os.path.join(wd, "fluent-urans_result.json"), "w") as f:
        json.dump({"c_aero_Nms": -3.2e-5, "converged": True,
                   "cell_count": 4_200_000, "turbulence_model": "kOmegaSST-URANS"}, f)
    d = ext.read_result(_case(), wd)
    assert d.c_aero_Nms == -3.2e-5
    assert d.provenance.converged and d.provenance.cell_count == 4_200_000
    assert d.is_usable()


def test_external_flags_unconverged_result_as_unusable():
    ext = ExternalCFDFlutterBackend(name="fluent-urans")
    wd = tempfile.mkdtemp()
    with open(os.path.join(wd, "fluent-urans_result.json"), "w") as f:
        json.dump({"c_aero_Nms": -3.2e-5, "converged": False}, f)
    d = ext.read_result(_case(), wd)
    assert not d.is_usable()
    assert any("UNCONVERGED" in f.message for f in d.findings)


def test_extract_with_external_backend_yields_none_not_fabrication():
    # extract_flutter_derivative catches SolverUnavailable and returns a None
    # derivative with an honest finding — never a fabricated number
    d = extract_flutter_derivative(_case(), backend=ExternalCFDFlutterBackend())
    assert d.c_aero_Nms is None
    assert d.findings


def test_case_artifacts_are_downloadable_ansys_formats():
    # The team has ANSYS/SolidWorks, not a JSON pipeline — the case must be offered
    # in formats they can actually run, as in-memory strings (no /tmp).
    arts = ExternalCFDFlutterBackend(name="external-urans").case_artifacts(_case())
    names = list(arts.keys())
    assert any(n.endswith(".jou") for n in names)     # Fluent journal
    assert any(n.endswith(".csv") for n in names)     # parameter table
    assert any(n.endswith(".txt") for n in names)     # setup sheet
    assert any(n.endswith(".json") for n in names)    # machine round-trip
    # every artifact is a non-empty string
    assert all(isinstance(v, str) and v.strip() for v in arts.values())


def test_case_artifacts_carry_the_real_case_numbers():
    import json as _json
    c = _case(frequency_hz=50, intake_speed_ms=40)
    arts = ExternalCFDFlutterBackend(name="external-urans").case_artifacts(c)
    # the CSV holds the actual frequency and speed
    csv = arts["external-urans_flutter_params.csv"]
    assert "oscillation_frequency,50" in csv
    assert "intake_speed,40" in csv
    # the JSON round-trips to the same case
    js = _json.loads(arts["external-urans_flutter_case.json"])
    assert js["case"]["frequency_hz"] == 50
    # the journal names the extract target
    assert "c_aero" in arts["external-urans_flutter_case.jou"].lower()


# --------------------------------------------------------------------------- #
#  Provenance status string + package surface
# --------------------------------------------------------------------------- #
def test_provenance_status_distinguishes_correlated():
    p = FlutterProvenance(backend="x", fidelity=FlutterFidelity.URANS,
                          converged=True, is_correlated=False)
    assert "NOT correlated" in p.status()
    p2 = FlutterProvenance(backend="x", fidelity=FlutterFidelity.EXPERIMENT,
                           converged=True, is_correlated=True,
                           correlated_against="flow rig")
    assert "flow rig" in p2.status()


def test_symbols_exposed_from_package():
    import suspension
    for name in ("OscillationCase", "FlutterDerivative", "QuasiSteadyFlutterModel",
                 "ExternalCFDFlutterBackend", "extract_flutter_derivative",
                 "FlutterFidelity"):
        assert hasattr(suspension, name), name
