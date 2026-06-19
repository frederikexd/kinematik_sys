# ============================================================================
#  KinematiK × SysBridge — Risk integration bridge
#  Created by Frederik Thio / Claude. Copyright (c) 2026.
# ============================================================================
"""
sysbridge_kinematik.py
======================

Translates live KinematiK vehicle and suspension state into a SysBridge
RiskInputs bundle, runs the SysBridge engine, and returns the full
structured result (RiskScore, failures, interactions, remediations).

Design contract
---------------
* Pure functions — no Streamlit calls, no session_state, no I/O.
* Graceful: every field that KinematiK can supply is wired up; everything
  else defaults to the RiskInputs neutral value.
* The caller (render_sysbridge_tab in app.py) owns the UI; this module owns
  the physics-to-risk mapping.

Mapping rationale
-----------------
KinematiK outputs are inherently structural/dynamic; SysBridge's R-codes map
onto them as follows:

  R1 (recall / event severity)
    → driven by corr_report.overall_within_tol: a model that can't predict its
      own measured result is a latent failure. Count of out-of-tolerance channels
      maps to recall_count (each OOT channel is a reportable design escape).

  R2 (FMEA / RPN)
    → camber gain, bump steer, and motion ratio deviation produce an analytic
      proxy RPN. High sensitivity (> 2 °/10mm camber gain) → higher occurrence;
      poor geometry convergence → higher severity.

  R3 (detection gap)
    → 1 − (fraction of sweep positions that converged). A suspension that fails
      to converge across its travel range has blind spots — an analytic proxy for
      detection gap.

  R4 (remaining life)
    → service_age_yr from the user (or 0 if not entered). design_life_yr
      estimated from car class (FSAE ≈ 3 seasons = 3 yr).

  R5 (stability / amplification)
    → camber gain magnitude as a proxy for sensitivity amplification. High camber
      gain means small hardpoint changes drive large camber changes → G > 1.

  R6 (completeness)
    → number of hardpoint variables populated / total. An incomplete geometry
      has lower variable_count; the KinematiK engine already exposes this.

  R7 (regulatory / jurisdiction)
    → FSAE rules are SAE-anchored; discipline = "motorsport suspension"
      maps to DisciplineTier.OPERATIONAL.

  R8 (QMS)
    → open non-conformances: out-of-tolerance simulation channels + any
      unconverged sweep points.

  R9 (physics model)
    → model name "KinematiK double-wishbone / generic" plus damage index derived
      from maximum member load fraction (pushrod / highest-loaded member).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from suspension.kinematics import SuspensionKinematics
    from suspension.dynamics import VehicleDynamics
    from suspension.laptime import LapResult
    from suspension.correlation import CorrelationReport

from sysbridge_engine import (
    RiskInputs, RiskScore,
    FailureDiagnosis, InteractionWarning, RemediationAction,
    DesignVerdict,
    compute_risk_score,
    diagnose_failures,
    detect_interactions,
    rank_remediations,
    render_design_verdict,
    Jurisdiction,
)


# ── Analytic proxy constants ──────────────────────────────────────────────────
_FSAE_DESIGN_LIFE_YR  = 3.0    # typical FSAE car season life
_CAMBER_GAIN_NOMINAL  = 1.0    # °/10mm — safe reference
_CAMBER_GAIN_CRITICAL = 3.5    # °/10mm — starts amplifying load sensitivity
_BUMP_STEER_NOMINAL   = 0.05   # °/10mm
_BUMP_STEER_CRITICAL  = 0.30   # °/10mm
_MAX_MEMBER_LOAD_N    = 5000.0 # N — representative maximum; normalises damage index


@dataclass
class KinematiKRiskContext:
    """
    Snapshot of KinematiK state used to build the SysBridge inputs.
    All fields are plain Python scalars / bools so this is pickle-safe and
    hashable for Streamlit caching.
    """
    # Geometry quality
    camber_gain_deg_per_10mm: float      # from sweep
    bump_steer_deg_per_10mm: float       # from sweep
    roll_centre_height_mm: float         # static
    scrub_radius_mm: float               # static
    caster_deg: float                    # static
    kpi_deg: float                       # static
    converged_fraction: float            # fraction of sweep that converged (0-1)
    topology_name: str                   # "double_wishbone" / "macpherson" etc.

    # Vehicle
    total_mass_kg: float
    max_lateral_g: float
    front_weight_dist: float             # 0-1

    # Load path (max member load fraction, 0-1)
    max_member_load_fraction: float      # 0 if not computed

    # Lap sim (optional — NaN if not run)
    lap_time_s: float
    avg_speed_ms: float

    # Correlation (optional — NaN / None if not run)
    oot_channels: int                    # out-of-tolerance channels
    total_channels: int                  # total correlation channels checked

    # User-supplied context
    service_age_yr: float = 0.0
    open_ncrs: int = 0
    jurisdiction: str = "US"


def build_risk_context(
    kin,
    veh,
    sweep,
    lap_result=None,
    corr_report=None,
    service_age_yr: float = 0.0,
    open_ncrs: int = 0,
    jurisdiction: str = "US",
) -> KinematiKRiskContext:
    """
    Build a KinematiKRiskContext from live KinematiK objects.

    Parameters
    ----------
    kin         : SuspensionKinematics or GenericKinematics
    veh         : VehicleDynamics
    sweep       : list of CornerState — from kin.sweep()
    lap_result  : LapResult or None
    corr_report : correlation CorrelationReport or None
    """
    # --- Geometry quality ---
    try:
        s = kin.static
        roll_centre_h = float(getattr(s, "roll_centre_height", 0.0) or 0.0)
        scrub = float(getattr(s, "scrub_radius", 0.0) or 0.0)
        caster = float(getattr(s, "caster", 0.0) or 0.0)
        kpi = float(getattr(s, "kpi", 0.0) or 0.0)
    except Exception:
        roll_centre_h = scrub = caster = kpi = 0.0

    # camber gain and bump steer from sweep (°/10mm)
    try:
        cambers = [st_.camber for st_ in sweep if st_.converged and math.isfinite(st_.camber)]
        toes    = [st_.toe    for st_ in sweep if st_.converged and math.isfinite(st_.toe)]
        travels = [st_.travel for st_ in sweep if st_.converged]
        if len(cambers) >= 2 and len(travels) >= 2:
            span = travels[-1] - travels[0]
            camber_gain = abs((cambers[-1] - cambers[0]) / span) * 10.0 if span else 0.0
            bump_steer  = abs((toes[-1]    - toes[0])    / span) * 10.0 if span else 0.0
        else:
            camber_gain = bump_steer = 0.0
    except Exception:
        camber_gain = bump_steer = 0.0

    converged_fraction = (
        sum(1 for st_ in sweep if st_.converged) / len(sweep)
        if sweep else 0.0
    )

    # --- Vehicle ---
    try:
        p = veh.p
        total_mass   = float(p.mass)
        front_w_dist = float(p.weight_dist_front)
    except Exception:
        total_mass   = 280.0
        front_w_dist = 0.47

    try:
        max_lat_g = float(veh.max_lateral_g())
        if not math.isfinite(max_lat_g) or max_lat_g <= 0:
            max_lat_g = 1.4
    except Exception:
        max_lat_g = 1.4

    # --- Load path (best effort) ---
    max_member_load_frac = 0.0
    try:
        from suspension import loadpath as lp_mod
        from suspension.dynamics import VehicleParams
        from suspension.kinematics import Hardpoints
        # compute load at 1.5g cornering — representative worst case
        _wl = lp_mod.WheelLoad(
            Fx=0.0,
            Fy=total_mass * 9.81 * 1.5 * front_w_dist,
            Fz=total_mass * 9.81 * front_w_dist,
        )
        _mf = lp_mod.solve_member_forces(kin, kin.static, _wl)
        _forces = [abs(v) for v in _mf.forces.values() if math.isfinite(v)]
        if _forces:
            max_member_load_frac = min(1.0, max(_forces) / _MAX_MEMBER_LOAD_N)
    except Exception:
        max_member_load_frac = 0.0

    # --- Lap sim ---
    lap_time_s = float("nan")
    avg_speed_ms = float("nan")
    if lap_result is not None:
        try:
            if getattr(lap_result, "ok", True):
                lap_time_s   = float(lap_result.lap_time_s)
                avg_speed_ms = float(lap_result.avg_speed_ms)
        except Exception:
            pass

    # --- Correlation ---
    oot_channels   = 0
    total_channels = 0
    if corr_report is not None:
        try:
            chs = getattr(corr_report, "channels", []) or []
            total_channels = len(chs)
            oot_channels   = sum(1 for c in chs if not getattr(c, "within_tol", True))
        except Exception:
            pass

    # --- Topology label ---
    topo = "double_wishbone"
    try:
        if hasattr(kin, "mechanism") and hasattr(kin.mechanism, "topology_name"):
            topo = kin.mechanism.topology_name
    except Exception:
        pass

    return KinematiKRiskContext(
        camber_gain_deg_per_10mm=camber_gain,
        bump_steer_deg_per_10mm=bump_steer,
        roll_centre_height_mm=roll_centre_h,
        scrub_radius_mm=scrub,
        caster_deg=caster,
        kpi_deg=kpi,
        converged_fraction=converged_fraction,
        topology_name=topo,
        total_mass_kg=total_mass,
        max_lateral_g=max_lat_g,
        front_weight_dist=front_w_dist,
        max_member_load_fraction=max_member_load_frac,
        lap_time_s=lap_time_s,
        avg_speed_ms=avg_speed_ms,
        oot_channels=oot_channels,
        total_channels=total_channels,
        service_age_yr=service_age_yr,
        open_ncrs=open_ncrs,
        jurisdiction=jurisdiction,
    )


def context_to_risk_inputs(ctx: KinematiKRiskContext) -> RiskInputs:
    """
    Map a KinematiKRiskContext to a SysBridge RiskInputs bundle.
    """
    # ── R1: event severity proxy ─────────────────────────────────────────────
    # Each out-of-tolerance correlation channel is treated as one "recall" event
    # (a design prediction that diverged from reality).
    recall_count = ctx.oot_channels
    recall_freq = (
        ctx.oot_channels / max(1, ctx.total_channels) * 2.0
        if ctx.total_channels > 0 else 0.0
    )

    # ── R2: FMEA / RPN proxy ─────────────────────────────────────────────────
    # Occurrence: scale camber gain + bump steer to SAE J1739 O scale (1-10)
    camber_norm = min(1.0, ctx.camber_gain_deg_per_10mm / _CAMBER_GAIN_CRITICAL)
    bump_norm   = min(1.0, ctx.bump_steer_deg_per_10mm  / _BUMP_STEER_CRITICAL)
    occurrence  = max(1, round(1 + (camber_norm * 0.6 + bump_norm * 0.4) * 9))

    # Severity: poor convergence → higher severity (uninspectable states)
    severity = max(1, round(1 + (1.0 - ctx.converged_fraction) * 9))

    # Detection: if correlation was run, scale to detection quality
    if ctx.total_channels > 0:
        d_detected = 1.0 - ctx.oot_channels / ctx.total_channels
    else:
        d_detected = 0.5  # unknown — pessimistic
    detection_inv = max(1, round(1 + (1.0 - d_detected) * 9))

    # Proxy RPN = O × S × D
    fmea_max_rpn = float(occurrence * severity * detection_inv)

    # ── R3: detection gap ────────────────────────────────────────────────────
    # Fraction of travel sweep that did NOT converge → dead zone in the design space
    detection_gap = max(0.0, 1.0 - ctx.converged_fraction)

    # ── R4: remaining life ───────────────────────────────────────────────────
    # FSAE car nominally 3-season life. service_age_yr from user.
    t_min_yr = _FSAE_DESIGN_LIFE_YR
    t_max_yr = _FSAE_DESIGN_LIFE_YR * 1.5
    service_age_yr = ctx.service_age_yr

    # ── R5: stability / amplification ────────────────────────────────────────
    # Camber gain > nominal → sensitivity amplifier > 1.
    # g_amplification = 1.0 at nominal; ramps up to ~2.5 at critical.
    raw_g = 1.0 + max(0.0, ctx.camber_gain_deg_per_10mm - _CAMBER_GAIN_NOMINAL) / (
        _CAMBER_GAIN_CRITICAL - _CAMBER_GAIN_NOMINAL
    )
    g_amplification = min(4.0, raw_g)

    # ── R6: completeness ─────────────────────────────────────────────────────
    # Use 10 hardpoints × 3 coords = 30 variables for a full DWB corner.
    # converged_fraction × 30 gives effective populated variables.
    variable_count  = round(30 * ctx.converged_fraction)
    variable_spread = ctx.converged_fraction
    prompt_complexity = 0.4  # structured tool, not free-text AI prompt

    # ── R7: discipline / jurisdiction ────────────────────────────────────────
    discipline  = "motorsport suspension dynamics"
    jurisdiction = ctx.jurisdiction

    # ── R8: QMS — open NCRs from user + OOT channels ────────────────────────
    qms_open_ncrs   = ctx.open_ncrs + ctx.oot_channels
    qms_open_capas  = max(0, ctx.open_ncrs)

    # ── R9: physics model ────────────────────────────────────────────────────
    physics_model_name = f"KinematiK {ctx.topology_name}"
    # damage index: max member load fraction (0-1)
    physics_damage_index = ctx.max_member_load_fraction if ctx.max_member_load_fraction > 0 else None
    # risk delta: positive if max_lat_g is high (more load → more damage)
    physics_risk_delta = max(0.0, ctx.max_lateral_g - 1.5) * 0.15

    return RiskInputs(
        # R1
        recall_count=recall_count,
        recall_frequency_per_yr=recall_freq,
        # R2
        fmea_max_rpn=fmea_max_rpn,
        fmea_max_severity=severity,
        fmea_max_occurrence=occurrence,
        # R3
        detection_gap=detection_gap,
        # R4
        t_min_yr=t_min_yr,
        t_max_yr=t_max_yr,
        service_age_yr=service_age_yr,
        # R5
        g_amplification=g_amplification,
        # R6
        variable_count=variable_count,
        variable_spread=variable_spread,
        prompt_complexity=prompt_complexity,
        # R7
        discipline=discipline,
        jurisdiction=jurisdiction,
        # R8
        qms_open_ncrs=qms_open_ncrs,
        qms_open_capas=qms_open_capas,
        # R9
        physics_model_name=physics_model_name,
        physics_damage_index=physics_damage_index,
        physics_risk_delta=physics_risk_delta,
    )


@dataclass
class SysBridgeResult:
    """Everything the SysBridge tab needs to render."""
    context:       KinematiKRiskContext
    inputs:        RiskInputs
    score:         RiskScore
    failures:      list[FailureDiagnosis]
    interactions:  list[InteractionWarning]
    remediations:  list[RemediationAction]
    verdict:       DesignVerdict


def run_sysbridge(ctx: KinematiKRiskContext) -> SysBridgeResult:
    """
    Full SysBridge pipeline from a KinematiK context snapshot.
    Returns a SysBridgeResult with everything the UI needs.
    """
    inputs        = context_to_risk_inputs(ctx)
    score         = compute_risk_score(inputs)
    failures      = diagnose_failures(inputs, score)
    interactions  = detect_interactions(inputs)
    remediations  = rank_remediations(inputs, score)
    verdict       = render_design_verdict(score, failures, interactions)
    return SysBridgeResult(
        context=ctx, inputs=inputs, score=score,
        failures=failures, interactions=interactions,
        remediations=remediations, verdict=verdict,
    )


def gate_colour(gate_str: str) -> str:
    """CSS class name for a gate verdict string."""
    return {
        "PASS": "good",
        "CONDITIONAL": "warn",
        "REJECT": "bad",
        "HOLD": "bad",
    }.get(gate_str, "")
