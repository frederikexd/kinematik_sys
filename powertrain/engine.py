"""powertrain.engine — full-car transient drivetrain + thermal cooling solver.

Two coupled systems, one module:

  1. Transient drivetrain simulator — forward-marching (semi-implicit Euler)
     launch simulation with motor torque curve, current limit, traction limit
     (with longitudinal load transfer), aero drag and rolling resistance.
     `optimize_gear_ratio` sweeps final-drive ratios for minimum time over a
     target distance.

  2. Cooling network analyzer — Darcy–Weisbach pipe hydraulics, wye-junction
     loss/flow-split audits for the team's 29→29, 29→40 and 29→12 mm
     y-branches, pump-vs-system operating point, and a lumped-capacitance
     transient coolant temperature march over a lap speed profile using
     effectiveness–NTU radiator heat rejection.

Coupling: the drivetrain result feeds the thermal solver directly
(`simulate_lap_thermal(..., drive=result)` uses the simulated speed and
electrical-loss traces as the air-side and heat-generation inputs).

Ledger coupling: `total_mass_from_ledger` / `publish_to_ledger` duck-type
against `suspension.interfaces.IntegrationLedger` (accepting the live object,
its `.as_dict()` form, or nothing) so the module has zero hard dependency on
the suspension package and stays importable standalone.

Dependencies: numpy only (already a core dependency of the app; lazy-loaded
there). Every solver returns plain numpy time-series arrays sized for direct
`st.line_chart` / plotly plotting.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional, Sequence

import numpy as np

G = 9.80665          # m/s²
RHO_AIR = 1.225      # kg/m³ (sea-level ISA)

# =========================================================================== #
#  Ledger coupling (duck-typed against suspension.interfaces)                 #
# =========================================================================== #

DEFAULT_VEHICLE_MASS_KG = 230.0   # app-wide fallback (matches streamlit_app)


def _ledger_items(ledger) -> Mapping[str, object]:
    """Normalise IntegrationLedger | {name: iface|dict} | None → {name: obj}."""
    if ledger is None:
        return {}
    d = ledger.as_dict() if hasattr(ledger, "as_dict") else ledger
    if not isinstance(d, Mapping):
        return {}
    # as_dict() may nest under a 'subsystems'/'items' key or be flat.
    for k in ("subsystems", "items"):
        if k in d and isinstance(d[k], Mapping):
            d = d[k]
            break
    return d


def _iface_get(iface, attr, default=None):
    if iface is None:
        return default
    if isinstance(iface, Mapping):
        v = iface.get(attr, default)
    else:
        v = getattr(iface, attr, default)
    return default if v is None else v


def total_mass_from_ledger(ledger=None, *, driver_kg: float = 0.0,
                           fallback_kg: float = DEFAULT_VEHICLE_MASS_KG) -> float:
    """Sum every declared `mass_kg` in the INTEGRATION ledger (+ driver).
    Falls back to `fallback_kg` when the ledger is empty/undeclared, so the
    solver never runs on a 0 kg car."""
    total = 0.0
    for iface in _ledger_items(ledger).values():
        try:
            total += float(_iface_get(iface, "mass_kg", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
    return (total if total > 1e-6 else float(fallback_kg)) + float(driver_kg)


def publish_to_ledger(ledger, *, drive: "DrivetrainResult" = None,
                      thermal: "ThermalResult" = None,
                      updated_by: str = "powertrain.engine"):
    """Write headline results back into the live IntegrationLedger in place
    (powertrain: peak torque/power; cooling: heat rejected). No-op on plain
    dicts or missing interfaces module — never raises into the UI."""
    if ledger is None or not hasattr(ledger, "get"):
        return ledger
    try:
        from suspension import interfaces as _ifm
    except Exception:
        return ledger

    def _touch(name):
        it = ledger.get(name) or _ifm.SubsystemInterface(name=name)
        it.updated_by = updated_by
        it.is_estimate = True
        return it

    if drive is not None:
        it = _touch("powertrain")
        it.peak_torque_nm = float(np.max(drive.motor_torque_nm))
        it.peak_power_kw = float(np.max(drive.elec_power_w) / 1e3)
        _set = getattr(ledger, "set", None) or getattr(ledger, "put", None)
        if _set:
            _set(it)
    if thermal is not None:
        it = _touch("cooling")
        it.heat_reject_w = float(np.max(thermal.q_reject_w))
        _set = getattr(ledger, "set", None) or getattr(ledger, "put", None)
        if _set:
            _set(it)
    return ledger


# =========================================================================== #
#  1 — Transient drivetrain simulator                                         #
# =========================================================================== #

@dataclass(frozen=True)
class MotorCurve:
    """Motor torque map. Either a measured (rpm, torque) table (linear
    interpolation, zero torque past the last point) or the classic
    constant-torque → constant-power idealisation from `from_peak`.

    Current limit: T = kt·I ⇒ available torque is additionally clamped to
    kt·i_max_a when both are supplied."""
    rpm: np.ndarray
    torque_nm: np.ndarray
    redline_rpm: float
    kt_nm_per_a: float = 0.0        # torque constant; 0 disables current clamp
    i_max_a: float = float("inf")   # inverter/accumulator current ceiling
    voltage_v: float = 400.0        # DC bus, for electrical power/current audit
    eta_motor: float = 0.92         # motor+inverter electrical efficiency

    @classmethod
    def from_peak(cls, peak_torque_nm: float, peak_power_kw: float,
                  redline_rpm: float, **kw) -> "MotorCurve":
        # Base speed where constant-torque meets constant-power: ω_b = P/T.
        w_base = (peak_power_kw * 1e3) / peak_torque_nm
        rpm_base = w_base * 60.0 / (2.0 * np.pi)
        rpm = np.linspace(0.0, redline_rpm, 200)
        w = np.maximum(rpm * 2.0 * np.pi / 60.0, 1e-9)
        tq = np.where(rpm <= rpm_base, peak_torque_nm,
                      (peak_power_kw * 1e3) / w)
        return cls(rpm=rpm, torque_nm=tq, redline_rpm=redline_rpm, **kw)

    def torque_at(self, rpm) -> np.ndarray:
        t = np.interp(rpm, self.rpm, self.torque_nm, left=self.torque_nm[0],
                      right=0.0)
        t = np.where(np.asarray(rpm) > self.redline_rpm, 0.0, t)
        if self.kt_nm_per_a > 0.0:
            t = np.minimum(t, self.kt_nm_per_a * self.i_max_a)
        return t


@dataclass(frozen=True)
class DrivetrainParams:
    motor: MotorCurve
    tire_radius_m: float = 0.203
    gear_ratio: float = 3.5            # motor:wheel (final drive incl. any reduction)
    mass_kg: float = DEFAULT_VEHICLE_MASS_KG
    drivetrain_eff: float = 0.94       # gears/chain mechanical efficiency
    mu: float = 1.4                    # peak longitudinal tire friction
    driven_weight_frac: float = 0.52   # static weight on driven (rear) axle
    cg_height_m: float = 0.28
    wheelbase_m: float = 1.55
    cda_m2: float = 1.10               # drag area Cd·A
    crr: float = 0.015                 # rolling resistance coefficient
    rot_inertia_factor: float = 1.05   # m_eff = factor·m (wheels/rotor spin-up)

    def with_ledger_mass(self, ledger, *, driver_kg: float = 68.0
                         ) -> "DrivetrainParams":
        """Return a copy whose mass is the live INTEGRATION-ledger total."""
        from dataclasses import replace
        return replace(self, mass_kg=total_mass_from_ledger(
            ledger, driver_kg=driver_kg, fallback_kg=self.mass_kg))


@dataclass
class DrivetrainResult:
    """Uniform-dt time series (equal-length 1-D arrays → drop straight into a
    DataFrame for Streamlit)."""
    t_s: np.ndarray
    x_m: np.ndarray
    v_ms: np.ndarray
    a_ms2: np.ndarray
    motor_rpm: np.ndarray
    motor_torque_nm: np.ndarray
    wheel_force_n: np.ndarray
    traction_limit_n: np.ndarray
    traction_limited: np.ndarray       # bool mask per step
    current_a: np.ndarray
    elec_power_w: np.ndarray
    loss_power_w: np.ndarray           # motor+drivetrain heat → thermal solver
    gear_ratio: float
    current_limited_frac: float = 0.0

    @property
    def time_to_distance_s(self) -> float:
        return float(self.t_s[-1])

    @property
    def v_final_kmh(self) -> float:
        return float(self.v_ms[-1] * 3.6)


def simulate_launch(p: DrivetrainParams, *, distance_m: float = 75.0,
                    v0_ms: float = 0.0, dt: float = 2e-3,
                    t_max_s: float = 30.0) -> DrivetrainResult:
    """Forward march to `distance_m` (FSAE accel default 75 m).

    Per step:
      rpm      = v/r · G · 60/2π
      T        = min(curve(rpm), kt·i_max)                (current limit)
      F_drive  = T·G·η / r
      N_driven = m·g·w_f + m·a_prev·h/L                   (load transfer, RWD)
      F        = min(F_drive, μ·N_driven)                 (traction limit)
      a        = (F − ½ρ·CdA·v² − Crr·m·g) / (k_rot·m)
    Semi-implicit Euler: v ← v+a·dt first, then x ← x+v·dt (stable, no
    energy pump-up at this dt)."""
    n_cap = int(t_max_s / dt) + 1
    T = np.empty(n_cap); X = np.empty(n_cap); V = np.empty(n_cap)
    A = np.empty(n_cap); RPM = np.empty(n_cap); TQ = np.empty(n_cap)
    F = np.empty(n_cap); FT = np.empty(n_cap); LIM = np.empty(n_cap, bool)
    I = np.empty(n_cap); PE = np.empty(n_cap); PL = np.empty(n_cap)

    m, r, gr = p.mass_kg, p.tire_radius_m, p.gear_ratio
    m_eff = p.rot_inertia_factor * m
    i_ceiling = (p.motor.kt_nm_per_a * p.motor.i_max_a
                 if p.motor.kt_nm_per_a > 0 else float("inf"))
    v, x, t, a_prev = float(v0_ms), 0.0, 0.0, 0.0
    n_cur_lim = 0
    i = 0
    while x < distance_m and i < n_cap:
        rpm = v / r * gr * 60.0 / (2.0 * np.pi)
        tq = float(p.motor.torque_at(rpm))
        cur_lim = tq >= i_ceiling - 1e-9
        n_cur_lim += cur_lim
        f_drive = tq * gr * p.drivetrain_eff / r
        # Longitudinal load transfer onto the driven axle: ΔN = m·a·h/L.
        n_driven = m * G * p.driven_weight_frac + m * a_prev * p.cg_height_m / p.wheelbase_m
        f_trac = p.mu * max(n_driven, 0.0)
        f = min(f_drive, f_trac)
        f_net = f - 0.5 * RHO_AIR * p.cda_m2 * v * v - p.crr * m * G
        a = f_net / m_eff
        # Effective torque/current actually transmitted (traction may clip).
        tq_eff = f * r / (gr * p.drivetrain_eff) if gr > 0 else 0.0
        w = rpm * 2.0 * np.pi / 60.0
        p_mech = tq_eff * w
        p_elec = p_mech / max(p.motor.eta_motor, 1e-6)
        cur = (tq_eff / p.motor.kt_nm_per_a if p.motor.kt_nm_per_a > 0
               else p_elec / max(p.motor.voltage_v, 1e-6))

        (T[i], X[i], V[i], A[i], RPM[i], TQ[i], F[i], FT[i], LIM[i], I[i],
         PE[i], PL[i]) = (t, x, v, a, rpm, tq_eff, f, f_trac,
                          f_drive > f_trac, cur, p_elec, p_elec - p_mech)
        v = max(v + a * dt, 0.0)
        x += v * dt
        t += dt
        a_prev = a
        i += 1

    s = slice(0, max(i, 1))
    return DrivetrainResult(
        t_s=T[s].copy(), x_m=X[s].copy(), v_ms=V[s].copy(), a_ms2=A[s].copy(),
        motor_rpm=RPM[s].copy(), motor_torque_nm=TQ[s].copy(),
        wheel_force_n=F[s].copy(), traction_limit_n=FT[s].copy(),
        traction_limited=LIM[s].copy(), current_a=I[s].copy(),
        elec_power_w=PE[s].copy(), loss_power_w=PL[s].copy(),
        gear_ratio=gr,
        current_limited_frac=float(n_cur_lim) / max(i, 1))


@dataclass
class GearSweepResult:
    ratios: np.ndarray
    times_s: np.ndarray                # time to distance per ratio (inf if DNF)
    v_final_kmh: np.ndarray
    traction_limited_frac: np.ndarray  # fraction of steps traction-capped
    current_limited_frac: np.ndarray
    best_ratio: float
    best: DrivetrainResult             # full trace at the optimum, for plotting
    runs: list = field(default_factory=list)   # (ratio, DrivetrainResult)

    def as_table(self) -> list:
        return [dict(ratio=float(r), t_s=float(t), v_kmh=float(v),
                     trac_lim=f"{tl:.0%}", cur_lim=f"{cl:.0%}")
                for r, t, v, tl, cl in zip(
                    self.ratios, self.times_s, self.v_final_kmh,
                    self.traction_limited_frac, self.current_limited_frac)]


def optimize_gear_ratio(p: DrivetrainParams, *, ratios: Sequence[float] = None,
                        distance_m: float = 75.0, dt: float = 2e-3,
                        keep_traces: bool = False) -> GearSweepResult:
    """Sweep final-drive ratios, minimise time over `distance_m`. A ratio is
    infeasible (time=inf) if the car never covers the distance within t_max —
    which is exactly what happens when the motor pins the redline too early
    (torque_at returns 0 past redline, so the march stalls naturally)."""
    ratios = np.asarray(ratios if ratios is not None
                        else np.linspace(2.0, 6.0, 17), float)
    from dataclasses import replace
    times, vf, tl, cl, runs = [], [], [], [], []
    best_t, best_res, best_ratio = float("inf"), None, float(ratios[0])
    for gr in ratios:
        res = simulate_launch(replace(p, gear_ratio=float(gr)),
                              distance_m=distance_m, dt=dt)
        done = res.x_m[-1] >= distance_m * 0.999
        t = res.time_to_distance_s if done else float("inf")
        times.append(t); vf.append(res.v_final_kmh)
        tl.append(float(np.mean(res.traction_limited)))
        cl.append(res.current_limited_frac)
        if keep_traces:
            runs.append((float(gr), res))
        if t < best_t:
            best_t, best_res, best_ratio = t, res, float(gr)
    if best_res is None:   # every ratio DNF'd — return last trace for debug
        best_res = res
    return GearSweepResult(
        ratios=ratios, times_s=np.asarray(times), v_final_kmh=np.asarray(vf),
        traction_limited_frac=np.asarray(tl), current_limited_frac=np.asarray(cl),
        best_ratio=best_ratio, best=best_res, runs=runs)


# =========================================================================== #
#  2 — Cooling network analyzer                                               #
# =========================================================================== #

@dataclass(frozen=True)
class CoolantProps:
    """Bulk fluid properties (defaults ≈ 50/50 water–glycol at 60 °C)."""
    rho: float = 1040.0      # kg/m³
    cp: float = 3550.0       # J/(kg·K)
    mu: float = 1.3e-3       # Pa·s
    k: float = 0.42          # W/(m·K)

    @classmethod
    def water(cls) -> "CoolantProps":
        return cls(rho=983.0, cp=4185.0, mu=4.7e-4, k=0.654)  # ~60 °C


@dataclass(frozen=True)
class PipeSegment:
    d_m: float
    length_m: float
    roughness_m: float = 1.5e-6      # drawn aluminium/silicone hose
    k_minor: float = 0.0             # lumped minor-loss coefficient Σζ

    @property
    def area_m2(self) -> float:
        return np.pi * self.d_m ** 2 / 4.0

    def audit(self, q_m3s: float, fl: CoolantProps) -> dict:
        """v, Re, friction factor, ΔP for volumetric flow q.
        f: laminar 64/Re, else Haaland explicit approximation of Colebrook:
          1/√f = −1.8·log10[ (ε/D/3.7)^1.11 + 6.9/Re ]
        ΔP = (f·L/D + Σζ)·½ρv²  (Darcy–Weisbach + minor losses)."""
        v = q_m3s / self.area_m2
        re = fl.rho * abs(v) * self.d_m / fl.mu
        if re < 1e-9:
            f = 0.0
        elif re < 2300.0:
            f = 64.0 / re
        else:
            f = (-1.8 * np.log10((self.roughness_m / self.d_m / 3.7) ** 1.11
                                 + 6.9 / re)) ** -2
        dp = (f * self.length_m / self.d_m + self.k_minor) * 0.5 * fl.rho * v * v
        return dict(v_ms=float(v), reynolds=float(re), f_darcy=float(f),
                    dp_pa=float(dp), mdot_kgs=float(fl.rho * q_m3s))


@dataclass(frozen=True)
class YBranch:
    """Wye junction, common inlet `d_in_m` → run continues at d_in, branch
    leaves at `d_branch_m`. Loss model (Idelchik-style engineering fit):
      K_run    = k0_run                              (through-flow)
      K_branch = k0_branch + Borda–Carnot area term:
                   expansion  (A_b > A_in): (1 − A_in/A_b)²
                   contraction(A_b < A_in): 0.5·(1 − A_b/A_in)
    Both K's referenced to the INLET velocity head ½ρv_in²."""
    name: str
    d_in_m: float
    d_branch_m: float
    angle_deg: float = 45.0
    k0_run: float = 0.30
    k0_branch: float = 1.00

    @property
    def _k_branch(self) -> float:
        a_in = np.pi * self.d_in_m ** 2 / 4.0
        a_b = np.pi * self.d_branch_m ** 2 / 4.0
        area_k = ((1.0 - a_in / a_b) ** 2 if a_b > a_in
                  else 0.5 * (1.0 - a_b / a_in))
        # Sharper take-off angle costs more; scale vs the 45° baseline.
        return (self.k0_branch + area_k) * (self.angle_deg / 45.0) ** 0.5

    def audit(self, q_in_m3s: float, branch_frac: float,
              fl: CoolantProps) -> "JunctionAudit":
        a_in = np.pi * self.d_in_m ** 2 / 4.0
        a_b = np.pi * self.d_branch_m ** 2 / 4.0
        q_b = q_in_m3s * float(np.clip(branch_frac, 0.0, 1.0))
        q_r = q_in_m3s - q_b
        v_in = q_in_m3s / a_in
        head_in = 0.5 * fl.rho * v_in * v_in
        return JunctionAudit(
            name=self.name,
            v_in_ms=float(v_in),
            v_run_ms=float(q_r / a_in),
            v_branch_ms=float(q_b / a_b),
            mdot_in_kgs=float(fl.rho * q_in_m3s),
            mdot_run_kgs=float(fl.rho * q_r),
            mdot_branch_kgs=float(fl.rho * q_b),
            dp_run_pa=float(self.k0_run * head_in),
            dp_branch_pa=float(self._k_branch * head_in))


# The team's three custom manifold junctions (common 29 mm inlet).
STANDARD_Y_BRANCHES = {
    f"29-{int(db * 1e3)}": YBranch(name=f"29mm-{int(db * 1e3)}mm",
                                   d_in_m=29e-3, d_branch_m=db)
    for db in (29e-3, 40e-3, 12e-3)
}


@dataclass
class JunctionAudit:
    name: str
    v_in_ms: float
    v_run_ms: float
    v_branch_ms: float
    mdot_in_kgs: float
    mdot_run_kgs: float
    mdot_branch_kgs: float
    dp_run_pa: float
    dp_branch_pa: float

    def as_dict(self) -> dict:
        return dict(vars(self))


@dataclass(frozen=True)
class PumpCurve:
    """Quadratic head curve ΔP(Q) = dp0·(1 − (Q/q_max)²), the standard EV
    coolant-pump idealisation. dp0 in Pa, q_max in m³/s."""
    dp0_pa: float = 55e3         # ~0.55 bar shut-off (typ. EWP80-class)
    q_max_m3s: float = 8e-4      # ~48 L/min free-flow

    def dp(self, q_m3s: float) -> float:
        return self.dp0_pa * max(1.0 - (q_m3s / self.q_max_m3s) ** 2, 0.0)


@dataclass(frozen=True)
class Radiator:
    """Crossflow radiator via effectiveness–NTU (both fluids unmixed approx):
      NTU = UA/C_min,  Cr = C_min/C_max
      ε   = 1 − exp[ NTU^0.22/Cr · (exp(−Cr·NTU^0.78) − 1) ]
      Q̇   = ε·C_min·(T_cool,in − T_air)
    Air-side capacity scales with car speed (ram) with a fan floor."""
    ua_w_per_k: float = 220.0
    frontal_area_m2: float = 0.045
    air_capture_eff: float = 0.55      # duct/inlet capture fraction
    fan_floor_ms: float = 4.0          # effective air speed with car stopped
    dp_coolant_k: float = 4.0          # coolant-side minor-loss ζ (core)
    d_port_m: float = 29e-3

    def c_air(self, v_car_ms) -> np.ndarray:
        v_air = np.maximum(np.asarray(v_car_ms, float) * self.air_capture_eff,
                           self.fan_floor_ms)
        return RHO_AIR * v_air * self.frontal_area_m2 * 1005.0  # ṁ·cp_air

    def q_reject(self, t_cool_c, t_air_c, v_car_ms, c_cool_w_k) -> np.ndarray:
        c_a = self.c_air(v_car_ms)
        c_min = np.minimum(c_a, c_cool_w_k)
        c_max = np.maximum(c_a, c_cool_w_k)
        cr = np.clip(c_min / np.maximum(c_max, 1e-9), 1e-6, 1.0)
        ntu = self.ua_w_per_k / np.maximum(c_min, 1e-9)
        eps = 1.0 - np.exp(ntu ** 0.22 / cr * (np.exp(-cr * ntu ** 0.78) - 1.0))
        return eps * c_min * (np.asarray(t_cool_c, float) - t_air_c)


@dataclass
class CoolingNetwork:
    """Series pipe run + radiator core loss + wye junctions feeding parallel
    legs. Junction branch fractions are solved so the parallel legs see equal
    ΔP (the physical split); pass `fixed_split` to override."""
    fluid: CoolantProps = field(default_factory=CoolantProps)
    pump: PumpCurve = field(default_factory=PumpCurve)
    radiator: Radiator = field(default_factory=Radiator)
    segments: Sequence[PipeSegment] = field(default_factory=lambda: (
        PipeSegment(d_m=29e-3, length_m=1.8, k_minor=2.0),   # motor loop
        PipeSegment(d_m=29e-3, length_m=1.2, k_minor=1.5),   # return run
    ))
    junctions: Sequence[YBranch] = field(
        default_factory=lambda: tuple(STANDARD_Y_BRANCHES.values()))
    # Downstream resistance of each junction's (run, branch) leg as extra
    # ζ referenced to that leg's own velocity head — used for the split solve.
    leg_k: Mapping[str, tuple] = field(default_factory=dict)

    # ---- hydraulics ------------------------------------------------------ #
    def _junction_split(self, j: YBranch, q_in: float) -> float:
        """Equal-ΔP parallel split by bisection on branch fraction x:
        ΔP_branch(x) = ΔP_run(1−x) with each leg's junction K + downstream ζ."""
        kr_extra, kb_extra = self.leg_k.get(j.name, (0.0, 0.0))
        a_in = np.pi * j.d_in_m ** 2 / 4.0
        a_b = np.pi * j.d_branch_m ** 2 / 4.0

        def imbalance(x):
            q_b, q_r = q_in * x, q_in * (1.0 - x)
            v_in = q_in / a_in
            h_in = 0.5 * self.fluid.rho * v_in * v_in
            dp_b = j._k_branch * h_in + kb_extra * 0.5 * self.fluid.rho * (q_b / a_b) ** 2
            dp_r = j.k0_run * h_in + kr_extra * 0.5 * self.fluid.rho * (q_r / a_in) ** 2
            return dp_b - dp_r

        lo, hi = 1e-4, 1.0 - 1e-4
        if imbalance(lo) * imbalance(hi) > 0:      # no crossing → K-dominated
            return 0.5 * a_b / (a_b + a_in) * 2.0  # area-weighted fallback
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            (lo, hi) = (mid, hi) if imbalance(mid) < 0 else (lo, mid)
        return 0.5 * (lo + hi)

    def system_dp(self, q_m3s: float) -> float:
        dp = sum(s.audit(q_m3s, self.fluid)["dp_pa"] for s in self.segments)
        dp += self.radiator.dp_coolant_k * 0.5 * self.fluid.rho \
            * (q_m3s / (np.pi * self.radiator.d_port_m ** 2 / 4.0)) ** 2
        for j in self.junctions:
            x = self._junction_split(j, q_m3s)
            a = j.audit(q_m3s, x, self.fluid)
            dp += max(a.dp_run_pa, a.dp_branch_pa)  # parallel: common ΔP
        return dp

    def operating_point(self) -> dict:
        """Pump ∩ system curve by bisection on Q (both monotone)."""
        lo, hi = 1e-8, self.pump.q_max_m3s
        for _ in range(80):
            mid = 0.5 * (lo + hi)
            (lo, hi) = (mid, hi) if self.pump.dp(mid) > self.system_dp(mid) \
                else (lo, mid)
        q = 0.5 * (lo + hi)
        return dict(q_m3s=q, q_lpm=q * 6e4, dp_pa=self.system_dp(q),
                    mdot_kgs=self.fluid.rho * q)

    def audit(self, q_m3s: float = None, *, fixed_split: float = None) -> dict:
        """Full hydraulic audit at Q (defaults to the pump operating point):
        per-segment velocity/Re/ΔP and per-junction velocity/ṁ/ΔP tables."""
        if q_m3s is None:
            q_m3s = self.operating_point()["q_m3s"]
        segs = [dict(d_mm=s.d_m * 1e3, L_m=s.length_m,
                     **s.audit(q_m3s, self.fluid)) for s in self.segments]
        juncs = []
        for j in self.junctions:
            x = fixed_split if fixed_split is not None \
                else self._junction_split(j, q_m3s)
            juncs.append(dict(branch_frac=float(x),
                              **j.audit(q_m3s, x, self.fluid).as_dict()))
        return dict(q_m3s=float(q_m3s), q_lpm=float(q_m3s * 6e4),
                    mdot_kgs=float(self.fluid.rho * q_m3s),
                    dp_total_pa=float(self.system_dp(q_m3s)),
                    segments=segs, junctions=juncs)


# ---- transient thermal march over a lap ----------------------------------- #

@dataclass
class ThermalResult:
    t_s: np.ndarray
    t_coolant_c: np.ndarray
    q_gen_w: np.ndarray
    q_reject_w: np.ndarray
    v_car_ms: np.ndarray
    q_lpm: float
    steady_margin_w: float     # mean(reject) − mean(gen); ≥0 ⇒ system copes

    @property
    def t_peak_c(self) -> float:
        return float(np.max(self.t_coolant_c))


def _resample(t_src, y_src, t_new):
    return np.interp(t_new, t_src, y_src)


def simulate_lap_thermal(net: CoolingNetwork, *,
                         drive: DrivetrainResult = None,
                         t_s: Sequence[float] = None,
                         v_car_ms: Sequence[float] = None,
                         q_gen_w: Sequence[float] = None,
                         n_laps: int = 5, t_amb_c: float = 30.0,
                         t0_c: float = 40.0,
                         coolant_volume_l: float = 2.5,
                         wetted_metal_kg: float = 3.0,
                         dt: float = 0.05) -> ThermalResult:
    """Lumped-capacitance coolant temperature over `n_laps` repeats of a lap
    profile:
        C_th·dT/dt = Q̇_gen(t) − Q̇_reject(T, v_car(t))
        C_th       = ρ·V·cp (coolant) + m_metal·cp_al
        Q̇_reject   = ε-NTU crossflow vs ram+fan air  (Radiator.q_reject)

    The lap profile comes from either (a) a DrivetrainResult — speed trace and
    electrical-loss trace used directly (the drivetrain⇄thermal coupling), or
    (b) explicit (t_s, v_car_ms, q_gen_w) arrays. Coolant ṁ is fixed at the
    pump operating point (hydraulics are quasi-static vs thermal timescales)."""
    if drive is not None:
        t_src, v_src = drive.t_s, drive.v_ms
        q_src = drive.loss_power_w if q_gen_w is None \
            else np.asarray(q_gen_w, float)
    else:
        if t_s is None or v_car_ms is None:
            raise ValueError("need `drive` or explicit (t_s, v_car_ms)")
        t_src = np.asarray(t_s, float)
        v_src = np.asarray(v_car_ms, float)
        q_src = (np.asarray(q_gen_w, float) if q_gen_w is not None
                 else np.full_like(t_src, 3000.0))

    lap_T = float(t_src[-1] - t_src[0])
    t = np.arange(0.0, lap_T * n_laps, dt)
    t_in_lap = t % lap_T + t_src[0]
    v = _resample(t_src, v_src, t_in_lap)
    q_gen = _resample(t_src, q_src, t_in_lap)

    op = net.operating_point()
    c_cool = op["mdot_kgs"] * net.fluid.cp                       # W/K stream
    c_th = (net.fluid.rho * coolant_volume_l * 1e-3 * net.fluid.cp
            + wetted_metal_kg * 896.0)                           # J/K lump

    T_c = np.empty_like(t)
    q_rej = np.empty_like(t)
    Tc = float(t0_c)
    for i in range(t.size):
        qr = float(net.radiator.q_reject(Tc, t_amb_c, v[i], c_cool))
        T_c[i], q_rej[i] = Tc, qr
        Tc += (q_gen[i] - qr) / c_th * dt

    return ThermalResult(
        t_s=t, t_coolant_c=T_c, q_gen_w=q_gen, q_reject_w=q_rej, v_car_ms=v,
        q_lpm=op["q_lpm"],
        steady_margin_w=float(np.mean(q_rej[t.size // 2:])
                              - np.mean(q_gen[t.size // 2:])))


# =========================================================================== #
#  Self-check                                                                 #
# =========================================================================== #
if __name__ == "__main__":
    motor = MotorCurve.from_peak(180.0, 80.0, 6500.0,
                                 kt_nm_per_a=0.61, i_max_a=300.0,
                                 voltage_v=400.0)
    params = DrivetrainParams(motor=motor, mass_kg=total_mass_from_ledger(None))
    sweep = optimize_gear_ratio(params, distance_m=75.0)
    best = sweep.best
    print(f"gear sweep: best {sweep.best_ratio:.2f}:1 → "
          f"{best.time_to_distance_s:.2f} s / 75 m, "
          f"{best.v_final_kmh:.0f} km/h, "
          f"traction-limited {np.mean(best.traction_limited):.0%}, "
          f"current-limited {best.current_limited_frac:.0%}")

    net = CoolingNetwork()
    op = net.operating_point()
    print(f"cooling op point: {op['q_lpm']:.1f} L/min @ {op['dp_pa']/1e3:.1f} kPa")
    for jn in net.audit()["junctions"]:
        print(f"  {jn['name']}: v_in {jn['v_in_ms']:.2f} m/s, "
              f"branch {jn['branch_frac']:.0%} → v {jn['v_branch_ms']:.2f} m/s, "
              f"ΔP_branch {jn['dp_branch_pa']:.0f} Pa, "
              f"ṁ_branch {jn['mdot_branch_kgs']*60:.1f} kg/min")

    th = simulate_lap_thermal(net, drive=best, n_laps=8, t_amb_c=32.0)
    print(f"thermal: peak coolant {th.t_peak_c:.1f} °C over 8 laps, "
          f"steady margin {th.steady_margin_w:+.0f} W")
    assert best.time_to_distance_s < 10.0 and 20.0 < th.t_peak_c < 200.0
    print("SELF-CHECK OK")
