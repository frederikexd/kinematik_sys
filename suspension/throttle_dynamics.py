# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================

"""
throttle_dynamics.py — coupled throttle-plate + manifold-pressure dynamics, and an
honest plate-flutter screen.

WHAT THIS ADDS OVER throttle_return.py's snap model
---------------------------------------------------
The snap model spins the plate against the spring with a *static* aero torque. That
is fine for "does it return and how fast". It does NOT capture two coupled effects
the brakes/pedal-box lead asked to see before ANSYS:

  1. MANIFOLD-PRESSURE COUPLING. The plate angle sets the effective throttle flow
     area; that area plus the pressure ratio sets the mass flow through the plate
     (compressible orifice equation); mass flow filling/emptying the fixed plenum
     sets dp/dt (a filling ODE); and the manifold vacuum pulls back on the plate as
     a torque. This is a genuinely COUPLED system: plate angle <-> manifold pressure.
     We integrate all three states (theta, theta_dot, p_manifold) together.

  2. PLATE FLUTTER. An aeroelastic instability: if the unsteady aerodynamic moment
     adds energy in phase with the plate's angular velocity (negative aero damping),
     small oscillations grow. We model the plate as a torsional oscillator (spring +
     structural + aero damping) and report the stability of small oscillations, plus
     grow/decay in a time march.

THE HONESTY LINE (read this — it governs what the numbers mean)
--------------------------------------------------------------
Manifold coupling is built from FIRST PRINCIPLES: the compressible orifice mass-flow
and the plenum filling ODE are standard, defensible equations (Heywood, *Internal
Combustion Engine Fundamentals*; standard gas dynamics). Given YOUR plenum volume,
plate/bore geometry and a discharge coefficient Cd, those parts are honest.

FLUTTER IS DIFFERENT and this module refuses to fake it. The aerodynamic
damping/stiffness of a fluttering plate are aeroelastic derivatives that come from
CFD or a flow rig — NOT from first principles. So:
  * the aero-damping coefficient `c_aero` defaults to 0 (no aero damping) and MUST be
    supplied from data to mean anything;
  * with c_aero = 0 the flutter screen reports only the STRUCTURAL stability (always
    stable if structural damping >= 0) and says loudly that the aeroelastic part was
    not modelled;
  * a predicted flutter onset from a supplied c_aero is labelled a SCREEN, not a
    validation — because a 1-DOF lumped aeroelastic model is indicative, and the real
    flutter answer is the CFD/FEA the lead runs next.

This is deliberate. The point of KinematiK is to get you to the right question with
trustworthy inputs so the ANSYS run confirms rather than discovers. A fabricated
flutter speed would do the opposite. So this strengthens the screen and is explicit
about being a screen.

STATE VECTOR (SI): [theta (rad), omega (rad/s), p_manifold (Pa absolute)]
CONSTANTS: R_air = 287 J/kg/K, gamma = 1.4 (air).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Optional

from .interfaces import Finding, Severity
from .throttle_return import ThrottleInertia, AIR_DENSITY_KGM3


R_AIR = 287.0          # J/(kg·K) specific gas constant, air
GAMMA = 1.4            # ratio of specific heats, air


# --------------------------------------------------------------------------- #
#  Compressible flow through the throttle plate (first-principles)
# --------------------------------------------------------------------------- #
def throttle_flow_area(theta_rad: float, bore_area_m2: float,
                       theta_closed_area_frac: float = 0.02) -> float:
    """Effective open flow area past the plate at angle theta (0=closed).

    A butterfly plate's projected open area grows ~ (1 - cos(theta)) as it opens;
    normalised so theta=90° gives ~ the full bore. A small leak area at closed
    (theta≈0) keeps the idle path open and avoids a divide-by-zero in flow.
    """
    frac = 0.5 * (1.0 - math.cos(2.0 * min(max(theta_rad, 0.0), math.pi / 2)))
    frac = max(frac, 0.0)
    leak = max(theta_closed_area_frac, 0.0)
    return bore_area_m2 * (leak + (1.0 - leak) * frac)


def compressible_mass_flow(area_m2: float, p_up_Pa: float, p_down_Pa: float,
                           T_up_K: float = 298.0, Cd: float = 0.7) -> float:
    """Mass flow (kg/s) through an orifice, compressible, from p_up to p_down.

    Standard isentropic orifice flow with choking (Heywood eq. for throttle flow):
      * subsonic when p_down/p_up > critical ratio,
      * choked (mass flow independent of p_down) below the critical ratio.
    Returns >= 0 (flow from up to down). If p_down > p_up, returns 0 (no reverse flow
    modelled here — the intake stroke pulls one way).
    """
    if area_m2 <= 0 or p_up_Pa <= 0:
        return 0.0
    pr = p_down_Pa / p_up_Pa
    if pr >= 1.0:
        return 0.0
    crit = (2.0 / (GAMMA + 1.0)) ** (GAMMA / (GAMMA - 1.0))
    if pr < crit:
        pr = crit          # choked: flow function evaluated at the critical ratio
    # isentropic compressible flow function
    term = (pr ** (2.0 / GAMMA)) - (pr ** ((GAMMA + 1.0) / GAMMA))
    if term < 0:
        term = 0.0
    flow_fn = math.sqrt((2.0 * GAMMA / (GAMMA - 1.0)) * term)
    mdot = Cd * area_m2 * p_up_Pa / math.sqrt(R_AIR * T_up_K) * flow_fn
    return max(mdot, 0.0)


# --------------------------------------------------------------------------- #
#  Config
# --------------------------------------------------------------------------- #
@dataclass
class ManifoldParams:
    """Plenum + intake parameters for the manifold-pressure coupling.

    plenum_volume_m3 : intake plenum volume downstream of the plate.
    bore_area_m2     : throttle bore cross-section (full open area).
    p_ambient_Pa     : upstream (atmospheric) pressure.
    T_ambient_K      : upstream temperature.
    engine_draw_kgps : mass flow the engine pulls OUT of the plenum (into cylinders).
        A simple constant sink; set from your engine's airflow at the operating point.
        0 = engine off (plenum just fills to ambient). Honest simplification: the real
        draw pulses with the intake stroke, but a mean sink is the right screening
        fidelity.
    Cd               : plate discharge coefficient (~0.7 typical; from flow bench).
    p_manifold0_Pa   : initial manifold pressure (defaults to ambient).
    """
    plenum_volume_m3: float = 2.0e-3
    bore_area_m2: float = 1.5e-3
    p_ambient_Pa: float = 101325.0
    T_ambient_K: float = 298.0
    engine_draw_kgps: float = 0.0
    Cd: float = 0.7
    p_manifold0_Pa: Optional[float] = None

    def as_dict(self):
        return asdict(self)


@dataclass
class FlutterParams:
    """Torsional-oscillator parameters for the plate-flutter screen.

    k_theta_Nm_per_rad : effective torsional stiffness restraining plate rotation
        (the return spring's rate about the axis, plus any shaft stiffness).
    c_struct_Nms       : structural/mechanical damping (bearing, seal) — >= 0.
    c_aero_Nms         : AERODYNAMIC damping coefficient. THE honesty knob. Positive =
        aero removes energy (stable); NEGATIVE = aero feeds energy in (flutter). This
        MUST come from CFD or a flow rig. Defaults to 0 (aeroelastic effect NOT
        modelled), which the screen reports explicitly.
    c_aero_ref_speed_ms: the intake speed at which c_aero was measured; the screen
        scales aero damping ~ with dynamic pressure (speed²) from this reference.
    """
    k_theta_Nm_per_rad: float = 2.0
    c_struct_Nms: float = 1.0e-3
    c_aero_Nms: float = 0.0
    c_aero_ref_speed_ms: float = 0.0

    def aero_unquantified(self) -> bool:
        return self.c_aero_Nms == 0.0

    def as_dict(self):
        return asdict(self)


# --------------------------------------------------------------------------- #
#  Coupled plate + manifold time march
# --------------------------------------------------------------------------- #
@dataclass
class CoupledResult:
    returns: bool
    return_time_s: float
    hung_at_deg: float
    peak_speed_rad_s: float
    min_manifold_kpa: float          # lowest manifold pressure seen (vacuum depth)
    final_manifold_kpa: float
    is_estimate: bool
    findings: list = field(default_factory=list)
    trace: list = field(default_factory=list)   # (t, theta_deg, omega, p_kpa)

    def as_dict(self):
        return dict(returns=self.returns, return_time_s=self.return_time_s,
                    hung_at_deg=self.hung_at_deg,
                    peak_speed_rad_s=self.peak_speed_rad_s,
                    min_manifold_kpa=self.min_manifold_kpa,
                    final_manifold_kpa=self.final_manifold_kpa,
                    is_estimate=self.is_estimate,
                    findings=[f.as_dict() for f in self.findings],
                    n_trace=len(self.trace))


def simulate_coupled_return(springs: list,
                            inertia: Optional[ThrottleInertia] = None,
                            manifold: Optional[ManifoldParams] = None,
                            resistance=None,
                            theta_open_deg: float = 90.0,
                            manifold_torque_coeff: float = 0.0,
                            dt: float = 2.0e-4,
                            t_max: float = 2.0) -> CoupledResult:
    """Integrate the plate AND the manifold pressure together, coupled.

    States: theta, omega, p_manifold. The plate closes under the spring; as it
    closes the flow area shrinks, the engine draw pulls the plenum down, and the
    manifold vacuum applies a torque on the plate scaled by `manifold_torque_coeff`
    (the pressure-difference torque per unit area·radius — from geometry/flow bench;
    0 = pressure coupling on the DYNAMICS off, though the pressure is still tracked).

    This is the manifold-coupling piece: it shows whether the developing vacuum as
    the plate closes measurably changes the return, and how deep the manifold vacuum
    goes. `manifold_torque_coeff` is the honest knob for the plate-side pressure
    torque; the pressure itself is computed from first principles regardless.
    """
    from .throttle_return import (ReturnResistance, _spring_closing_torque_at)
    if resistance is None:
        resistance = ReturnResistance()
    if inertia is None:
        inertia = ThrottleInertia()
    if manifold is None:
        manifold = ManifoldParams()

    I = max(float(inertia.I_kgm2), 1e-9)
    theta_open = math.radians(max(theta_open_deg, 1e-3))
    T_closed = sum(s.torque_closed_Nm for s in springs)
    T_open = sum(s.torque_open_Nm for s in springs)
    T_fric = abs(resistance.friction_Nm)
    T_drag = abs(resistance.cable_drag_Nm) + abs(resistance.sensor_detent_Nm)
    is_estimate = bool(getattr(inertia, "is_estimate", False)) or \
        any(getattr(s, "is_estimate", False) for s in springs)
    findings: list = []

    p_amb = manifold.p_ambient_Pa
    p = manifold.p_manifold0_Pa if manifold.p_manifold0_Pa is not None else p_amb
    V = max(manifold.plenum_volume_m3, 1e-9)
    Tamb = manifold.T_ambient_K

    def spring_t(theta):
        return _spring_closing_torque_at(theta, theta_open, T_closed, T_open)

    def dp_dt(theta, p_now):
        # mass flow in through the plate (ambient -> plenum), minus engine draw out
        area = throttle_flow_area(theta, manifold.bore_area_m2)
        mdot_in = compressible_mass_flow(area, p_amb, p_now, Tamb, manifold.Cd)
        mdot_out = max(manifold.engine_draw_kgps, 0.0)
        # plenum as isothermal gas spring: dp/dt = (R T / V) * (mdot_in - mdot_out)
        return (R_AIR * Tamb / V) * (mdot_in - mdot_out)

    def manifold_torque(theta, p_now):
        # vacuum (p_amb - p) acts across the plate; +ve tends to hold it (sign set by
        # geometry). Scaled by the user coeff; 0 = not applied to the dynamics.
        return manifold_torque_coeff * (p_amb - p_now)

    def accel(theta, omega, p_now):
        Ts = spring_t(theta)                     # closes toward -theta
        Tm = manifold_torque(theta, p_now)       # +ve opposes closing (holds open)
        drive = -Ts + Tm
        if abs(omega) > 1e-3:
            fric = (T_fric + T_drag) * (1.0 if omega < 0 else -1.0)
            return (drive + fric) / I
        else:
            cap = T_fric + T_drag
            if abs(drive) <= cap:
                return 0.0
            return (drive - cap * (1.0 if drive > 0 else -1.0)) / I

    theta = theta_open
    omega = 0.0
    t = 0.0
    peak_speed = 0.0
    min_p = p
    trace = [(0.0, theta_open_deg, 0.0, p / 1000.0)]
    n = int(t_max / max(dt, 1e-6))
    returned = False
    for _ in range(n):
        # RK4 on the 3-state system
        def deriv(th, om, pp):
            return om, accel(th, om, pp), dp_dt(th, pp)
        k1 = deriv(theta, omega, p)
        k2 = deriv(theta + 0.5 * dt * k1[0], omega + 0.5 * dt * k1[1], p + 0.5 * dt * k1[2])
        k3 = deriv(theta + 0.5 * dt * k2[0], omega + 0.5 * dt * k2[1], p + 0.5 * dt * k2[2])
        k4 = deriv(theta + dt * k3[0], omega + dt * k3[1], p + dt * k3[2])
        theta = theta + (dt / 6.0) * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
        omega = omega + (dt / 6.0) * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
        p = p + (dt / 6.0) * (k1[2] + 2 * k2[2] + 2 * k3[2] + k4[2])
        p = max(p, 1.0)
        t += dt
        peak_speed = max(peak_speed, abs(omega))
        min_p = min(min_p, p)
        if len(trace) < 6000:
            trace.append((t, math.degrees(max(theta, 0.0)), omega, p / 1000.0))
        if theta <= 0.0:
            returned = True
            theta = 0.0
            break

    if returned:
        findings.append(Finding(
            "throttle-coupled", Severity.OK,
            f"Coupled plate+manifold return: closes in {t*1000:.0f} ms"
            + (" (ESTIMATED inertia/rate)" if is_estimate else "")
            + f"; manifold vacuum reached {(p_amb-min_p)/1000:.1f} kPa below ambient.",
            subsystems=["brakes", "powertrain"],
            detail=dict(return_time_s=t, min_manifold_kpa=min_p / 1000.0,
                        estimate=is_estimate)))
    else:
        findings.append(Finding(
            "throttle-coupled", Severity.FAIL,
            f"Coupled model: throttle did NOT close within {t_max*1000:.0f} ms "
            f"(stuck near {math.degrees(theta):.0f}°). If manifold_torque_coeff>0, the "
            f"developing vacuum may be holding it — check that against the spring.",
            subsystems=["brakes", "powertrain"],
            detail=dict(hung_at_deg=math.degrees(theta), estimate=is_estimate)))

    return CoupledResult(
        returns=returned, return_time_s=(t if returned else math.inf),
        hung_at_deg=(0.0 if returned else math.degrees(theta)),
        peak_speed_rad_s=peak_speed, min_manifold_kpa=min_p / 1000.0,
        final_manifold_kpa=p / 1000.0, is_estimate=is_estimate,
        findings=findings, trace=trace)


# --------------------------------------------------------------------------- #
#  Plate-flutter screen (honest: aero damping is a supplied coefficient)
# --------------------------------------------------------------------------- #
@dataclass
class FlutterResult:
    stable: bool                    # small oscillations decay?
    damping_ratio: float            # effective zeta (>0 stable, <0 flutter)
    natural_freq_hz: float
    aero_modelled: bool             # was a real aero-damping coeff supplied?
    onset_speed_ms: Optional[float] # speed where zeta crosses 0, if aero given
    is_screen: bool                 # ALWAYS True: this is a screen, not validation
    findings: list = field(default_factory=list)

    def as_dict(self):
        return dict(stable=self.stable, damping_ratio=self.damping_ratio,
                    natural_freq_hz=self.natural_freq_hz,
                    aero_modelled=self.aero_modelled,
                    onset_speed_ms=self.onset_speed_ms, is_screen=self.is_screen,
                    findings=[f.as_dict() for f in self.findings])


def screen_plate_flutter(inertia: ThrottleInertia,
                         flutter: FlutterParams,
                         intake_speed_ms: float = 0.0) -> FlutterResult:
    """Screen the plate for torsional-flutter stability at a given intake speed.

    Models the plate as a 1-DOF torsional oscillator:
        I*theta_ddot + (c_struct + c_aero(V))*theta_dot + k_theta*theta = 0
    Stable iff the total damping is positive. c_aero is scaled with dynamic pressure
    from its reference speed: c_aero(V) = c_aero_ref * (V / V_ref)^2.

    HONEST OUTPUT:
      * If no aero-damping coefficient was supplied (c_aero_Nms == 0), the aeroelastic
        part is NOT modelled: the screen reports structural stability only and says
        so — it does NOT claim the plate won't flutter.
      * If a coefficient IS supplied, it reports the damping ratio and (if aero damping
        can go negative with speed) the onset speed — labelled a SCREEN, because a
        1-DOF lumped aeroelastic estimate is indicative, and CFD/FEA is the validation.
    """
    I = max(float(inertia.I_kgm2), 1e-12)
    k = max(flutter.k_theta_Nm_per_rad, 1e-9)
    wn = math.sqrt(k / I)                 # rad/s
    fn = wn / (2.0 * math.pi)
    is_estimate = bool(getattr(inertia, "is_estimate", False))
    findings: list = []

    # aero damping scaled to this speed
    c_aero = 0.0
    aero_modelled = flutter.c_aero_Nms != 0.0
    if aero_modelled and intake_speed_ms > 0 and flutter.c_aero_ref_speed_ms > 0:
        c_aero = flutter.c_aero_Nms * (intake_speed_ms / flutter.c_aero_ref_speed_ms) ** 2
    elif aero_modelled:
        c_aero = flutter.c_aero_Nms

    c_total = flutter.c_struct_Nms + c_aero
    zeta = c_total / (2.0 * math.sqrt(k * I))
    stable = c_total > 0.0

    onset = None
    if aero_modelled and flutter.c_aero_Nms < 0 and flutter.c_aero_ref_speed_ms > 0:
        # c_struct + c_aero_ref*(V/Vref)^2 = 0  ->  V = Vref*sqrt(-c_struct/c_aero_ref)
        ratio = -flutter.c_struct_Nms / flutter.c_aero_Nms
        if ratio > 0:
            onset = flutter.c_aero_ref_speed_ms * math.sqrt(ratio)

    if not aero_modelled:
        findings.append(Finding(
            "throttle-flutter", Severity.WARN,
            f"Flutter screen ran on STRUCTURAL damping only (natural freq "
            f"{fn:.0f} Hz): no aerodynamic damping coefficient was supplied, so the "
            f"aeroelastic part — the part that actually causes flutter — was NOT "
            f"modelled. This does not say the plate won't flutter. Get the aero "
            f"damping derivative from CFD or a flow rig, or take flutter straight to "
            f"ANSYS CFX/Fluent.", subsystems=["brakes", "powertrain"],
            detail=dict(natural_freq_hz=fn, c_struct=flutter.c_struct_Nms)))
    elif stable:
        msg = (f"Flutter SCREEN: stable at {intake_speed_ms:.0f} m/s "
               f"(damping ratio {zeta:.3f}, natural freq {fn:.0f} Hz).")
        if onset is not None:
            msg += (f" But aero damping goes negative above ~{onset:.0f} m/s — flutter "
                    f"onset there. ")
        msg += ("Screen only — a 1-DOF lumped aeroelastic model is indicative; confirm "
                "with CFD/FEA before sign-off.")
        findings.append(Finding(
            "throttle-flutter", Severity.OK if onset is None else Severity.WARN, msg,
            subsystems=["brakes", "powertrain"],
            detail=dict(damping_ratio=zeta, natural_freq_hz=fn, onset_speed_ms=onset)))
    else:
        findings.append(Finding(
            "throttle-flutter", Severity.FAIL,
            f"Flutter SCREEN: UNSTABLE at {intake_speed_ms:.0f} m/s (net damping "
            f"negative, ratio {zeta:.3f}) — aero is feeding energy into plate "
            f"oscillation at {fn:.0f} Hz. Screen only, but a red flag: confirm with "
            f"CFD/FEA and stiffen/damp the plate or shaft.",
            subsystems=["brakes", "powertrain"],
            detail=dict(damping_ratio=zeta, natural_freq_hz=fn)))

    return FlutterResult(
        stable=stable, damping_ratio=zeta, natural_freq_hz=fn,
        aero_modelled=aero_modelled, onset_speed_ms=onset, is_screen=True,
        findings=findings)
