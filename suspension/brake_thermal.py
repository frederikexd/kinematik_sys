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
# Material library — pick a rotor material and its representative properties.
# Textbook/handbook ranges, NOT measured on a specific supplier's grade. Use to
# COMPARE materials; calibrate before quoting any absolute temperature.
# ---------------------------------------------------------------------------
@dataclass
class RotorMaterial:
    name: str
    rho_kg_m3: float        # density
    cp_j_kgK: float         # specific heat
    k_w_mK: float           # thermal conductivity
    T_max_c: float          # practical max service temp before damage/fade-glaze
    emissivity: float       # surface emissivity (oxidised/used)
    note: str = ""


ROTOR_MATERIALS: dict = {
    "grey_cast_iron": RotorMaterial(
        "Grey cast iron", 7150.0, 460.0, 52.0, 700.0, 0.55,
        "The FSAE default. Cheap, high thermal mass, good damping; "
        "fades/glazes past ~650 °C and rusts."),
    "steel": RotorMaterial(
        "Stainless / alloy steel", 7800.0, 480.0, 25.0, 750.0, 0.35,
        "Corrosion-resistant, lower conductivity so hotter surface spikes; "
        "common on lighter cars."),
    "carbon_carbon": RotorMaterial(
        "Carbon-carbon", 1800.0, 1400.0, 40.0, 1200.0, 0.80,
        "Very light, huge specific heat, high temp ceiling — but only works "
        "ABOVE a high operating window and is expensive."),
    "aluminum_mmc": RotorMaterial(
        "Aluminium MMC (SiC)", 2800.0, 800.0, 150.0, 500.0, 0.30,
        "Light, very high conductivity (even surface temps), but a LOW temp "
        "ceiling — fine for low-energy cars, risky for heavy braking."),
}


def material(name: str) -> RotorMaterial:
    return ROTOR_MATERIALS.get(name, ROTOR_MATERIALS["grey_cast_iron"])


# ---------------------------------------------------------------------------
# Air properties + rotating-disc forced convection (first-principles option)
# ---------------------------------------------------------------------------
_AIR_K = 0.026           # W/(m·K) thermal conductivity, dry air ~300 K
_AIR_NU = 1.6e-5         # m²/s kinematic viscosity
_AIR_PR = 0.71           # Prandtl number


def h_rotating_disc(diameter_mm: float, v_car_ms: float,
                    *, duct_gain: float = 1.0) -> float:
    """Forced-convection coefficient (W/m²·K) for a rotor from a rotating-disc +
    cross-flow Nusselt correlation, instead of a hand-set h0+h1·v line.

    Combines (in quadrature) the disc spinning in air (turbulent-disc
    Nu=0.015·Re^0.8) and the car's forward speed forcing air across it
    (flat-plate Nu=0.037·Re^0.8·Pr^(1/3)). `duct_gain` credits a brake-cooling
    duct that raises local airflow (1.0 = unducted, 2-3 = a good duct).
    """
    D = max(diameter_mm, 1.0) / 1000.0
    r = D / 2.0
    omega = max(abs(v_car_ms), 0.0) / max(r, 1e-3)       # rotor spins with wheel
    Re_rot = omega * r * r / _AIR_NU
    Nu_rot = 0.015 * max(Re_rot, 1.0) ** 0.8
    Re_x = max(abs(v_car_ms), 0.0) * D / _AIR_NU
    Nu_x = 0.037 * max(Re_x, 1.0) ** 0.8 * _AIR_PR ** (1.0 / 3.0)
    Nu = (Nu_rot ** 2 + Nu_x ** 2) ** 0.5
    h = Nu * _AIR_K / max(D, 1e-3)
    return float(max(h * max(duct_gain, 0.1), 12.0))


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
# Two-node TRANSIENT model: rotor mass + pad mass, integrated through time.
#   This is the single biggest fidelity jump over the steady single_stop /
#   equilibrium pair: it shows the temperature TRACE through a lap or a repeated-
#   stop fade test, so thermal ACCUMULATION (the thing that actually cooks pads
#   mid-endurance) is visible, and it returns PAD temperature separately, since
#   pads fade on their own temperature, not the rotor's.
# ---------------------------------------------------------------------------
@dataclass
class TwoNodeParams:
    # rotor
    rotor_mat: str = "grey_cast_iron"
    diameter_mm: float = 220.0
    thickness_mm: float = 5.0
    vented_fraction: float = 0.0
    hat_clearance_mm: float = 40.0
    area_factor: float = 2.4         # convective-area multiplier (vented > solid)
    duct_gain: float = 1.0           # brake-duct airflow credit for convection
    # pad (per front corner — both pads of one caliper lumped)
    m_pad_kg: float = 0.12
    cp_pad: float = 800.0            # J/(kg·K), sintered/organic pad (effective)
    pad_area_m2: float = 0.0030      # pad face area touching the rotor
    k_pad_rotor: float = 6.0         # W/K conduction rotor<->pad while braking
    pad_heat_fraction: float = 0.10  # of the friction heat, share into the pad
    # environment / provenance
    T_ambient_c: float = 30.0
    emissivity: float = 0.55
    enable_radiation: bool = True
    calibrated: bool = False

    _SIGMA: float = 5.670374419e-8


@dataclass
class TransientTrace:
    t_s: list                       # time stamps
    T_rotor_c: list                 # rotor bulk temperature trace
    T_pad_c: list                   # pad temperature trace
    p_in_w: list                    # heat into the rotor at each step
    T_rotor_peak_c: float
    T_pad_peak_c: float
    T_rotor_final_c: float
    rotor_mass_kg: float
    material_T_max_c: float
    over_material_limit: bool       # did the rotor exceed its material ceiling?
    synthesized: bool


class TwoNodeRotorPad:
    """Rotor + pad, two lumped masses, integrated through a power-vs-time input.

    Drive it with `simulate(power_series, dt)` where power_series is the heat
    flowing into ONE front rotor at each timestep (W). Helpers below build that
    series for a repeated-stop fade test or straight from a lap-sim trace.
    """

    def __init__(self, params: TwoNodeParams | None = None):
        self.p = params or TwoNodeParams()
        m = material(self.p.rotor_mat)
        self.mat = m
        self.m_rotor = rotor_mass_kg(
            self.p.diameter_mm, self.p.thickness_mm,
            vented_fraction=self.p.vented_fraction,
            hat_clearance_mm=self.p.hat_clearance_mm, rho_kg_m3=m.rho_kg_m3)
        self.A_conv = convective_area_m2(
            self.p.diameter_mm, hat_clearance_mm=self.p.hat_clearance_mm,
            area_factor=self.p.area_factor)
        self.C_rotor = max(self.m_rotor * m.cp_j_kgK, 1e-6)
        self.C_pad = max(self.p.m_pad_kg * self.p.cp_pad, 1e-6)

    def simulate(self, power_series, dt: float,
                 v_series=None) -> TransientTrace:
        """Integrate rotor+pad temperatures. `power_series[i]` is heat into the
        rotor (W) for step i; `v_series[i]` (m/s) sets the convection coefficient
        that step (defaults to a standstill value if omitted)."""
        p = self.p
        Tamb = p.T_ambient_c
        Tamb_k = Tamb + 273.15
        Tr = Tamb
        Tp = Tamb
        ts, Tr_tr, Tp_tr, pin = [], [], [], []
        t = 0.0
        n = len(power_series)
        for i in range(n):
            q_in = max(float(power_series[i]), 0.0)
            v = float(v_series[i]) if (v_series is not None and i < len(v_series)) else 0.0
            h = h_rotating_disc(p.diameter_mm, v, duct_gain=p.duct_gain)

            # split friction heat: most to the rotor, a share to the pad
            q_rotor = q_in * (1.0 - p.pad_heat_fraction)
            q_pad_gen = q_in * p.pad_heat_fraction

            # conduction rotor<->pad (only meaningful while in contact/braking)
            in_contact = q_in > 1.0
            q_cond = (p.k_pad_rotor * (Tr - Tp)) if in_contact else 0.0

            # rotor losses: convection + radiation off the swept area
            q_conv = h * self.A_conv * (Tr - Tamb)
            q_rad = (p.emissivity * p._SIGMA * self.A_conv
                     * ((Tr + 273.15) ** 4 - Tamb_k ** 4)) if p.enable_radiation else 0.0

            dTr = (q_rotor - q_cond - q_conv - q_rad) / self.C_rotor * dt
            # pad: gains its friction share + conduction from rotor, sheds a
            # little to air (small pad area, modest h)
            q_pad_conv = 0.5 * h * p.pad_area_m2 * (Tp - Tamb)
            dTp = (q_pad_gen + q_cond - q_pad_conv) / self.C_pad * dt

            Tr = max(Tamb, Tr + dTr)
            Tp = max(Tamb, Tp + dTp)
            t += dt
            ts.append(t); Tr_tr.append(Tr); Tp_tr.append(Tp); pin.append(q_in)

        T_rmax = max(Tr_tr) if Tr_tr else Tamb
        T_pmax = max(Tp_tr) if Tp_tr else Tamb
        return TransientTrace(
            t_s=ts, T_rotor_c=Tr_tr, T_pad_c=Tp_tr, p_in_w=pin,
            T_rotor_peak_c=T_rmax, T_pad_peak_c=T_pmax,
            T_rotor_final_c=(Tr_tr[-1] if Tr_tr else Tamb),
            rotor_mass_kg=self.m_rotor, material_T_max_c=self.mat.T_max_c,
            over_material_limit=(T_rmax > self.mat.T_max_c),
            synthesized=not p.calibrated)


# ---------------------------------------------------------------------------
# 1-D THROUGH-THICKNESS conduction — resolves the SURFACE-to-CORE gradient.
#
#   The lumped models above know only the rotor's average temperature. But in a
#   hard stop heat enters the friction face faster than it conducts inward, so
#   the SURFACE runs far hotter than the bulk for a few seconds — and that
#   surface spike, plus the steep gradient behind it, is what drives thermal
#   CRACKING and surface fade. A single bulk number can't see it.
#
#   This discretises the half-thickness (heating is symmetric on both faces, so
#   we model half with an insulated centreline) into N nodes and integrates the
#   1-D heat equation explicitly. From the surface-to-core ΔT it estimates a
#   thermal stress  σ ≈ E·α·ΔT / (1−ν)  — a SCREENING number for crack risk,
#   not an FEA stress field. Still 1-D (through thickness only): no hot-spot
#   location, no vane-root concentration. Flagged synthesized like everything
#   else until calibrated.
# ---------------------------------------------------------------------------

# Material mechanical properties for the thermal-stress screen (representative
# handbook values: Young's modulus E, thermal-expansion α, Poisson ν, and a
# rough tensile/rupture stress the screen compares against).
_MECH = {
    "grey_cast_iron": dict(E_gpa=110.0, alpha=1.1e-5, nu=0.26, sigma_lim_mpa=250.0),
    "steel":          dict(E_gpa=200.0, alpha=1.2e-5, nu=0.29, sigma_lim_mpa=500.0),
    "carbon_carbon":  dict(E_gpa=40.0,  alpha=2.0e-6, nu=0.15, sigma_lim_mpa=120.0),
    "aluminum_mmc":   dict(E_gpa=120.0, alpha=1.6e-5, nu=0.30, sigma_lim_mpa=300.0),
}


@dataclass
class ThroughThicknessTrace:
    t_s: list                       # time stamps
    T_surface_c: list               # friction-surface temperature trace
    T_core_c: list                  # centre-plane (core) temperature trace
    dT_gradient_c: list             # surface − core at each step
    sigma_mpa: list                 # screening thermal stress at each step
    T_surface_peak_c: float
    dT_gradient_peak_c: float        # peak surface-to-core gradient
    sigma_peak_mpa: float            # peak screening thermal stress
    sigma_limit_mpa: float           # material's comparison stress
    crack_risk: str                  # 'low' / 'elevated' / 'high'
    n_nodes: int
    rotor_mass_kg: float
    material_T_max_c: float
    over_material_limit: bool
    synthesized: bool


class OneDRotor:
    """1-D through-thickness rotor conduction. Resolves the surface-to-core
    gradient an explicit finite-difference scheme, and screens thermal-crack
    risk from the gradient. Same fade-test / lap power series drive it as the
    lumped models."""

    def __init__(self, params: TwoNodeParams | None = None, *, n_nodes: int = 12):
        self.p = params or TwoNodeParams()
        self.mat = material(self.p.rotor_mat)
        self.n = max(int(n_nodes), 4)
        # half-thickness in metres (symmetric heating both faces)
        self.half_t = max(self.p.thickness_mm, 0.5) / 1000.0 / 2.0
        self.dx = self.half_t / (self.n - 1)
        self.alpha = self.mat.k_w_mK / max(self.mat.rho_kg_m3 * self.mat.cp_j_kgK,
                                           1e-6)  # thermal diffusivity m²/s
        # swept friction-ring area of ONE face (heat enters here)
        r_out = max(self.p.diameter_mm, 1.0) / 2000.0
        r_in = max(r_out - self.p.hat_clearance_mm / 1000.0, 0.02)
        self.face_area = math.pi * (r_out ** 2 - r_in ** 2)
        self.A_conv = convective_area_m2(
            self.p.diameter_mm, hat_clearance_mm=self.p.hat_clearance_mm,
            area_factor=self.p.area_factor)
        self.m_rotor = rotor_mass_kg(
            self.p.diameter_mm, self.p.thickness_mm,
            vented_fraction=self.p.vented_fraction,
            hat_clearance_mm=self.p.hat_clearance_mm, rho_kg_m3=self.mat.rho_kg_m3)

    def _max_stable_dt(self) -> float:
        """Explicit 1-D conduction is stable only for Fourier number ≤ 0.5."""
        return 0.5 * self.dx * self.dx / max(self.alpha, 1e-9)

    def simulate(self, power_series, dt: float,
                 v_series=None) -> ThroughThicknessTrace:
        p = self.p
        Tamb = p.T_ambient_c
        Tamb_k = Tamb + 273.15
        mech = _MECH.get(p.rotor_mat, _MECH["grey_cast_iron"])
        E = mech["E_gpa"] * 1e9
        alpha_x = mech["alpha"]
        nu = mech["nu"]
        sig_lim = mech["sigma_lim_mpa"]

        # Sub-step so each conduction update respects the stability limit, while
        # still reporting on the caller's dt grid.
        dt_max = self._max_stable_dt()
        sub = max(int(math.ceil(dt / max(dt_max, 1e-9))), 1)
        h_dt = dt / sub
        Fo = self.alpha * h_dt / (self.dx * self.dx)   # ≤ 0.5 by construction

        T = [Tamb] * self.n        # node 0 = surface, node n-1 = core (centre)
        ts, Tsurf, Tcore, dTg, sig = [], [], [], [], []
        rho_cp = self.mat.rho_kg_m3 * self.mat.cp_j_kgK
        t = 0.0
        n_steps = len(power_series)
        for i in range(n_steps):
            q_in = max(float(power_series[i]), 0.0)
            v = float(v_series[i]) if (v_series is not None and i < len(v_series)) else 0.0
            h = h_rotating_disc(p.diameter_mm, v, duct_gain=p.duct_gain)
            # Heating is symmetric on BOTH friction faces; we model HALF the
            # thickness (one face + insulated centre-plane), so this half sees
            # HALF the rotor's heat and ONE face's worth of convective area.
            q_rotor = q_in * (1.0 - p.pad_heat_fraction)      # into rotor (W)
            q_surf_flux = (q_rotor / 2.0) / max(self.face_area, 1e-6)  # W/m², one face
            # convective coefficient acts over this face's area; per unit face
            # area that's just h (loss term below is already per m²).

            for _ in range(sub):
                Tn = T[:]  # explicit: update from previous field
                for j in range(self.n):
                    if j == 0:
                        # SURFACE node: conduction inward + incoming flux − losses
                        cond = self.alpha * (T[1] - T[0]) / (self.dx * self.dx)
                        # convective/radiative loss per unit FACE area, scaled by
                        # the area_factor so a vented rotor's extra wetted area
                        # cools the surface the same way it does in the lumped
                        # model (keeps the two models' energy budgets consistent).
                        loss = p.area_factor * h * (T[0] - Tamb)
                        rad = (p.area_factor * p.emissivity * p._SIGMA
                               * ((T[0] + 273.15) ** 4 - Tamb_k ** 4)) \
                            if p.enable_radiation else 0.0
                        src = (q_surf_flux - loss - rad) / (self.dx / 2.0) / rho_cp
                        Tn[0] = T[0] + h_dt * (cond + src)
                    elif j == self.n - 1:
                        # CORE node: insulated centreline (symmetry) → mirror node
                        cond = self.alpha * (T[j - 1] - T[j]) / (self.dx * self.dx)
                        Tn[j] = T[j] + h_dt * cond
                    else:
                        cond = self.alpha * (T[j - 1] - 2.0 * T[j] + T[j + 1]) \
                            / (self.dx * self.dx)
                        Tn[j] = T[j] + h_dt * cond
                T = [max(Tamb, x) for x in Tn]

            t += dt
            grad = T[0] - T[-1]                         # surface − core
            # constrained thermal stress from the through-thickness gradient
            sigma = E * alpha_x * abs(grad) / (1.0 - nu) / 1e6   # MPa
            ts.append(t); Tsurf.append(T[0]); Tcore.append(T[-1])
            dTg.append(grad); sig.append(sigma)

        T_smax = max(Tsurf) if Tsurf else Tamb
        g_max = max(dTg) if dTg else 0.0
        s_max = max(sig) if sig else 0.0
        ratio = s_max / max(sig_lim, 1e-6)
        risk = "low" if ratio < 0.5 else ("elevated" if ratio < 0.85 else "high")
        return ThroughThicknessTrace(
            t_s=ts, T_surface_c=Tsurf, T_core_c=Tcore, dT_gradient_c=dTg,
            sigma_mpa=sig, T_surface_peak_c=T_smax, dT_gradient_peak_c=g_max,
            sigma_peak_mpa=s_max, sigma_limit_mpa=sig_lim, crack_risk=risk,
            n_nodes=self.n, rotor_mass_kg=self.m_rotor,
            material_T_max_c=self.mat.T_max_c,
            over_material_limit=(T_smax > self.mat.T_max_c),
            synthesized=not p.calibrated)


def fade_test_power_series(*, mass_kg: float, v0_ms: float, front_bias: float,
                           n_stops: int, decel_g: float = 1.2,
                           gap_s: float = 8.0, dt: float = 0.1,
                           heat_to_rotor: float = 0.90):
    """Build a (power_series, v_series) for a repeated-stop fade test: `n_stops`
    hard stops from v0, each followed by `gap_s` of cooling at cruise. This is
    the classic 'do the brakes survive the back half of endurance' check.
    Returns lists ready for TwoNodeRotorPad.simulate."""
    g = 9.81
    a = max(decel_g * g, 0.5)
    t_stop = v0_ms / a                       # s to stop
    powers, vs = [], []
    for _ in range(max(int(n_stops), 1)):
        # during the stop: instantaneous power into one front rotor = F_brake·v
        steps = max(int(t_stop / dt), 1)
        for s in range(steps):
            v = max(v0_ms - a * s * dt, 0.0)
            F_axle = mass_kg * a * front_bias        # front axle braking force
            P_one = F_axle * v * heat_to_rotor / 2.0  # one of two front rotors
            powers.append(P_one); vs.append(v)
        # cooling gap at a cruise speed (no braking power, airflow continues)
        for s in range(max(int(gap_s / dt), 1)):
            powers.append(0.0); vs.append(0.6 * v0_ms)
    return powers, vs, dt


def lap_power_series(distance, speed, *, mass_kg: float, front_bias: float,
                     dt: float = 0.1, long_g=None, heat_to_rotor: float = 0.90):
    """Resample a lap-sim distance/speed trace into a uniform (power, v, dt)
    series for the transient model: braking power into one front rotor over time,
    zero where the car isn't braking. Lets you watch the rotor heat up corner by
    corner over a real lap."""
    n = min(len(distance), len(speed))
    if n < 2:
        return [0.0], [0.0], dt
    # build time from ds/v, then resample onto a uniform dt grid
    t = [0.0]
    for i in range(1, n):
        v_avg = max(0.5 * (float(speed[i]) + float(speed[i - 1])), 0.3)
        ds = abs(float(distance[i]) - float(distance[i - 1]))
        t.append(t[-1] + ds / v_avg)
    T_end = t[-1]
    # instantaneous braking power at each original sample
    p_inst = [0.0] * n
    for i in range(n - 1):
        v0 = float(speed[i]); v1 = float(speed[i + 1])
        if v1 >= v0:
            continue
        if long_g is not None and i < len(long_g) and float(long_g[i]) >= 0:
            continue
        dt_i = max(t[i + 1] - t[i], 1e-4)
        dq = 0.5 * mass_kg * (v0 * v0 - v1 * v1)
        p_inst[i] = (dq * front_bias * heat_to_rotor / 2.0) / dt_i
    # resample onto uniform grid (nearest original sample)
    powers, vs = [], []
    steps = max(int(T_end / dt), 1)
    j = 0
    for s in range(steps):
        tt = s * dt
        while j < n - 1 and t[j + 1] < tt:
            j += 1
        powers.append(p_inst[j]); vs.append(float(speed[j]))
    return powers, vs, dt


def required_duct_gain(*, p_brake_avg_w: float, v_work_ms: float,
                       diameter_mm: float, T_target_c: float,
                       params: BrakeThermalParams | None = None,
                       hat_clearance_mm: float = 40.0) -> dict:
    """Invert the equilibrium: what brake-duct airflow gain is needed to hold the
    rotor at/below `T_target_c`? A direct cooling-duct spec for the brakes lead.
    Returns the required duct_gain and the baseline (unducted) equilibrium."""
    p = params or default_brake_thermal_params()
    A = convective_area_m2(diameter_mm, hat_clearance_mm=hat_clearance_mm,
                           area_factor=p.area_factor)
    dT = max(T_target_c - p.T_ambient_c, 1.0)
    # radiation help at the target temperature
    Tt_k = T_target_c + 273.15
    Ta_k = p.T_ambient_c + 273.15
    q_rad = (p.emissivity * p._SIGMA * A * (Tt_k ** 4 - Ta_k ** 4)
             if p.enable_radiation else 0.0)
    q_conv_needed = max(p_brake_avg_w - q_rad, 0.0)
    h_needed = q_conv_needed / (A * dT)
    h_base = h_rotating_disc(diameter_mm, v_work_ms, duct_gain=1.0)
    gain = h_needed / max(h_base, 1e-6)
    return {
        "required_duct_gain": float(gain),
        "h_needed_w_m2K": float(h_needed),
        "h_unducted_w_m2K": float(h_base),
        "achievable_unducted": gain <= 1.0,
        "T_target_c": float(T_target_c),
    }


def pad_wear_index(*, q_lap_j: float, T_pad_peak_c: float,
                   front_bias: float) -> dict:
    """A relative pad-wear proxy (NOT a mm/lap life). Wear scales with the energy
    the pad processes and rises steeply with temperature once hot. Use to COMPARE
    setups — a higher index means shorter pad life. Dimensionless, anchored so a
    cool low-energy lap ≈ 1.0."""
    q_front_pad = q_lap_j * front_bias / 2.0           # per front pad pair, J
    # Arrhenius-ish temperature acceleration above 300 °C
    temp_accel = 1.0 + max(T_pad_peak_c - 300.0, 0.0) / 200.0
    idx = (q_front_pad / 5.0e5) * temp_accel           # 500 kJ ref → ~1.0 cool
    return {
        "wear_index": float(idx),
        "temp_acceleration": float(temp_accel),
        "q_per_front_pad_kj": float(q_front_pad / 1000.0),
    }


# ---------------------------------------------------------------------------
# ROTOR OPTIMISER — own the optimisation loop, hand Ansys only the winners.
#
#   The FEA pain isn't the single solve, it's the LOOP: re-parametrise CAD,
#   re-mesh, re-apply BCs, re-run, wait — times forty. This runs that whole
#   search in milliseconds per candidate over the 0-D/transient model, returns
#   the lightest rotor that still survives the duty cycle, the full mass-vs-temp
#   Pareto front, and the feasible/infeasible split — so the lead meshes 2–3
#   geometries instead of forty.
# ---------------------------------------------------------------------------
@dataclass
class RotorCandidate:
    diameter_mm: float
    thickness_mm: float
    vented_fraction: float
    material: str
    rotor_mass_kg: float
    T_rotor_peak_c: float
    T_pad_peak_c: float
    T_limit_c: float
    feasible: bool              # stays under the material/target temp limit
    margin_c: float             # T_limit - T_peak (negative => over limit)
    T_surface_peak_c: float = 0.0     # 1-D surface peak (hotter than bulk)
    sigma_peak_mpa: float = 0.0       # screening thermal stress
    crack_risk: str = "n/a"           # 'low'/'elevated'/'high' from 1-D screen


@dataclass
class OptimisationResult:
    candidates: list            # every RotorCandidate evaluated
    feasible: list              # subset that meet the constraint
    best: "RotorCandidate | None"   # lightest feasible candidate
    pareto: list                # non-dominated (min mass, min peak temp) front
    n_evaluated: int
    constraint_c: float
    synthesized: bool


def _evaluate_candidate(dia, th, vent, mat, *, mass_kg, v0_ms, front_bias,
                        n_stops, gap_s, area_factor, duct_gain,
                        T_constraint_c, screen_cracks=False) -> RotorCandidate:
    """Run the transient fade test for one geometry and score it. If
    `screen_cracks`, also run the 1-D through-thickness model to get the surface
    spike and a thermal-crack risk, and fold 'high' crack risk into infeasible."""
    tn = TwoNodeParams(rotor_mat=mat, diameter_mm=dia, thickness_mm=th,
                       vented_fraction=vent, area_factor=area_factor,
                       duct_gain=duct_gain)
    pw, vs, dt = fade_test_power_series(mass_kg=mass_kg, v0_ms=v0_ms,
                                        front_bias=front_bias, n_stops=n_stops,
                                        gap_s=gap_s)
    model = TwoNodeRotorPad(tn)
    tr = model.simulate(pw, dt, v_series=vs)
    limit = min(T_constraint_c, model.mat.T_max_c)
    margin = limit - tr.T_rotor_peak_c
    temp_ok = margin >= 0.0

    T_surf = 0.0
    sigma = 0.0
    risk = "n/a"
    crack_ok = True
    if screen_cracks:
        od = OneDRotor(tn, n_nodes=6).simulate(pw, dt, v_series=vs)
        T_surf = od.T_surface_peak_c
        sigma = od.sigma_peak_mpa
        risk = od.crack_risk
        crack_ok = (risk != "high")   # reject only HIGH crack risk

    return RotorCandidate(
        diameter_mm=float(dia), thickness_mm=float(th),
        vented_fraction=float(vent), material=mat,
        rotor_mass_kg=tr.rotor_mass_kg, T_rotor_peak_c=tr.T_rotor_peak_c,
        T_pad_peak_c=tr.T_pad_peak_c, T_limit_c=float(limit),
        feasible=(temp_ok and crack_ok), margin_c=float(margin),
        T_surface_peak_c=float(T_surf), sigma_peak_mpa=float(sigma),
        crack_risk=risk)


def optimise_rotor(*, mass_kg: float, v0_ms: float, front_bias: float,
                   T_constraint_c: float,
                   materials: list | None = None,
                   dia_range=(180.0, 300.0), dia_steps: int = 10,
                   th_range=(3.0, 10.0), th_steps: int = 6,
                   vent_range=(0.0, 0.5), vent_steps: int = 3,
                   n_stops: int = 8, gap_s: float = 6.0,
                   area_factor: float = 2.4, duct_gain: float = 1.0,
                   screen_cracks: bool = False,
                   calibrated: bool = False) -> OptimisationResult:
    """Search rotor geometry (diameter × thickness × vent × material) for the
    LIGHTEST rotor that still stays under `T_constraint_c` over a repeated-stop
    fade test. Returns the winner, the full Pareto front (mass vs peak temp), and
    every candidate so the trade space is visible.

    The default grid is ~10×6×3×N candidates — each a millisecond-scale transient
    solve, so even four materials finish in ~1–2 s. That is the forty-Ansys-runs
    loop, done before the mesh would have finished generating.
    """
    materials = materials or ["grey_cast_iron"]
    cands: list = []

    def _lin(rng, n):
        a, b = rng
        n = max(int(n), 1)
        if n == 1:
            return [a]
        return [a + (b - a) * i / (n - 1) for i in range(n)]

    for mat in materials:
        for dia in _lin(dia_range, dia_steps):
            for th in _lin(th_range, th_steps):
                for vent in _lin(vent_range, vent_steps):
                    cands.append(_evaluate_candidate(
                        dia, th, vent, mat, mass_kg=mass_kg, v0_ms=v0_ms,
                        front_bias=front_bias, n_stops=n_stops, gap_s=gap_s,
                        area_factor=area_factor, duct_gain=duct_gain,
                        T_constraint_c=T_constraint_c,
                        screen_cracks=screen_cracks))

    feasible = [c for c in cands if c.feasible]
    best = min(feasible, key=lambda c: c.rotor_mass_kg) if feasible else None

    # Pareto front over the FEASIBLE set (the front that matters — lightest rotor
    # for each achievable peak temp). Falls back to all candidates if nothing is
    # feasible, so the lead still sees how far off the constraint the space is.
    pool = feasible if feasible else cands
    pareto = []
    for c in pool:
        dominated = any(
            (o.rotor_mass_kg <= c.rotor_mass_kg
             and o.T_rotor_peak_c <= c.T_rotor_peak_c
             and (o.rotor_mass_kg < c.rotor_mass_kg
                  or o.T_rotor_peak_c < c.T_rotor_peak_c))
            for o in pool)
        if not dominated:
            pareto.append(c)
    pareto.sort(key=lambda c: c.rotor_mass_kg)

    return OptimisationResult(
        candidates=cands, feasible=feasible, best=best, pareto=pareto,
        n_evaluated=len(cands), constraint_c=float(T_constraint_c),
        synthesized=not calibrated)


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
