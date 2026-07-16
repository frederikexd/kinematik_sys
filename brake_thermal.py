# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================

"""
Lumped-parameter BRAKE ROTOR thermal model.

WHY THIS MODULE EXISTS (read this before trusting a number it produces)
-----------------------------------------------------------------------
A brakes lead optimising rotor geometry in a full 3D FEA package (Ansys) burns
most of their compute ruling OUT geometries that a fast 0-D energy balance would
have killed in seconds. This module does NOT replace that FEA: it has no mesh, no
temperature field across the disc face, no coning or vane-by-vane flow, no stress.
What it does is answer the three questions that actually decide which rotors are
worth meshing:

    1. SINGLE STOP   — how hot does the rotor bulk get in one hard stop from
       v0?  ΔT = Q_front / (m_rotor · c_p), where Q_front is the front share
       (set by the brake bias the Brakes tab already computes) of the kinetic
       energy ½·m·v².
    2. ENDURANCE     — over a repeated braking stint, does the rotor stabilise
       below the pad's fade threshold, or climb until it cooks?  An energy
       balance of average braking power in vs. forced-convection out
       (h·A·ΔT, h speed-dependent) gives the equilibrium temperature.
    3. MASS↔TEMP     — the trade the FEA is being used to explore: shrink the
       rotor → less mass (good) → less thermal capacity → higher peak temp
       (bad).  Sweeping diameter/thickness plots that frontier instantly, so
       only the 2–3 survivors get a full Ansys run.

This is the SAME lumped-mass + convection + explicit-Euler machinery as
`tire_thermal.py`, with braking power as the heat-input term instead of tyre
sliding dissipation. It mirrors that module's HONESTY CONTRACT exactly.

THE HONESTY CONTRACT, APPLIED
-----------------------------
  * Every EQUATION here is a textbook energy balance — safe and physical.
  * What is NOT safe is the PARAMETERS: rotor specific heat and density vary by
    grade (grey iron vs. steel vs. carbon-carbon), the convection coefficient to
    air depends on duct design and the actual airflow, and the heat-split to the
    rotor (vs. pad, caliper, hub, radiation) is geometry-specific. Without bench
    or track data these are REPRESENTATIVE values that give the right SHAPE with
    invented magnitudes.
  * Therefore `BrakeThermalParams.calibrated` defaults to False, and every
    result carries `synthesized=True` until a parameter set fitted to measured
    rotor data is supplied. Use these numbers to TRIAGE which geometries deserve
    Ansys — never as a substitute for the FEA's absolute temperatures.
"""

from __future__ import annotations
from dataclasses import dataclass, field
import math


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------
@dataclass
class BrakeThermalParams:
    """Lumped rotor-thermal parameters. Defaults are REPRESENTATIVE values for a
    grey cast-iron FSAE rotor — chosen so the heat-up curve has a sensible shape
    and time constant, NOT measured on any specific rotor.

    `calibrated` is the single most important field: leave it False (default) and
    every output is flagged synthesized. Set True ONLY when density, specific
    heat, convection coefficients and the heat-split came from bench/track data
    for the actual rotor — that is what turns these from physically-shaped
    guesses into numbers you can quote as absolute.
    """
    # --- material (grey cast iron defaults) ---
    rho_kg_m3: float = 7150.0        # rotor material density
    cp_rotor: float = 460.0          # J/(kg·K) specific heat of the rotor material

    # --- heat split ---
    # fraction of braking energy that ends up in the ROTOR this stop (the rest
    # goes to pads, caliper, hub, and radiation). 0.85–0.95 is typical for a
    # short hard stop where the rotor takes almost all of it.
    heat_to_rotor: float = 0.90

    # --- convection to air: h = h0 + h1 * v  (v = vehicle speed, m/s) ---
    h_air0: float = 30.0             # W/(m²·K) at standstill (natural convection)
    h_air1: float = 4.0              # W/(m²·K) per (m/s) of airflow over the rotor
    # convective area scaling: a vented rotor sheds heat over far more area than a
    # solid disc of the same diameter. 2.0 ≈ both faces; >2 for vaned/vented.
    area_factor: float = 2.4

    # --- radiation (small but not nothing once the rotor is hot) ---
    emissivity: float = 0.55         # oxidised iron
    enable_radiation: bool = True

    # --- pad fade law mu_pad(T): grip vs. temperature, mirrors tyre mu_scale ---
    # Pads fade above a threshold; this multiplies the effective pad μ. OFF by
    # default (returns 1.0); when enabled it is flagged synthesized.
    enable_fade: bool = False
    T_fade_onset_c: float = 450.0    # rotor temp where fade begins
    fade_per_C: float = 0.0012       # fraction of μ lost per °C above onset
    fade_floor: float = 0.45         # never fade effective μ below this fraction

    # --- ambient ---
    T_ambient_c: float = 30.0

    # --- provenance ---
    calibrated: bool = False
    fitted_to: str = ""

    _SIGMA: float = 5.670374419e-8   # Stefan–Boltzmann, W/(m²·K⁴)

    def h_air(self, v_x: float) -> float:
        """Speed-dependent convection coefficient (W/m²K)."""
        return self.h_air0 + self.h_air1 * max(abs(float(v_x)), 0.0)

    def mu_fade(self, T_c: float) -> float:
        """Effective pad-μ multiplier from rotor temperature. Identity unless
        fade is enabled."""
        if not self.enable_fade:
            return 1.0
        dT = float(T_c) - self.T_fade_onset_c
        if dT <= 0:
            return 1.0
        s = 1.0 - self.fade_per_C * dT
        return float(max(s, self.fade_floor))


def default_brake_thermal_params() -> BrakeThermalParams:
    """Representative, UNCALIBRATED parameter set (calibrated=False by design)."""
    return BrakeThermalParams()


# ---------------------------------------------------------------------------
# Geometry → rotor mass and convective area
# ---------------------------------------------------------------------------
def rotor_mass_kg(diameter_mm: float, thickness_mm: float,
                  *, vented_fraction: float = 0.0,
                  hat_clearance_mm: float = 40.0,
                  rho_kg_m3: float = 7150.0) -> float:
    """Approximate rotor friction-ring mass from outer diameter and thickness.

    Models the swept annulus (outer radius down to the hat clearance) as a flat
    ring of the given thickness. `vented_fraction` removes that fraction of the
    volume to approximate the cooling vanes/slots of a vented rotor (0 = solid).
    This is a first-order mass estimate for the thermal capacity, NOT a CAD mass.
    """
    r_out = max(diameter_mm, 1.0) / 2000.0           # m
    r_in = max(r_out - hat_clearance_mm / 1000.0, 0.02)
    area_annulus = math.pi * (r_out ** 2 - r_in ** 2)   # m²
    vol = area_annulus * (thickness_mm / 1000.0)        # m³
    vol *= (1.0 - max(0.0, min(vented_fraction, 0.8)))
    return float(vol * rho_kg_m3)


def convective_area_m2(diameter_mm: float, *, hat_clearance_mm: float = 40.0,
                       area_factor: float = 2.4) -> float:
    """Convective area of the rotor's swept annulus, scaled by `area_factor`
    (≈2 for a solid disc's two faces, larger for vented/vaned rotors)."""
    r_out = max(diameter_mm, 1.0) / 2000.0
    r_in = max(r_out - hat_clearance_mm / 1000.0, 0.02)
    face = math.pi * (r_out ** 2 - r_in ** 2)
    return float(face * max(area_factor, 0.1))


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
@dataclass
class SingleStopResult:
    q_total_j: float            # total kinetic energy dissipated in the stop
    q_front_j: float            # energy into the (two) front rotors
    q_per_rotor_j: float        # energy into ONE front rotor
    rotor_mass_kg: float
    delta_T_c: float            # bulk temperature rise of one front rotor
    T_peak_c: float             # starting temp + rise
    synthesized: bool


@dataclass
class EnduranceResult:
    p_brake_avg_w: float        # average braking power into one front rotor
    h_air: float                # convection coefficient at the working speed
    area_m2: float
    T_equilibrium_c: float      # steady-state rotor temp (in - out balanced)
    faded: bool                 # does equilibrium exceed the fade onset?
    mu_fade_mult: float         # effective pad-μ multiplier at equilibrium
    synthesized: bool


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------
class BrakeThermalModel:
    """Lumped single-mass rotor thermal model. Same energy-balance machinery as
    the tyre thermal channel, with braking power as the input term."""

    def __init__(self, params: BrakeThermalParams | None = None):
        self.p = params or default_brake_thermal_params()

    # ---- single hard stop -------------------------------------------------
    def single_stop(self, *, mass_kg: float, v0_ms: float,
                    front_bias: float, diameter_mm: float,
                    thickness_mm: float, vented_fraction: float = 0.0,
                    T_start_c: float | None = None) -> SingleStopResult:
        """Bulk temperature rise of ONE front rotor in a single stop from v0.

        `front_bias` is the fraction of braking torque at the front axle (e.g.
        0.65). Energy is split front/rear by bias, then halved across the two
        front rotors.
        """
        p = self.p
        T0 = self.p.T_ambient_c if T_start_c is None else float(T_start_c)
        q_total = 0.5 * float(mass_kg) * float(v0_ms) ** 2
        q_front = q_total * float(front_bias) * p.heat_to_rotor
        q_per = q_front / 2.0                      # two front rotors
        m_rotor = rotor_mass_kg(diameter_mm, thickness_mm,
                                vented_fraction=vented_fraction,
                                rho_kg_m3=p.rho_kg_m3)
        C = max(m_rotor * p.cp_rotor, 1e-6)        # J/K thermal capacity
        dT = q_per / C
        return SingleStopResult(
            q_total_j=q_total, q_front_j=q_front, q_per_rotor_j=q_per,
            rotor_mass_kg=m_rotor, delta_T_c=dT, T_peak_c=T0 + dT,
            synthesized=not p.calibrated)

    # ---- endurance / repeated braking equilibrium -------------------------
    def equilibrium(self, *, p_brake_avg_w: float, v_work_ms: float,
                    diameter_mm: float, hat_clearance_mm: float = 40.0
                    ) -> EnduranceResult:
        """Steady-state rotor temperature where convective (+ radiative) cooling
        balances the average braking power into one front rotor.

        `p_brake_avg_w` is the time-averaged power dumped into ONE front rotor
        over the stint (compute it from total brake energy per lap × front share
        / lap time, or from the lap sim's braking events).
        """
        p = self.p
        A = convective_area_m2(diameter_mm, hat_clearance_mm=hat_clearance_mm,
                               area_factor=p.area_factor)
        h = p.h_air(v_work_ms)
        Tamb_k = p.T_ambient_c + 273.15

        # Solve P = h·A·(T-Tamb) + ε·σ·A·(T⁴-Tamb⁴) for T. Convection alone is
        # linear; with radiation we do a few Newton iterations.
        # Start from the convection-only solution.
        T = p.T_ambient_c + p_brake_avg_w / max(h * A, 1e-6)
        if p.enable_radiation:
            for _ in range(40):
                Tk = T + 273.15
                f = (h * A * (T - p.T_ambient_c)
                     + p.emissivity * p._SIGMA * A * (Tk ** 4 - Tamb_k ** 4)
                     - p_brake_avg_w)
                df = (h * A
                      + 4.0 * p.emissivity * p._SIGMA * A * Tk ** 3)
                step = f / max(df, 1e-9)
                T -= step
                if abs(step) < 1e-3:
                    break
        mult = p.mu_fade(T)
        return EnduranceResult(
            p_brake_avg_w=float(p_brake_avg_w), h_air=h, area_m2=A,
            T_equilibrium_c=float(T),
            faded=(p.enable_fade and T > p.T_fade_onset_c),
            mu_fade_mult=mult, synthesized=not p.calibrated)

    # ---- mass ↔ peak-temp trade sweep -------------------------------------
    def mass_temp_sweep(self, *, mass_kg: float, v0_ms: float,
                        front_bias: float, thickness_mm: float,
                        dia_min_mm: float = 180.0, dia_max_mm: float = 300.0,
                        n: int = 25, vented_fraction: float = 0.0,
                        T_start_c: float | None = None) -> list[dict]:
        """Sweep rotor diameter and return [{diameter_mm, rotor_mass_kg,
        T_peak_c}] for a single stop — the mass-vs-peak-temperature frontier the
        FEA is being used to explore. Pick the 2–3 knee points to mesh."""
        out = []
        n = max(int(n), 2)
        for i in range(n):
            d = dia_min_mm + (dia_max_mm - dia_min_mm) * i / (n - 1)
            r = self.single_stop(mass_kg=mass_kg, v0_ms=v0_ms,
                                  front_bias=front_bias, diameter_mm=d,
                                  thickness_mm=thickness_mm,
                                  vented_fraction=vented_fraction,
                                  T_start_c=T_start_c)
            out.append(dict(diameter_mm=d, rotor_mass_kg=r.rotor_mass_kg,
                            T_peak_c=r.T_peak_c, delta_T_c=r.delta_T_c))
        return out

    # ---- provenance -------------------------------------------------------
    def provenance(self) -> dict:
        return {
            "model": "lumped single-mass rotor thermal (0-D energy balance)",
            "calibrated": self.p.calibrated,
            "fitted_to": self.p.fitted_to,
            "note": ("Equations are exact energy balances; parameters (cp, "
                     "density, convection h, heat-split) are representative "
                     "unless calibrated. Use to triage which rotor geometries "
                     "deserve a full Ansys run — not as absolute temperatures."),
        }


# ---------------------------------------------------------------------------
# Lap-sim coupling: derive braking energy/power from a velocity trace
# ---------------------------------------------------------------------------
@dataclass
class LapBrakeEnergy:
    q_lap_j: float              # total braking energy dissipated over one lap (all axles)
    q_front_lap_j: float        # front axle's share over the lap
    p_front_avg_w: float        # avg power into ONE front rotor over the lap
    p_front_peak_w: float       # peak instantaneous power into ONE front rotor
    v_brake_mean_ms: float      # mean speed during braking (sets convection h)
    n_brake_samples: int        # how many samples were decelerating
    lap_time_s: float


def lap_brake_energy(distance, speed, *, mass_kg: float, front_bias: float,
                     lap_time_s: float, long_g=None,
                     heat_to_rotor: float = 0.90) -> LapBrakeEnergy:
    """Compute braking energy and average front-rotor power from a lap-sim speed
    trace — the honest input for the endurance equilibrium.

    The kinetic energy lost between two consecutive samples while the car is
    slowing IS the braking energy dissipated there (½·m·(v_i² − v_{i+1}²) for
    every step where v drops, optionally gated by long_g < 0). Summed over the
    lap, split by `front_bias`, halved across the two front rotors, divided by
    lap time → average power into one front rotor. No new physics, just the
    energy the trace already implies.

    `distance` and `speed` are the LapResult arrays (m, m/s). `long_g` is
    optional; when given, only samples with long_g < 0 count as braking, which
    excludes coast-down on drag alone.
    """
    n = min(len(distance), len(speed))
    if n < 2:
        return LapBrakeEnergy(0.0, 0.0, 0.0, 0.0, 0.0, 0, float(lap_time_s))

    q_total = 0.0
    p_peak_per_rotor = 0.0
    v_brake_sum = 0.0
    n_brake = 0
    for i in range(n - 1):
        v0 = float(speed[i])
        v1 = float(speed[i + 1])
        dv2 = v0 * v0 - v1 * v1
        if dv2 <= 0:
            continue  # accelerating or steady — no braking energy here
        if long_g is not None and i < len(long_g) and float(long_g[i]) >= 0:
            continue  # not braking-limited at this sample
        dq = 0.5 * float(mass_kg) * dv2          # J dissipated this step (all axles)
        q_total += dq
        n_brake += 1
        v_brake_sum += 0.5 * (v0 + v1)
        # instantaneous power into one front rotor over this step's duration
        ds = abs(float(distance[i + 1]) - float(distance[i]))
        v_avg = max(0.5 * (v0 + v1), 1e-3)
        dt = ds / v_avg
        if dt > 1e-6:
            p_step = (dq * float(front_bias) * heat_to_rotor / 2.0) / dt
            p_peak_per_rotor = max(p_peak_per_rotor, p_step)

    q_front = q_total * float(front_bias) * heat_to_rotor
    lap_t = max(float(lap_time_s), 1e-6)
    p_front_avg = (q_front / 2.0) / lap_t        # one of two front rotors
    v_mean = (v_brake_sum / n_brake) if n_brake else 0.0
    return LapBrakeEnergy(
        q_lap_j=q_total, q_front_lap_j=q_front, p_front_avg_w=p_front_avg,
        p_front_peak_w=p_peak_per_rotor, v_brake_mean_ms=v_mean,
        n_brake_samples=n_brake, lap_time_s=lap_t)
