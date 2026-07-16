# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
# ============================================================================
"""Tests for sourcing throttle-return inputs from real data (bench log, CAD).

These lock in the honest boundary-break: real measured/CAD data replaces human
transcription where a source of truth exists, and the module refuses to fabricate
where it doesn't.
"""
import pytest

from suspension.throttle_return_ingest import (
    spring_rate_from_bench_log, crosscheck_pedal_against_cad,
    BenchFit, CadCrossCheck)
from suspension.interfaces import Severity


# --------------------------------------------------------------------------- #
#  Bench-log spring-rate fit
# --------------------------------------------------------------------------- #
def _csv(rows):
    return "\n".join(",".join(str(c) for c in r) for r in rows).encode("utf-8")


def test_fits_linear_rate_from_clean_log():
    # k = 500 N/m: at 10/20/30/40 mm -> 5/10/15/20 N
    log = _csv([["deflection_mm", "force_N"],
                [10, 5.0], [20, 10.0], [30, 15.0], [40, 20.0]])
    fit = spring_rate_from_bench_log(log)
    assert fit.is_trustworthy
    assert fit.k_N_per_m == pytest.approx(500.0, rel=1e-6)
    assert fit.r_squared == pytest.approx(1.0, abs=1e-9)
    assert fit.n_points == 4
    assert any(f.severity == Severity.OK for f in fit.findings)


def test_recovers_preload_intercept():
    # force = 500*x + 3 N  (3 N seat preload)
    log = _csv([["deflection_mm", "force_N"],
                [0, 3.0], [10, 8.0], [20, 13.0], [30, 18.0]])
    fit = spring_rate_from_bench_log(log)
    assert fit.k_N_per_m == pytest.approx(500.0, rel=1e-6)
    assert fit.preload_N == pytest.approx(3.0, abs=1e-6)


def test_kgf_mass_column_is_converted_to_newtons():
    # hang 1,2,3 kg; deflection 10,20,30 mm -> k should be ~ (9.80665 N)/0.01 m
    log = _csv([["deflection_mm", "force_N"], [10, 1], [20, 2], [30, 3]])
    fit = spring_rate_from_bench_log(log, force_in_kgf=True)
    # 1 kgf over 10 mm = 9.80665 N / 0.01 m = 980.665 N/m
    assert fit.k_N_per_m == pytest.approx(980.665, rel=1e-4)


def test_deflection_in_metres_supported():
    log = _csv([["x", "force"], [0.01, 5.0], [0.02, 10.0], [0.03, 15.0]])
    fit = spring_rate_from_bench_log(log, deflection_in_mm=False)
    assert fit.k_N_per_m == pytest.approx(500.0, rel=1e-6)


def test_too_few_points_is_not_trustworthy():
    log = _csv([["deflection_mm", "force_N"], [10, 5.0]])
    fit = spring_rate_from_bench_log(log)
    assert not fit.is_trustworthy
    assert any(f.severity in (Severity.WARN, Severity.MISSING) for f in fit.findings)


def test_nonlinear_spring_flagged_low_r2():
    # clearly nonlinear (quadratic-ish) -> R^2 below floor -> not trustworthy
    log = _csv([["deflection_mm", "force_N"],
                [10, 1.0], [20, 4.0], [30, 9.0], [40, 16.0], [50, 25.0]])
    fit = spring_rate_from_bench_log(log)
    assert not fit.is_trustworthy
    assert fit.r_squared < 0.98
    assert any(f.severity == Severity.WARN for f in fit.findings)


def test_garbage_bytes_do_not_crash():
    fit = spring_rate_from_bench_log(b"\x00\x01not a csv at all")
    assert isinstance(fit, BenchFit)
    assert not fit.is_trustworthy


def test_missing_columns_reported_not_guessed_when_single_col():
    log = _csv([["justforce"], [5.0], [10.0]])
    fit = spring_rate_from_bench_log(log)
    assert not fit.is_trustworthy
    assert any(f.severity == Severity.MISSING for f in fit.findings)


# --------------------------------------------------------------------------- #
#  CAD cross-check of typed pedal dimensions
# --------------------------------------------------------------------------- #
def _manifest(units, max_coord):
    return {"provenance": {"units": units, "max_coord": max_coord}}


def test_consistent_dims_pass_crosscheck():
    # model envelope ~ 2*120 = 240 mm; a 90 mm lever fits fine
    cc = crosscheck_pedal_against_cad(35, 8, 90, _manifest("mm", 120.0))
    assert cc.ok
    assert any(f.severity == Severity.OK for f in cc.findings)


def test_dimension_larger_than_model_is_flagged_as_transcription_error():
    # model envelope ~ 2*50 = 100 mm; a typed 900 mm lever can't be real
    cc = crosscheck_pedal_against_cad(35, 8, 900, _manifest("mm", 50.0))
    assert not cc.ok
    assert any(f.severity == Severity.FAIL for f in cc.findings)


def test_inch_units_warns_about_unit_mismatch():
    cc = crosscheck_pedal_against_cad(35, 8, 3.5, _manifest("in", 4.0))
    # 4 in -> 101.6 mm; envelope ~203 mm, dims small but consistent; must warn units
    assert any("units" in f.message.lower() or "in" in (f.detail.get("cad_units") or "")
               for f in cc.findings)


def test_no_cad_basis_reports_absence_not_silent_pass():
    cc = crosscheck_pedal_against_cad(35, 8, 90, {"provenance": {}})
    assert cc.ok               # nothing to contradict
    assert any(f.severity == Severity.MISSING for f in cc.findings)  # but says so


def test_unknown_units_cannot_convert_and_says_so():
    cc = crosscheck_pedal_against_cad(35, 8, 90, _manifest("furlongs", 1.0))
    assert any(f.severity == Severity.MISSING for f in cc.findings)


# --------------------------------------------------------------------------- #
#  Package surface
# --------------------------------------------------------------------------- #
def test_symbols_exposed_from_package():
    import suspension
    for name in ("spring_rate_from_bench_log", "crosscheck_pedal_against_cad",
                 "BenchFit", "CadCrossCheck"):
        assert hasattr(suspension, name), name
