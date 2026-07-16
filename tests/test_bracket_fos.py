"""Tests for the bracket Factor-of-Safety screening module (FoS ≥ 1.5 on yield)."""

import math
import pytest

from suspension.bracket_fos import (
    Bracket, BracketMaterial, MATERIALS, screen_bracket, compare_materials,
    bracket_report,
)
from suspension.interfaces import Severity


def _thin_tab(material="Steel 1018 CR (cold-rolled)", P=2000.0, lever=25.0,
              w=30.0, t=4.0):
    return Bracket(name="tab", material=material, width_mm=w, thickness_mm=t,
                   P_N=P, lever_arm_mm=lever, hole_dia_mm=8.0, edge_dist_mm=12.0,
                   weld_leg_mm=6.0, weld_length_mm=60.0, load_is_shear=True,
                   n_bolts=1, is_estimate=True)


def test_fos_divides_yield_not_ultimate():
    # Pure direct tension, no lever: σ = P/A, FoS must be Sy/σ exactly.
    mat = MATERIALS["Steel 1018 CR (cold-rolled)"]
    br = Bracket(name="t", material="Steel 1018 CR (cold-rolled)",
                 width_mm=20.0, thickness_mm=5.0, P_N=10000.0, lever_arm_mm=0.0,
                 load_is_shear=False)
    r = screen_bracket(br)
    sigma = 10000.0 / (20.0 * 5.0)            # 100 MPa
    assert r.min_fos == pytest.approx(mat.yield_MPa / sigma, rel=1e-6)
    # and definitely NOT ultimate-based
    assert r.min_fos != pytest.approx(mat.uts_MPa / sigma, rel=1e-6)


def test_bending_governs_a_thin_levered_tab():
    r = screen_bracket(_thin_tab())
    assert r.governing_mode == "root bending"
    assert r.verdict == "FAIL"          # 4 mm tab on a 25 mm arm is way under


def test_thick_short_bracket_passes():
    br = _thin_tab(t=10.0, lever=8.0)
    r = screen_bracket(br)
    assert r.verdict == "PASS"
    assert r.min_fos >= 1.5


def test_verdict_bands():
    # Construct a case landing just above 1.5 -> TIGHT (within 15% band).
    # Sweep thickness until min_fos crosses 1.5 and confirm banding logic.
    last = None
    for t in [x / 10 for x in range(30, 120)]:
        r = screen_bracket(_thin_tab(t=t))
        if r.min_fos >= 1.5:
            assert r.verdict in ("TIGHT", "PASS")
            if r.min_fos < 1.5 * 1.15:
                assert r.verdict == "TIGHT"
            else:
                assert r.verdict == "PASS"
            last = r
            break
    assert last is not None


def test_fos_target_is_configurable():
    br = _thin_tab(t=10.0, lever=8.0)
    strict = screen_bracket(br, fos_target=3.0)
    lax = screen_bracket(br, fos_target=1.5)
    # same bracket, stricter target can only worsen or hold the verdict
    assert lax.min_fos == pytest.approx(strict.min_fos, rel=1e-9)
    assert strict.fos_target == 3.0


def test_unknown_material_is_missing_not_crash():
    br = _thin_tab(material="Unobtainium")
    r = screen_bracket(br)
    assert r.verdict == "INVALID"
    assert any(f.severity == Severity.MISSING for f in r.findings)


def test_invalid_geometry_flagged():
    br = Bracket(name="bad", material="Steel 1018 CR (cold-rolled)",
                 width_mm=0.0, thickness_mm=4.0, P_N=1000.0)
    r = screen_bracket(br)
    assert r.verdict == "INVALID"


def test_findings_are_owned_by_chassis():
    r = screen_bracket(_thin_tab())
    assert r.findings
    for f in r.findings:
        assert "chassis" in f.subsystems


def test_fail_finding_has_fail_severity():
    r = screen_bracket(_thin_tab())          # known FAIL
    assert any(f.severity == Severity.FAIL for f in r.findings)


def test_weld_check_runs_only_when_weld_given():
    with_weld = screen_bracket(_thin_tab())
    assert any("weld" in m.mode for m in with_weld.modes)
    no_weld = _thin_tab()
    no_weld.weld_leg_mm = 0.0
    no_weld.weld_length_mm = 0.0
    r2 = screen_bracket(no_weld)
    assert not any("weld" in m.mode for m in r2.modes)


def test_shear_modes_use_shear_yield():
    # A pure-shear section check must divide the shear yield (0.577·Sy), so its
    # FoS is lower than if tensile yield had been (wrongly) used.
    mat = MATERIALS["Steel 1018 CR (cold-rolled)"]
    br = Bracket(name="s", material="Steel 1018 CR (cold-rolled)",
                 width_mm=20.0, thickness_mm=5.0, P_N=10000.0, lever_arm_mm=0.0,
                 load_is_shear=True)
    r = screen_bracket(br)
    sigma = 10000.0 / 100.0
    assert r.min_fos == pytest.approx(mat.shear_yield_MPa / sigma, rel=1e-6)


def test_compare_materials_ranks_by_fos_and_covers_the_decision():
    rows = compare_materials(_thin_tab())
    names = [r["material"] for r in rows]
    assert any("1018 CR" in n for n in names)
    assert any("4130" in n for n in names)
    # sorted descending by FoS
    fos = [r["min_fos"] for r in rows]
    assert fos == sorted(fos, reverse=True)


def test_aswelded_4130_close_to_1018cr():
    # The brief's core claim: un-heat-treated 4130 at a weld is ~ 1018 CR.
    a = MATERIALS["Steel 4130 (as-welded, no PWHT)"].yield_MPa
    b = MATERIALS["Steel 1018 CR (cold-rolled)"].yield_MPa
    assert abs(a - b) / b < 0.10           # within 10%


def test_report_is_plain_text_and_honest():
    txt = bracket_report(screen_bracket(_thin_tab()))
    assert "VERDICT" in txt
    assert "Screening only" in txt          # the honesty caveat must be present
    assert "root bending" in txt
