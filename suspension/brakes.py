# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================

"""
Brake-rotor thermal design loop — spin a wheel in a virtual tunnel to get the
convective coefficient, push the lap's braking energy through a TRANSIENT rotor
thermal model, then mill the rotor as light as the peak temperature allows while
proving the caliper fluid never boils.

WHY THIS MODULE EXISTS (read this before trusting a temperature it prints)
--------------------------------------------------------------------------
A brake rotor is the one part on an FSAE car where the three things this toolkit
already models separately — CFD airflow, a transient energy input from the lap
sim, and a mass/stiffness trade — all collide on the same chunk of metal. The
design question is brutally specific and the failure modes are physical, not
cosmetic:

  * Too much rotor  -> dead unsprung, rotating mass; the car is slow everywhere
                       and the brakes are never the limit, so the metal is waste.
  * Too little rotor -> the friction ring runs past the pad's binder degradation
                       temperature (pad FADE: the bite goes away mid-stop), and
                       heat soaks back through the hat and caliper into the fluid
                       until it BOILS (a compressible vapour column under the
                       piston: the pedal goes to the floor). Both end the run.

The job, then, is to find the LIGHTEST rotor whose PEAK friction-ring temperature
over a real lap stays under the pad limit AND whose caliper-piston/fluid interface
stays under the brake fluid's wet boiling point under consecutive stops. You
cannot answer that with a static hand calc — the rotor heats into every corner and
sheds on every straight, so peak temperature is a TRANSIENT result, and the
cooling that bounds it is a SPEED-DEPENDENT convective coefficient that itself
comes from airflow through the wheel. This module bolts those together.

THREE LAYERS, IN THE ESTABLISHED KinematiK IDIOM
------------------------------------------------
1. CONVECTIVE COOLING MAP — `WheelTunnel` / `RotorCFDSolver` (cfd.py idiom).
   The aero seam, specialised to a spinning wheel in a box. KinematiK owns the
   run matrix (wheel speed = track speed / rolling radius, sweeping speed),
   *not* the Navier–Stokes solve. A real solver (STAR-CCM+/Fluent/OpenFOAM with
   an MRF or sliding-mesh rotating wheel) drops in at `RotorCFDSolver`; until it
   does, `ReferenceRotorCFD` returns a transparent analytic `h_c(speed)` whose
   provenance shouts "surrogate, not CFD". Same write/run/read protocol, same
   `SolverUnavailable` honesty, as the car-aero seam.

2. TRANSIENT THERMAL FEA — `RotorThermalModel` (pack_thermal.py idiom).
   An explicit lumped-capacitance network (friction ring -> hat -> hub, plus a
   caliper/fluid branch) integrated with forward-Euler + adaptive sub-steps over
   the lap's braking-power trace. The heat input is the REAL alternating flux from
   the lap sim — `P_brake = F_brake · v` deposited into the ring on every braking
   sample, zero on throttle/coast — so the temperature rises into a corner and
   falls on the straight exactly as it does on track. Every parameter that needs a
   dyno or a thermocouple to know is flagged `synthesized` until calibrated, the
   same contract `tire_thermal` and `pack_thermal` use.

3. MASS / VENT OPTIMISATION + FLUID LIMIT — `optimize_rotor`, `fluid_boil_check`.
   Given the transient peak, thin the friction ring, shrink the hat, and add vent
   slots / drillings only where the model says the thermal mass is not needed to
   hold peak temperature under the pad limit — then re-run and confirm the lighter
   rotor still passes BOTH the pad-fade limit and the fluid-boil limit. The
   optimiser never returns a rotor that fails a limit; it returns the lightest one
   that passes, or reports that none in the search does.

DELIBERATE NON-GOAL: this is not Abaqus and not Altair Inspire. It is a
low-order, transparent, instant lumped network with a coarse vent/topology search
— the same relationship `pack_thermal` has to a full CFD-conjugate-heat-transfer
solve. It gives you the right SHAPE and a defensible relative ranking with no
license; it tells you, loudly, that the absolute numbers are provisional until you
calibrate the masses, the contact conductances and the `h_c` map against a real
solve and a real thermocouple. Where a calibrated CHT field is needed, the
`RotorCFDSolver` seam is exactly where it plugs in.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Protocol, Sequence, runtime_checkable

import numpy as np


# =========================================================================== #
#  SECTION 0 — Materials & fluids (the data you must not guess silently)
# =========================================================================== #
@dataclass(frozen=True)
class RotorMaterial:
    """
    Thermophysical properties of the rotor disc material. Defaults are
    representative of the grey cast iron / steel most FSAE rotors are cut from;
    floating two-piece rotors often pair a steel ring with an aluminium hat, which
    you model by giving the hat its own material in `RotorGeometry`.

    These are textbook bulk properties — safe to use — but `density` and the
    friction-ring `cp` are exactly what set rotating mass and how fast the disc
    soaks, so they are part of what `calibrated` certifies when you set it.
    """
    name: str = "grey cast iron"
    density: float = 7150.0          # kg/m^3
    cp: float = 460.0                # J/(kg·K) specific heat
    k_cond: float = 52.0            # W/(m·K) thermal conductivity
    emissivity: float = 0.55        # for the (small) radiation term
    max_service_c: float = 720.0     # disc material limit (NOT the pad limit)


# A couple of common ones, named so a demo / UI can offer them.
ROTOR_MATERIALS: dict[str, RotorMaterial] = {
    "grey cast iron": RotorMaterial(),
    "stainless 410": RotorMaterial(
        name="stainless 410", density=7740.0, cp=460.0, k_cond=25.0,
        emissivity=0.50, max_service_c=800.0),
    "aluminium hat 6061": RotorMaterial(
        name="aluminium 6061 (hat)", density=2700.0, cp=896.0, k_cond=167.0,
        emissivity=0.20, max_service_c=200.0),
}


@dataclass(frozen=True)
class BrakeFluid:
    """
    Brake-fluid boiling points. The number that matters on a used car is the WET
    boiling point — fluid absorbs water through the year and the wet point can sit
    100+ °C below the dry point. The fluid-boil check uses `wet_boil_c` by default
    precisely because designing to the dry point is how a real pedal goes long.

    Defaults are the published figures for common racing fluids. They are public
    spec, not a guess — but verify against the tin you actually run.
    """
    name: str = "Motul RBF 600"
    dry_boil_c: float = 312.0
    wet_boil_c: float = 216.0


BRAKE_FLUIDS: dict[str, BrakeFluid] = {
    "Motul RBF 600": BrakeFluid(),
    "Motul RBF 660": BrakeFluid(name="Motul RBF 660", dry_boil_c=325.0, wet_boil_c=204.0),
    "Castrol SRF": BrakeFluid(name="Castrol SRF", dry_boil_c=310.0, wet_boil_c=270.0),
    "DOT 4 generic": BrakeFluid(name="DOT 4 (generic)", dry_boil_c=230.0, wet_boil_c=155.0),
}


@dataclass(frozen=True)
class PadSpec:
    """
    The friction pad. `degradation_c` is the binder/fade limit — the friction-ring
    temperature above which bite falls off (pad FADE). This, not the disc material
    limit, is almost always the ceiling the ring must stay under, so it is the
    primary pass/fail temperature for the friction ring.
    """
    name: str = "endurance compound"
    mu: float = 0.45                 # pad-disc friction coefficient
    degradation_c: float = 550.0     # fade onset on the friction ring
    max_continuous_c: float = 600.0  # absolute do-not-exceed


# =========================================================================== #
#  SECTION 1 — CONVECTIVE COOLING MAP (the spinning-wheel CFD seam)
# =========================================================================== #
#
# This mirrors aero/cfd.py exactly, narrowed to one job: a wheel + rotor isolated
# in a virtual tunnel, the wheel SPUN at the angular speed matching the car's road
# speed, so the solver can report the mass flow of air through the wheel barrel and
# the speed-dependent convective coefficient h_c on the rotor faces and vents. The
# Navier–Stokes solve lives outside KinematiK; this owns the seam.
class RotorCFDFidelity(str, Enum):
    """How physically complete a wheel-tunnel backend's h_c is — honest labelling."""
    CORRELATION = "correlation"   # Nusselt/empirical surrogate: trend only
    RANS_MRF = "rans_mrf"         # steady RANS, rotating wall / MRF zone
    RANS_SLIDING = "rans_sliding"  # sliding-mesh rotating wheel (the working point)
    DES = "des"                   # resolved wake/vent flow, the expensive truth


@dataclass
class RotorCFDProvenance:
    """Where an h_c map came from and what it's worth (cfd.py CFDProvenance idiom)."""
    backend: str
    fidelity: RotorCFDFidelity
    is_correlated: bool = False          # True only when tied to a measured h_c / temp
    turbulence_model: str = ""
    rotating_wall: bool = False          # was the wheel actually spun? (MRF/sliding)
    cell_count: Optional[int] = None
    correlated_against: str = ""
    notes: str = ""

    def status(self) -> str:
        mesh = f", {self.cell_count/1e6:.1f}M cells" if self.cell_count else ""
        spin = "rotating wall" if self.rotating_wall else "STATIONARY wheel (no spin!)"
        if self.is_correlated:
            ref = self.correlated_against or "measured h_c"
            return f"{self.backend} ({self.fidelity.value}{mesh}, {spin}), correlated against {ref}"
        return (f"{self.backend} ({self.fidelity.value}{mesh}, {spin}), UNCORRELATED — "
                f"convective coefficient is a model output not tied to a measured "
                f"rotor temperature; trust the speed TREND, not the absolute h_c")


@dataclass(frozen=True)
class WheelTunnelPoint:
    """
    One operating point for the wheel-in-tunnel solve: the freestream/road speed and
    the wheel spin that must match it. Keeping both explicit is the lock that stops
    anyone reporting a "cooling" number from a stationary wheel — on a real car the
    barrel pumping from wheel rotation is a large part of rotor cooling, so a
    stationary-wheel h_c is optimistic and wrong.
    """
    speed_ms: float                 # road / freestream speed
    rolling_radius_m: float = 0.22  # tyre rolling radius -> sets wheel spin
    rho: float = 1.225
    mu_air: float = 1.81e-5         # dynamic viscosity, for Reynolds/Nusselt

    @property
    def wheel_rad_s(self) -> float:
        """Wheel angular velocity that matches the road speed (no-slip rolling)."""
        return self.speed_ms / max(self.rolling_radius_m, 1e-6)


@dataclass
class ConvectiveResult:
    """
    What comes back across the wheel-tunnel seam for one speed: the mass flow of air
    through the wheel barrel and the convective coefficient on the rotor faces and
    in the vent channels — or honest `None` holes where the solve didn't produce
    them. A `None` is "not computed", never zero, never a guess (cfd.py contract).
    """
    point: WheelTunnelPoint
    h_face_w_m2k: Optional[float] = None     # convective coeff, rotor friction faces
    h_vent_w_m2k: Optional[float] = None     # convective coeff, internal vent channels
    mdot_barrel_kg_s: Optional[float] = None  # air mass flow through the wheel barrel
    converged: bool = False
    wall_clock_s: Optional[float] = None
    provenance: Optional[RotorCFDProvenance] = None
    notes: str = ""

    def is_usable(self) -> bool:
        return self.converged and self.h_face_w_m2k is not None


@runtime_checkable
class RotorCFDSolver(Protocol):
    """
    The wheel-tunnel seam (cfd.py CFDSolver idiom). A backend turns a
    `WheelTunnelPoint` into solver input (`write_case`), optionally runs it
    (`run_case`), and parses h_c / mdot back (`read_result`). A backend that cannot
    solve here must still write a faithful case and raise `RotorSolverUnavailable`
    from `run_case` — never fabricate an h_c.
    """
    name: str
    def provenance(self) -> RotorCFDProvenance: ...
    def write_case(self, pt: WheelTunnelPoint, workdir: str) -> str: ...
    def run_case(self, pt: WheelTunnelPoint, workdir: str) -> ConvectiveResult: ...
    def read_result(self, pt: WheelTunnelPoint, workdir: str) -> ConvectiveResult: ...


class RotorSolverUnavailable(RuntimeError):
    """Raised by a wheel-tunnel backend that can write input but cannot solve here."""


class ReferenceRotorCFD:
    """
    A transparent analytic h_c(speed) for a spinning wheel + rotor. NOT a CFD solve
    — a smooth surrogate so the run-matrix / thermal / optimiser machinery is
    runnable and testable with no license and no mesh, exactly like
    `ReferenceAeroModel`. Its provenance shouts "surrogate".

    Physics it encodes (deliberately simple, all sign-correct and montonic):
      * forced convection on the rotor faces from a flat-plate-style Nusselt law,
        h ~ Re^0.8, i.e. h_face grows with speed and is augmented by wheel spin,
      * a higher vent-channel coefficient (internal flow, larger wetted area per
        unit frontal area) scaled off the face value,
      * a barrel mass-flow that grows with both road speed and wheel spin.
    The COEFFICIENTS are FSAE-plausible, not measured; provenance.is_correlated is
    False, so everything downstream is flagged synthesized until you calibrate.
    """
    name = "reference-rotor-cfd"

    def __init__(self,
                 char_length_m: float = 0.16,    # rotor characteristic length
                 face_area_ratio: float = 1.0,   # exposed face area scaling
                 vent_h_multiplier: float = 1.6,  # vent channels cool better
                 barrel_area_m2: float = 0.018,  # effective barrel inflow area
                 spin_augment: float = 0.35):    # extra h from wheel rotation pumping
        self.char_length_m = char_length_m
        self.face_area_ratio = face_area_ratio
        self.vent_h_multiplier = vent_h_multiplier
        self.barrel_area_m2 = barrel_area_m2
        self.spin_augment = spin_augment

    def provenance(self) -> RotorCFDProvenance:
        return RotorCFDProvenance(
            backend=self.name,
            fidelity=RotorCFDFidelity.CORRELATION,
            is_correlated=False,
            turbulence_model="none (analytic Nusselt surrogate)",
            rotating_wall=True,    # the surrogate DOES include the spin term
            notes=("Analytic stand-in, NOT a Navier–Stokes wheel solve. Exists to "
                   "make the cooling-map/thermal/optimiser pipeline runnable without "
                   "a solver. Use for trends; never report as CFD h_c."),
        )

    def _h_face(self, pt: WheelTunnelPoint) -> float:
        # Reynolds number on the rotor characteristic length at road speed.
        re = pt.rho * max(pt.speed_ms, 0.1) * self.char_length_m / pt.mu_air
        # Nusselt ~ 0.0296 Re^0.8 Pr^(1/3) (turbulent flat plate, air Pr~0.71)
        nu = 0.0296 * (re ** 0.8) * (0.71 ** (1.0 / 3.0))
        k_air = 0.026                       # W/(m·K), air
        h = nu * k_air / self.char_length_m
        # wheel spin pumps barrel air over the rotor: augment with rim tip speed.
        v_tip = pt.wheel_rad_s * pt.rolling_radius_m   # == road speed, but explicit
        h *= (1.0 + self.spin_augment * (v_tip / max(pt.speed_ms, 0.1) - 1.0 + 1.0))
        return float(max(h, 5.0)) * self.face_area_ratio

    def run_case(self, pt: WheelTunnelPoint, workdir: str = "") -> ConvectiveResult:
        t0 = time.time()
        h_face = self._h_face(pt)
        h_vent = h_face * self.vent_h_multiplier
        mdot = pt.rho * self.barrel_area_m2 * max(pt.speed_ms, 0.0) * \
            (1.0 + 0.2 * (pt.wheel_rad_s * pt.rolling_radius_m) / max(pt.speed_ms, 0.1))
        return ConvectiveResult(
            point=pt, h_face_w_m2k=h_face, h_vent_w_m2k=h_vent,
            mdot_barrel_kg_s=float(mdot), converged=True,
            wall_clock_s=time.time() - t0, provenance=self.provenance(),
            notes="analytic surrogate value")

    def write_case(self, pt: WheelTunnelPoint, workdir: str) -> str:
        os.makedirs(workdir, exist_ok=True)
        path = os.path.join(workdir, "reference_wheel_case.txt")
        with open(path, "w") as f:
            f.write(f"# reference analytic wheel-tunnel case — NOT a CFD deck\n"
                    f"speed={pt.speed_ms} m/s, wheel={pt.wheel_rad_s:.1f} rad/s\n")
        return path

    def read_result(self, pt: WheelTunnelPoint, workdir: str) -> ConvectiveResult:
        return self.run_case(pt, workdir)


class OpenFOAMRotorCFD:
    """
    A real wheel-tunnel adapter stub (backends.py idiom): it WRITES a faithful
    description of the rotating-wheel case a team would run (MRF or sliding mesh,
    rotating wall BC on the wheel, freestream inlet) and raises
    `RotorSolverUnavailable` from `run_case` rather than solving on no mesh/binary.
    The point is that a licensed/own-cluster solve drops in here with zero changes
    upstream.
    """
    name = "openfoam-rotating-wheel"

    def __init__(self, turbulence_model: str = "kOmegaSST",
                 fidelity: RotorCFDFidelity = RotorCFDFidelity.RANS_MRF):
        self.turbulence_model = turbulence_model
        self.fidelity = fidelity

    def provenance(self, cell_count: Optional[int] = None) -> RotorCFDProvenance:
        return RotorCFDProvenance(
            backend=self.name, fidelity=self.fidelity, is_correlated=False,
            turbulence_model=self.turbulence_model, rotating_wall=True,
            cell_count=cell_count,
            notes="Writes a rotating-wheel case; solve runs on the team's cluster.")

    def write_case(self, pt: WheelTunnelPoint, workdir: str) -> str:
        os.makedirs(workdir, exist_ok=True)
        path = os.path.join(workdir, "wheel_tunnel.setup")
        with open(path, "w") as f:
            f.write("# OpenFOAM rotating-wheel wheel-tunnel case (stub)\n")
            f.write(f"freestream_speed_ms {pt.speed_ms}\n")
            f.write(f"wheel_omega_rad_s {pt.wheel_rad_s:.4f}\n")
            f.write(f"rolling_radius_m {pt.rolling_radius_m}\n")
            f.write(f"turbulence {self.turbulence_model}\n")
            f.write("rotating_wall true   # MRF zone or sliding mesh on the wheel\n")
            f.write("post: areaAverage(h) on rotor_faces, rotor_vents; "
                    "phi through barrel_inlet\n")
        return path

    def run_case(self, pt: WheelTunnelPoint, workdir: str) -> ConvectiveResult:
        self.write_case(pt, workdir)
        raise RotorSolverUnavailable(
            "OpenFOAM rotating-wheel solve not available in this environment. "
            "A valid case was written to '%s'; run it on a machine with OpenFOAM "
            "(simpleFoam + MRF or pimpleFoam + sliding mesh), then call "
            "read_result()." % workdir)

    def read_result(self, pt: WheelTunnelPoint, workdir: str) -> ConvectiveResult:
        raise RotorSolverUnavailable(
            "No parsed OpenFOAM result found; run the case first, then parse "
            "areaAverage(h) and the barrel mass flux into a ConvectiveResult.")


@dataclass
class ConvectiveMap:
    """
    The deliverable of the cooling-map layer: convective coefficient as a function
    of road speed, ready for the transient thermal model to look up at every lap
    sample. Built by sweeping a `RotorCFDSolver` over a speed range. Carries the
    provenance forward so the thermal result can never imply more than the map.
    """
    speeds_ms: np.ndarray
    h_face: np.ndarray
    h_vent: np.ndarray
    mdot_barrel: np.ndarray
    provenance: Optional[RotorCFDProvenance] = None
    synthesized: bool = True
    warnings: list = field(default_factory=list)

    def h_face_at(self, speed_ms) -> np.ndarray:
        """Interpolate face h_c at arbitrary speed(s); clamps outside the swept range."""
        s = np.asarray(speed_ms, float)
        return np.interp(np.abs(s), self.speeds_ms, self.h_face,
                         left=self.h_face[0], right=self.h_face[-1])

    def h_vent_at(self, speed_ms) -> np.ndarray:
        s = np.asarray(speed_ms, float)
        return np.interp(np.abs(s), self.speeds_ms, self.h_vent,
                         left=self.h_vent[0], right=self.h_vent[-1])


def build_convective_map(solver: Optional[RotorCFDSolver] = None,
                         speeds_ms: Optional[Sequence[float]] = None,
                         rolling_radius_m: float = 0.22,
                         rho: float = 1.225,
                         workdir: str = "/tmp/kinematik_wheeltunnel") -> ConvectiveMap:
    """
    Sweep the wheel-tunnel solver across a speed range and assemble the h_c(speed)
    map. With the reference solver this is instant; with a real solver each speed is
    a CFD case (the run matrix), so keep the sweep coarse and interpolate.

    If a real backend raises `RotorSolverUnavailable` at a point, that point is left
    out and a warning is recorded — the map never invents an h_c to fill the hole.
    Never raises.
    """
    solver = solver or ReferenceRotorCFD()
    speeds = np.asarray(speeds_ms if speeds_ms is not None
                        else [5, 10, 15, 20, 25, 30, 35], float)
    speeds = np.sort(np.unique(np.clip(speeds, 0.1, None)))
    hf, hv, md, kept = [], [], [], []
    warnings: list[str] = []
    prov = None
    for v in speeds:
        pt = WheelTunnelPoint(speed_ms=float(v), rolling_radius_m=rolling_radius_m,
                              rho=rho)
        try:
            res = solver.run_case(pt, workdir)
        except RotorSolverUnavailable as e:
            warnings.append(f"speed {v:.0f} m/s: {e}")
            continue
        if not res.is_usable():
            warnings.append(f"speed {v:.0f} m/s: solver returned no usable h_c.")
            continue
        prov = res.provenance or prov
        hf.append(res.h_face_w_m2k)
        hv.append(res.h_vent_w_m2k if res.h_vent_w_m2k is not None else res.h_face_w_m2k)
        md.append(res.mdot_barrel_kg_s if res.mdot_barrel_kg_s is not None else 0.0)
        kept.append(v)

    if not kept:
        warnings.append("No usable convective points; falling back to a constant, "
                        "clearly-synthesized h_c=60 W/m²K so downstream can run.")
        kept = [1.0, 100.0]
        hf = [60.0, 60.0]
        hv = [90.0, 90.0]
        md = [0.0, 0.0]

    synth = not (prov is not None and prov.is_correlated)
    return ConvectiveMap(
        speeds_ms=np.asarray(kept, float), h_face=np.asarray(hf, float),
        h_vent=np.asarray(hv, float), mdot_barrel=np.asarray(md, float),
        provenance=prov, synthesized=synth, warnings=warnings)


# =========================================================================== #
#  SECTION 2 — ROTOR GEOMETRY (the thing the optimiser is allowed to change)
# =========================================================================== #
@dataclass
class RotorGeometry:
    """
    The rotor as the optimiser sees it: a friction ring (the swept annulus the pads
    grip) on a hat that bolts to the hub. The fields the optimiser is allowed to
    move — `ring_thickness_mm`, `hat_mass_kg`, `n_drillings`, `vent_fraction` — are
    exactly the mass-reduction levers in the brief: thin the ring, shrink the hat,
    drill holes, open vents.

    `is_vented` switches between a solid disc and a two-piece vented disc with
    internal channels (which see the higher vent-channel h_c from the cooling map).
    """
    outer_radius_mm: float = 110.0
    inner_radius_mm: float = 70.0     # inner edge of the friction ring
    ring_thickness_mm: float = 7.0    # the primary mass + thermal-capacity lever
    is_vented: bool = False
    vent_fraction: float = 0.0        # 0..0.6: fraction of ring volume removed as vents
    n_drillings: int = 0              # cross-drilled holes (mass + a little area)
    drill_radius_mm: float = 3.0
    hat_mass_kg: float = 0.35         # the hat/bell mass (separate lever)
    material: RotorMaterial = field(default_factory=RotorMaterial)
    hat_material: Optional[RotorMaterial] = None   # None -> same as ring

    # --- derived geometry -------------------------------------------------- #
    def ring_solid_volume_m3(self) -> float:
        ro = self.outer_radius_mm / 1000.0
        ri = self.inner_radius_mm / 1000.0
        t = self.ring_thickness_mm / 1000.0
        return math.pi * (ro * ro - ri * ri) * t

    def ring_volume_m3(self) -> float:
        """Ring volume after vents and drillings are removed."""
        v = self.ring_solid_volume_m3()
        v *= (1.0 - max(min(self.vent_fraction, 0.6), 0.0))
        drill_vol = (self.n_drillings
                     * math.pi * (self.drill_radius_mm / 1000.0) ** 2
                     * (self.ring_thickness_mm / 1000.0))
        return max(v - drill_vol, 1e-9)

    def ring_mass_kg(self) -> float:
        return self.ring_volume_m3() * self.material.density

    def total_mass_kg(self) -> float:
        return self.ring_mass_kg() + self.hat_mass_kg

    def ring_face_area_m2(self) -> float:
        """Both annular faces exposed to convection (the dominant cooling area)."""
        ro = self.outer_radius_mm / 1000.0
        ri = self.inner_radius_mm / 1000.0
        return 2.0 * math.pi * (ro * ro - ri * ri)

    def vent_area_m2(self) -> float:
        """Extra wetted area from internal vent channels + drillings (cooling gain)."""
        if not self.is_vented and self.n_drillings == 0:
            return 0.0
        # vent channels: model their wetted area as proportional to removed volume
        mean_r = 0.5 * (self.outer_radius_mm + self.inner_radius_mm) / 1000.0
        vent_a = self.vent_fraction * 2.0 * math.pi * mean_r * \
            (self.ring_thickness_mm / 1000.0) * 4.0
        drill_a = (self.n_drillings * 2.0 * math.pi
                   * (self.drill_radius_mm / 1000.0)
                   * (self.ring_thickness_mm / 1000.0))
        return vent_a + drill_a

    def ring_thermal_capacity_j_per_k(self) -> float:
        return self.ring_mass_kg() * self.material.cp

    def hat_thermal_capacity_j_per_k(self) -> float:
        mat = self.hat_material or self.material
        return self.hat_mass_kg * mat.cp


# =========================================================================== #
#  SECTION 3 — BRAKING-ENERGY TRACE (the transient heat input from the lap)
# =========================================================================== #
def braking_power_trace(lap, lap_params,
                        front_bias: float = 0.62,
                        corner: str = "front",
                        rolling_radius_m: float = 0.22,
                        warn=lambda m: None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Turn a QSS `LapResult` into the heat-flux history into ONE rotor over the lap.

    The lap sim is distance-indexed and reports `long_g` (braking = negative). The
    braking power at the contact patch is  P_brake = F_brake · v = m·|a_brake|·v,
    deposited as heat in the rotors only while the car is braking; it is ZERO on
    throttle and coast. That is the alternating heat-up-into-a-corner /
    cool-down-on-the-straight cycle the brief asks for — it falls straight out of
    the lap trace, no separate model needed.

    The total braking heat is split front/rear by `front_bias`, then halved (two
    rotors per axle) and assigned to one rotor by `corner` ("front" or "rear").
    Returns (time_s, q_into_rotor_W, speed_ms) on the lap's own time base.

    Duck-typed on `lap.speed`, `lap.distance`, `lap.long_g`. Never raises.
    """
    try:
        v = np.asarray(lap.speed, float)
        d = np.asarray(lap.distance, float)
        lg = np.asarray(lap.long_g, float)
        n = min(v.size, d.size, lg.size)
        if n < 2:
            warn("Lap trace too short to build a braking-power history.")
            return np.zeros(1), np.zeros(1), np.zeros(1)
        v, d, lg = v[:n], d[:n], lg[:n]
        g = float(getattr(lap_params, "g", 9.81))
        mass = float(getattr(lap_params, "mass", 280.0))

        # recover a time base from distance / speed (pack_thermal does the same)
        t = np.zeros(n)
        q = np.zeros(n)
        axle_frac = front_bias if corner == "front" else (1.0 - front_bias)
        for i in range(1, n):
            ds = d[i] - d[i - 1]
            v_avg = max(0.5 * (v[i] + v[i - 1]), getattr(lap_params, "V_MIN", 0.5))
            dt = ds / v_avg if (np.isfinite(ds) and ds > 0) else 0.0
            t[i] = t[i - 1] + dt
            a = lg[i] * g if np.isfinite(lg[i]) else 0.0
            if a < 0.0:                       # braking only
                P_total = mass * abs(a) * v[i]     # W of braking power, whole car
                P_axle = P_total * axle_frac        # this axle's share
                q[i] = P_axle / 2.0                 # one of two rotors on the axle
            # else q stays 0: throttle / coast deposits no brake heat
        if t[-1] <= 0:
            warn("Recovered zero lap time; braking trace is degenerate.")
        return t, q, v
    except Exception as e:                    # never raise, like the rest of the repo
        warn(f"braking_power_trace failed ({type(e).__name__}); returned empty.")
        return np.zeros(1), np.zeros(1), np.zeros(1)


# =========================================================================== #
#  SECTION 4 — TRANSIENT THERMAL FEA (the lumped rotor network)
# =========================================================================== #
@dataclass
class RotorThermalParams:
    """
    Parameters of the lumped rotor->hat->hub->caliper->fluid network. The masses
    and areas come from `RotorGeometry`; what lives HERE is everything that needs a
    rig or a thermocouple to know honestly — the contact conductances and the
    caliper/fluid branch — plus the ambient and the heat-split.

    `calibrated` is the single most important field, same as ThermalParams /
    CellParams: leave it False and every temperature is flagged `synthesized`. Set
    it True only when these conductances were fitted to a measured rotor + caliper
    temperature trace.
    """
    ambient_c: float = 35.0
    fric_to_rotor: float = 0.92      # fraction of brake power into the disc (rest -> pad)

    # conduction conductances (W/K) between lumped nodes
    k_ring_hat: float = 14.0         # friction ring  <-> hat/bell
    k_hat_hub: float = 30.0          # hat            <-> wheel hub (big sink)
    k_pad_caliper: float = 2.2       # ring -> pad -> caliper body (small on purpose)
    k_caliper_fluid: float = 4.5     # caliper body   <-> piston/fluid interface

    # the caliper/fluid branch thermal masses (J/K)
    c_caliper_j_k: float = 850.0     # aluminium caliper body
    c_fluid_j_k: float = 95.0        # fluid column under the pistons (small => fast)
    fluid_to_ambient_w_k: float = 1.5  # weak convection off the caliper to air

    # hub acts as a near-infinite sink at ambient + a small rise
    hub_node_c_j_k: float = 4000.0   # effective hub/upright thermal mass

    # radiation off the friction ring (small but not nothing when hot)
    enable_radiation: bool = True

    # provenance
    calibrated: bool = False
    fitted_to: str = ""


@dataclass
class RotorThermalResult:
    """
    Full transient result for one rotor over the lap (pack_thermal Result idiom).
    Carries the peak the optimiser reads, the time histories for plotting, and the
    pass/fail facts: did the ring exceed pad fade, did the fluid exceed its boil.
    """
    time_s: np.ndarray
    ring_temp_c: np.ndarray
    hat_temp_c: np.ndarray
    caliper_temp_c: np.ndarray
    fluid_temp_c: np.ndarray
    speed_ms: np.ndarray
    q_in_w: np.ndarray

    peak_ring_c: float
    peak_fluid_c: float
    peak_caliper_c: float

    pad_degradation_c: float
    fluid_boil_c: float

    synthesized: bool
    provenance: str
    warnings: list = field(default_factory=list)

    @property
    def ring_fades(self) -> bool:
        return self.peak_ring_c >= self.pad_degradation_c

    @property
    def fluid_boils(self) -> bool:
        return self.peak_fluid_c >= self.fluid_boil_c

    @property
    def passes(self) -> bool:
        return not (self.ring_fades or self.fluid_boils)

    def headroom_c(self) -> tuple[float, float]:
        """(pad headroom, fluid headroom) in °C — positive = safe margin."""
        return (self.pad_degradation_c - self.peak_ring_c,
                self.fluid_boil_c - self.peak_fluid_c)

    @staticmethod
    def failed(msg: str) -> "RotorThermalResult":
        z = np.full(1, np.nan)
        return RotorThermalResult(
            time_s=z.copy(), ring_temp_c=z.copy(), hat_temp_c=z.copy(),
            caliper_temp_c=z.copy(), fluid_temp_c=z.copy(), speed_ms=z.copy(),
            q_in_w=z.copy(), peak_ring_c=float("nan"), peak_fluid_c=float("nan"),
            peak_caliper_c=float("nan"), pad_degradation_c=float("nan"),
            fluid_boil_c=float("nan"), synthesized=True,
            provenance="FAILED", warnings=[msg])


class RotorThermalModel:
    """
    Explicit transient lumped-capacitance rotor network (pack_thermal idiom).

    Nodes and the energy balance integrated per step:
        ring:    C_ring·dT/dt = Q_brake − Q_conv(face,vent) − Q_rad
                                 − k_ring_hat·(T_ring−T_hat) − k_pad_caliper·(T_ring−T_cal)
        hat:     C_hat·dT/dt  = k_ring_hat·(T_ring−T_hat) − k_hat_hub·(T_hat−T_hub)
        caliper: C_cal·dT/dt  = k_pad_caliper·(T_ring−T_cal) − k_caliper_fluid·(T_cal−T_fluid)
                                 − fluid_to_ambient·(T_cal−T_amb)
        fluid:   C_fluid·dT/dt= k_caliper_fluid·(T_cal−T_fluid)
        hub:     large sink, drifts up slowly.

    Convection uses the speed-dependent h_c(speed) from the `ConvectiveMap`, sampled
    at each lap speed — face area always, vent area only if the rotor is vented.
    Forward-Euler with adaptive sub-steps for stability. Never raises.
    """

    def __init__(self, geom: RotorGeometry,
                 cmap: ConvectiveMap,
                 params: Optional[RotorThermalParams] = None,
                 pad: Optional[PadSpec] = None,
                 fluid: Optional[BrakeFluid] = None,
                 use_wet_boil: bool = True):
        self.geom = geom
        self.cmap = cmap
        self.params = params or RotorThermalParams()
        self.pad = pad or PadSpec()
        self.fluid = fluid or BrakeFluid()
        self.use_wet_boil = use_wet_boil
        self.warnings: list[str] = []

    def _warn(self, m: str):
        if m and m not in self.warnings:
            self.warnings.append(m)

    def simulate(self, time_s: np.ndarray, q_in_w: np.ndarray,
                 speed_ms: np.ndarray, n_laps: int = 1) -> RotorThermalResult:
        try:
            t_in = np.nan_to_num(np.asarray(time_s, float).ravel(), nan=0.0)
            q_in = np.nan_to_num(np.asarray(q_in_w, float).ravel(),
                                 nan=0.0, posinf=0.0, neginf=0.0)
            v_in = np.nan_to_num(np.asarray(speed_ms, float).ravel(), nan=0.0)
            m = min(t_in.size, q_in.size, v_in.size)
            if m < 2:
                return RotorThermalResult.failed("Braking trace too short to integrate.")
            t_in, q_in, v_in = t_in[:m], q_in[:m], v_in[:m]

            n_laps = max(int(n_laps), 1)
            if n_laps > 1:    # stitch laps end-to-end (pattern repeats, time accrues)
                dt0 = (t_in[1] - t_in[0]) if t_in.size > 1 else 0.1
                ts, qs, vs = [t_in], [q_in], [v_in]
                for _ in range(1, n_laps):
                    ts.append(t_in - t_in[0] + ts[-1][-1] + dt0)
                    qs.append(q_in)
                    vs.append(v_in)
                t_in = np.concatenate(ts)
                q_in = np.concatenate(qs)
                v_in = np.concatenate(vs)

            p = self.params
            g = self.geom
            # capacities
            C_ring = max(g.ring_thermal_capacity_j_per_k(), 1.0)
            C_hat = max(g.hat_thermal_capacity_j_per_k(), 1.0)
            C_cal = max(p.c_caliper_j_k, 1.0)
            C_fluid = max(p.c_fluid_j_k, 1.0)
            C_hub = max(p.hub_node_c_j_k, 1.0)

            A_face = g.ring_face_area_m2()
            A_vent = g.vent_area_m2() if g.is_vented or g.n_drillings else 0.0
            emis = g.material.emissivity if p.enable_radiation else 0.0
            sigma = 5.670e-8

            # h_c lookups vs the lap speed
            h_face = self.cmap.h_face_at(v_in)               # (m,)
            h_vent = self.cmap.h_vent_at(v_in)

            Tamb = p.ambient_c
            T_ring = Tamb; T_hat = Tamb; T_cal = Tamb
            T_fluid = Tamb; T_hub = Tamb

            nt = t_in.size
            ring_h = np.empty(nt); hat_h = np.empty(nt)
            cal_h = np.empty(nt); fl_h = np.empty(nt)
            ring_h[0] = T_ring; hat_h[0] = T_hat
            cal_h[0] = T_cal; fl_h[0] = T_fluid

            # stability: dt < C_min / (sum of conductances out of the fastest node).
            # the fluid node is small and tightly coupled -> it sets the limit.
            g_fluid_out = p.k_caliper_fluid
            dt_stable_fluid = 0.5 * C_fluid / max(g_fluid_out, 1e-9)
            g_ring_out = (p.k_ring_hat + p.k_pad_caliper
                          + np.max(h_face) * A_face + np.max(h_vent) * A_vent)
            dt_stable_ring = 0.5 * C_ring / max(g_ring_out, 1e-9)
            dt_stable = max(min(dt_stable_fluid, dt_stable_ring), 1e-4)

            peak_ring = T_ring; peak_fluid = T_fluid; peak_cal = T_cal

            for s in range(1, nt):
                dt_macro = max(t_in[s] - t_in[s - 1], 0.0)
                if dt_macro <= 0.0:
                    ring_h[s] = T_ring; hat_h[s] = T_hat
                    cal_h[s] = T_cal; fl_h[s] = T_fluid
                    continue
                n_sub = max(int(math.ceil(dt_macro / dt_stable)), 1)
                if n_sub > 8000:
                    n_sub = 8000
                    self._warn("Rotor thermal sub-stepping capped at 8000/step; "
                               "results near that step are coarse.")
                dt = dt_macro / n_sub
                q_brake = p.fric_to_rotor * q_in[s]
                hf = h_face[s]; hv = h_vent[s]
                for _ in range(n_sub):
                    # ring losses
                    q_conv = hf * A_face * (Tamb - T_ring) + hv * A_vent * (Tamb - T_ring)
                    q_rad = (emis * sigma * A_face
                             * ((T_ring + 273.15) ** 4 - (Tamb + 273.15) ** 4)) \
                        if emis > 0 else 0.0
                    q_ring_hat = p.k_ring_hat * (T_ring - T_hat)
                    q_ring_cal = p.k_pad_caliper * (T_ring - T_cal)
                    dT_ring = (q_brake + q_conv - q_rad - q_ring_hat - q_ring_cal) \
                        * (dt / C_ring)
                    # hat
                    q_hat_hub = p.k_hat_hub * (T_hat - T_hub)
                    dT_hat = (q_ring_hat - q_hat_hub) * (dt / C_hat)
                    # caliper
                    q_cal_fluid = p.k_caliper_fluid * (T_cal - T_fluid)
                    q_cal_amb = p.fluid_to_ambient_w_k * (T_cal - Tamb)
                    dT_cal = (q_ring_cal - q_cal_fluid - q_cal_amb) * (dt / C_cal)
                    # fluid
                    dT_fluid = (q_cal_fluid) * (dt / C_fluid)
                    # hub (slow sink)
                    dT_hub = (q_hat_hub
                              - 8.0 * (T_hub - Tamb)) * (dt / C_hub)

                    T_ring += dT_ring; T_hat += dT_hat
                    T_cal += dT_cal; T_fluid += dT_fluid; T_hub += dT_hub

                T_ring = float(np.nan_to_num(T_ring, nan=Tamb))
                T_fluid = float(np.nan_to_num(T_fluid, nan=Tamb))
                ring_h[s] = T_ring; hat_h[s] = T_hat
                cal_h[s] = T_cal; fl_h[s] = T_fluid
                peak_ring = max(peak_ring, T_ring)
                peak_fluid = max(peak_fluid, T_fluid)
                peak_cal = max(peak_cal, T_cal)

            synth = (self.cmap.synthesized or not self.params.calibrated)
            boil = self.fluid.wet_boil_c if self.use_wet_boil else self.fluid.dry_boil_c
            prov = self._provenance_string(synth)
            return RotorThermalResult(
                time_s=t_in, ring_temp_c=ring_h, hat_temp_c=hat_h,
                caliper_temp_c=cal_h, fluid_temp_c=fl_h, speed_ms=v_in,
                q_in_w=q_in, peak_ring_c=peak_ring, peak_fluid_c=peak_fluid,
                peak_caliper_c=peak_cal, pad_degradation_c=self.pad.degradation_c,
                fluid_boil_c=boil, synthesized=synth, provenance=prov,
                warnings=list(self.warnings) + list(self.cmap.warnings))
        except Exception as e:
            return RotorThermalResult.failed(
                f"Rotor thermal integration failed ({type(e).__name__}): {e}")

    def _provenance_string(self, synth: bool) -> str:
        cmap_status = (self.cmap.provenance.status()
                       if self.cmap.provenance else "no cooling-map provenance")
        if synth:
            return ("SYNTHESIZED rotor temperatures — a transparent lumped network "
                    "with representative (uncalibrated) conductances and an "
                    f"uncorrelated cooling map [{cmap_status}]. Trust the SHAPE and "
                    "relative ranking; do not report absolute °C or a pass/fail "
                    "margin as fact until the masses, contact conductances and h_c "
                    "are calibrated against a measured rotor+caliper trace.")
        return (f"Calibrated rotor thermal model (fitted to {self.params.fitted_to}); "
                f"cooling map [{cmap_status}].")


# --------------------------------------------------------------------------- #
#  Top-level convenience: lap -> cooling map -> transient rotor temperatures
# --------------------------------------------------------------------------- #
def simulate_rotor_thermal(lap, lap_params,
                           geom: Optional[RotorGeometry] = None,
                           cmap: Optional[ConvectiveMap] = None,
                           cfd_solver: Optional[RotorCFDSolver] = None,
                           params: Optional[RotorThermalParams] = None,
                           pad: Optional[PadSpec] = None,
                           fluid: Optional[BrakeFluid] = None,
                           corner: str = "front",
                           front_bias: float = 0.62,
                           rolling_radius_m: float = 0.22,
                           use_wet_boil: bool = True,
                           n_laps: Optional[int] = None) -> RotorThermalResult:
    """
    The headline: take a virtual lap and predict the TRANSIENT temperature of one
    brake rotor over it — friction ring, hat, caliper body and the fluid at the
    pistons — using a speed-dependent convective coefficient from the wheel-tunnel
    seam. Reports the peak ring temperature (vs pad fade) and the peak fluid
    temperature (vs the fluid's boil point).

    Steps:
      1. build/accept the h_c(speed) cooling map (the wheel-in-tunnel layer),
      2. derive the braking heat-flux trace from the lap (P_brake = F·v, brake-only),
      3. integrate the transient rotor->hat->caliper->fluid network over n_laps.

    `lap` only needs `.speed`, `.distance`, `.long_g` (duck-typed). Never raises.
    """
    geom = geom or RotorGeometry()
    if cmap is None:
        cmap = build_convective_map(cfd_solver, rolling_radius_m=rolling_radius_m)
    model = RotorThermalModel(geom, cmap, params=params, pad=pad, fluid=fluid,
                              use_wet_boil=use_wet_boil)
    t, q, v = braking_power_trace(lap, lap_params, front_bias=front_bias,
                                  corner=corner, rolling_radius_m=rolling_radius_m,
                                  warn=model._warn)
    laps = n_laps
    if laps is None:
        laps = int(getattr(lap, "meta", {}).get("laps", 1)) if hasattr(lap, "meta") else 1
        laps = max(laps, 1)
    return model.simulate(t, q, v, n_laps=laps)


# =========================================================================== #
#  SECTION 5 — FLUID-BOIL CHECK (consecutive-stops soak to the pistons)
# =========================================================================== #
@dataclass
class FluidBoilCheck:
    """Verdict of the caliper-fluid boil check under the run that was simulated."""
    peak_fluid_c: float
    boil_c: float
    fluid_name: str
    using_wet: bool
    margin_c: float                  # boil − peak; negative = it boiled
    boils: bool
    note: str = ""

    def summary(self) -> str:
        which = "wet" if self.using_wet else "dry"
        verdict = "BOILS" if self.boils else "ok"
        return (f"Caliper fluid ({self.fluid_name}, {which} boil "
                f"{self.boil_c:.0f}°C): peak piston-fluid {self.peak_fluid_c:.0f}°C "
                f"-> {verdict} ({self.margin_c:+.0f}°C margin). {self.note}")


def fluid_boil_check(result: RotorThermalResult,
                     fluid: Optional[BrakeFluid] = None,
                     using_wet: bool = True) -> FluidBoilCheck:
    """
    Reduce a transient rotor result to the single fluid-survival fact: did the
    localized temperature at the caliper pistons reach the boiling point of the
    racing brake fluid under the consecutive stops simulated? Designing to the WET
    boil point is the safe default — that's the fluid the car actually has after a
    season of moisture pickup.
    """
    fluid = fluid or BrakeFluid()
    boil = fluid.wet_boil_c if using_wet else fluid.dry_boil_c
    peak = result.peak_fluid_c
    margin = boil - peak
    note = ("Heat reaches the fluid by conduction ring->pad->caliper->piston; the "
            "fluid node is small, so consecutive stops soak it faster than it sheds."
            if result.synthesized is False else
            "Synthesized result — treat the margin as a relative indicator.")
    return FluidBoilCheck(peak_fluid_c=peak, boil_c=boil, fluid_name=fluid.name,
                          using_wet=using_wet, margin_c=margin,
                          boils=peak >= boil, note=note)


# =========================================================================== #
#  SECTION 6 — MASS / VENT TOPOLOGY OPTIMISATION
# =========================================================================== #
@dataclass
class RotorCandidate:
    """One trial rotor geometry and what the transient model said about it."""
    geom: RotorGeometry
    result: RotorThermalResult
    mass_kg: float
    peak_ring_c: float
    peak_fluid_c: float
    passes: bool

    def label(self) -> str:
        v = "vented" if self.geom.is_vented else "solid"
        return (f"t={self.geom.ring_thickness_mm:.1f}mm, {v}, "
                f"vent={self.geom.vent_fraction:.0%}, "
                f"{self.geom.n_drillings} holes, hat={self.geom.hat_mass_kg:.2f}kg "
                f"-> {self.mass_kg:.2f}kg, ring {self.peak_ring_c:.0f}°C, "
                f"fluid {self.peak_fluid_c:.0f}°C, "
                f"{'PASS' if self.passes else 'FAIL'}")


@dataclass
class RotorOptimization:
    """
    Result of the mass-reduction search: every candidate evaluated, and the
    lightest one that PASSES both the pad-fade and fluid-boil limits. If none
    passes, `best` is None and the caller is told the baseline is already at the
    limit — the optimiser never returns a rotor that fails.
    """
    candidates: list                 # all evaluated, any order
    passing: list                    # subset that pass, sorted lightest-first
    baseline: RotorCandidate
    synthesized: bool
    provenance: str
    warnings: list = field(default_factory=list)

    @property
    def best(self) -> Optional[RotorCandidate]:
        return self.passing[0] if self.passing else None

    def summary(self) -> str:
        lines = [f"Rotor mass-reduction study ({len(self.candidates)} candidates):"]
        b = self.baseline
        lines.append(f"  baseline: {b.label()}")
        if self.best is None:
            lines.append("  -> NO lighter candidate passes both limits; the baseline "
                         "is already near the thermal limit. Add cooling (vents, a "
                         "duct, more h_c) before removing mass.")
            return "\n".join(lines)
        win = self.best
        saved = b.mass_kg - win.mass_kg
        lines.append(f"  -> lightest PASSING: {win.label()}")
        lines.append(f"     saves {saved*1000:.0f} g of unsprung rotating mass per "
                     f"corner vs baseline, with ring peak still "
                     f"{win.result.headroom_c()[0]:.0f}°C under pad fade and fluid "
                     f"{win.result.headroom_c()[1]:.0f}°C under boil.")
        if self.synthesized:
            lines.append("     NOTE: synthesized thermal model — ranking is trustworthy, "
                         "absolute margins are provisional until calibrated.")
        return "\n".join(lines)


def rotor_candidate_grid(baseline: RotorGeometry,
                         thickness_mm: Optional[Sequence[float]] = None,
                         vent_fraction: Optional[Sequence[float]] = None,
                         n_drillings: Optional[Sequence[int]] = None,
                         hat_mass_kg: Optional[Sequence[float]] = None,
                         vented: Optional[Sequence[bool]] = None) -> list[RotorGeometry]:
    """
    Build a factorial grid of trial rotor geometries off a baseline — the discrete
    stand-in for topology optimisation: each axis is a real manufacturing lever
    (ring thickness, vent fraction, cross-drillings, hat mass, solid-vs-vented).
    Keep the grid modest; the transient solve runs per candidate.
    """
    import itertools
    th = thickness_mm if thickness_mm is not None else [baseline.ring_thickness_mm]
    vf = vent_fraction if vent_fraction is not None else [baseline.vent_fraction]
    nd = n_drillings if n_drillings is not None else [baseline.n_drillings]
    hm = hat_mass_kg if hat_mass_kg is not None else [baseline.hat_mass_kg]
    vt = vented if vented is not None else [baseline.is_vented]
    out = []
    for t, vfi, ndi, hmi, vti in itertools.product(th, vf, nd, hm, vt):
        out.append(RotorGeometry(
            outer_radius_mm=baseline.outer_radius_mm,
            inner_radius_mm=baseline.inner_radius_mm,
            ring_thickness_mm=float(t), is_vented=bool(vti),
            vent_fraction=float(vfi), n_drillings=int(ndi),
            drill_radius_mm=baseline.drill_radius_mm, hat_mass_kg=float(hmi),
            material=baseline.material, hat_material=baseline.hat_material))
    return out


def optimize_rotor(lap, lap_params,
                   candidates: Sequence[RotorGeometry],
                   cmap: Optional[ConvectiveMap] = None,
                   cfd_solver: Optional[RotorCFDSolver] = None,
                   params: Optional[RotorThermalParams] = None,
                   pad: Optional[PadSpec] = None,
                   fluid: Optional[BrakeFluid] = None,
                   corner: str = "front",
                   front_bias: float = 0.62,
                   rolling_radius_m: float = 0.22,
                   use_wet_boil: bool = True,
                   n_laps: Optional[int] = None,
                   baseline: Optional[RotorGeometry] = None) -> RotorOptimization:
    """
    Score every candidate rotor on the SAME lap and cooling map, and return the
    lightest one whose transient peak keeps the ring under pad fade AND the fluid
    under its boil point. This is the brief's core trade made honest: mill away mass
    only where the transient model proves the thermal mass isn't needed to hold
    peak temperature, and never ship a rotor that fails a limit.

    The cooling map is built once (one wheel-tunnel sweep) and shared across
    candidates, so the only per-candidate cost is the instant lumped solve.
    Never raises.
    """
    if cmap is None:
        cmap = build_convective_map(cfd_solver, rolling_radius_m=rolling_radius_m)
    base_geom = baseline or candidates[0]

    def _score(geom: RotorGeometry) -> RotorCandidate:
        res = simulate_rotor_thermal(
            lap, lap_params, geom=geom, cmap=cmap, params=params, pad=pad,
            fluid=fluid, corner=corner, front_bias=front_bias,
            rolling_radius_m=rolling_radius_m, use_wet_boil=use_wet_boil,
            n_laps=n_laps)
        return RotorCandidate(
            geom=geom, result=res, mass_kg=geom.total_mass_kg(),
            peak_ring_c=res.peak_ring_c, peak_fluid_c=res.peak_fluid_c,
            passes=res.passes)

    base_cand = _score(base_geom)
    evaluated = [_score(g) for g in candidates]
    passing = sorted((c for c in evaluated if c.passes), key=lambda c: c.mass_kg)
    synth = any(c.result.synthesized for c in evaluated) or base_cand.result.synthesized
    prov = (base_cand.result.provenance if evaluated else "no candidates")
    warns: list[str] = []
    for c in evaluated:
        for w in c.result.warnings:
            if w not in warns:
                warns.append(w)
    return RotorOptimization(
        candidates=evaluated, passing=passing, baseline=base_cand,
        synthesized=synth, provenance=prov, warnings=warns)
