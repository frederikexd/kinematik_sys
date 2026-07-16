# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================

"""
throttle_flutter_cosim.py — the co-simulation seam where a CFD solver supplies the
plate's AERODYNAMIC DAMPING DERIVATIVE, so the flutter screen stops depending on a
hand-typed coefficient.

WHY THIS MODULE EXISTS (read before adding a backend)
-----------------------------------------------------
The flutter screen in throttle_dynamics.py models the plate as a torsional
oscillator, but its one aeroelastic input — the aero-damping coefficient c_aero —
has to come from somewhere real. Typing it in is honest but weak. The right source
is a CFD study: force the plate in a small sinusoidal oscillation about an operating
angle, measure the aerodynamic moment, and extract the component IN PHASE WITH
ANGULAR VELOCITY. That in-phase component IS the aero-damping derivative; its sign
decides flutter (negative = energy fed in = flutter). This is a standard forced-
oscillation aeroelastic derivative extraction.

That solve is a meshed, unsteady CFD case — exactly the kind of thing that lives
OUTSIDE KinematiK, in Fluent / STAR-CCM+ / CFX / OpenFOAM on the team's cluster.
So, mirroring aero/cfd.py and tire_cosim.py, this module owns the SEAM, not the
solver: a typed protocol a real backend drops into, a solver-neutral case
description, a result that carries its own provenance, and a runnable REFERENCE
backend (a quasi-steady analytical estimator) that is honestly labelled trends-only
so the machinery is testable today without a license.

THE HONESTY CONTRACT (strict here, specifically)
------------------------------------------------
A CFD-extracted damping derivative LOOKS like measurement, so an unconverged or
under-resolved run is more dangerous than an obvious guess. Therefore:
  * every FlutterDerivative carries `provenance` (backend, fidelity, converged,
    correlated-against) — a number without provenance is not engineering data;
  * a backend returns `None`/raises for a channel it did not actually compute; it
    never fabricates a derivative to fill the hole;
  * `is_correlated` is False until tied to a flow-rig or wind-tunnel measurement;
  * the REFERENCE backend is labelled QUASI_STEADY (trends only) and says plainly
    it does not resolve the unsteady wake that governs real flutter — it is a
    placeholder that lets you run the seam, not a substitute for CFD.

DELIBERATE NON-GOAL: this module does not mesh or solve Navier–Stokes. A real
backend `write_case` emits solver input the team runs on their cluster; `run_case`
either drives it or raises SolverUnavailable with an actionable message. Feeding the
extracted derivative into FlutterParams closes the loop honestly: the flutter screen
then rests on a real CFD number with visible provenance, and the ANSYS/CFD run
becomes the SOURCE of the derivative rather than a step the tool pretends to skip.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional, Protocol, runtime_checkable

from .interfaces import Finding, Severity
from .throttle_dynamics import FlutterParams
from .throttle_return import AIR_DENSITY_KGM3


class FlutterFidelity(str, Enum):
    """How physically complete a flutter-derivative backend's result is."""
    QUASI_STEADY = "quasi_steady"     # analytic/quasi-steady: trends only, NOT flutter-resolving
    URANS = "urans"                   # unsteady RANS forced-oscillation: working point
    DES = "des"                       # DES/LES: resolved unsteady wake, the expensive truth
    EXPERIMENT = "experiment"         # flow-rig forced oscillation: the real thing


@dataclass
class FlutterProvenance:
    """Where a flutter aero-damping derivative came from and what it's worth."""
    backend: str
    fidelity: FlutterFidelity
    is_correlated: bool = False
    converged: bool = False
    turbulence_model: str = ""
    cell_count: Optional[int] = None
    correlated_against: str = ""       # "flow-rig forced oscillation", ...
    notes: str = ""

    def status(self) -> str:
        mesh = f", {self.cell_count/1e6:.1f}M cells" if self.cell_count else ""
        conv = "converged" if self.converged else "UNCONVERGED"
        if self.is_correlated:
            ref = self.correlated_against or "physical reference"
            return f"{self.backend} ({self.fidelity.value}{mesh}, {conv}), correlated vs {ref}"
        return f"{self.backend} ({self.fidelity.value}{mesh}, {conv}), NOT correlated"

    def as_dict(self):
        d = asdict(self)
        d["fidelity"] = self.fidelity.value
        return d


@dataclass
class OscillationCase:
    """Solver-neutral description of one forced-oscillation flutter case.

    The backend oscillates the plate by +/- `amplitude_deg` about `mean_angle_deg`
    at `frequency_hz`, in a flow of `intake_speed_ms`, and extracts the aerodynamic
    moment. Geometry (bore, plate radius, air density) closes the non-dimensional.
    """
    mean_angle_deg: float = 45.0
    amplitude_deg: float = 2.0
    frequency_hz: float = 50.0
    intake_speed_ms: float = 40.0
    plate_radius_m: float = 0.02
    plate_area_m2: float = 1.0e-3
    rho: float = AIR_DENSITY_KGM3

    def as_dict(self):
        return asdict(self)


@dataclass
class FlutterDerivative:
    """The extracted aero-damping derivative for the plate, with provenance.

    c_aero_Nms       : aerodynamic damping coefficient (N·m·s). NEGATIVE = aero feeds
                       energy into plate oscillation (flutter). None = not computed.
    ref_speed_ms     : speed it was extracted at (feeds FlutterParams scaling).
    in_phase_moment_Nm, quad_moment_Nm : the moment components (damping vs stiffness)
                       for transparency — None if not computed.
    provenance       : where it came from and whether it's trustworthy.
    """
    c_aero_Nms: Optional[float]
    ref_speed_ms: float
    in_phase_moment_Nm: Optional[float] = None
    quad_moment_Nm: Optional[float] = None
    provenance: Optional[FlutterProvenance] = None
    findings: list = field(default_factory=list)

    def is_usable(self) -> bool:
        return (self.c_aero_Nms is not None and self.provenance is not None
                and self.provenance.converged)

    def to_flutter_params(self, k_theta_Nm_per_rad: float,
                          c_struct_Nms: float = 1.0e-3) -> FlutterParams:
        """Build FlutterParams from this derivative so the screen uses the CFD number.

        Raises if the derivative isn't usable — you don't get to silently feed an
        uncomputed/unconverged derivative into the screen.
        """
        if self.c_aero_Nms is None:
            raise ValueError(
                "No aero-damping derivative was computed — cannot build FlutterParams. "
                "Run a converged forced-oscillation case first.")
        return FlutterParams(
            k_theta_Nm_per_rad=k_theta_Nm_per_rad,
            c_struct_Nms=c_struct_Nms,
            c_aero_Nms=self.c_aero_Nms,
            c_aero_ref_speed_ms=self.ref_speed_ms)

    def as_dict(self):
        return dict(c_aero_Nms=self.c_aero_Nms, ref_speed_ms=self.ref_speed_ms,
                    in_phase_moment_Nm=self.in_phase_moment_Nm,
                    quad_moment_Nm=self.quad_moment_Nm,
                    provenance=self.provenance.as_dict() if self.provenance else None,
                    findings=[f.as_dict() for f in self.findings])


class SolverUnavailable(RuntimeError):
    """Raised by a backend that can write input but cannot solve here.

    Carries an actionable message (which binary/license/cluster is missing), never a
    silent fallback to a fabricated derivative.
    """


@runtime_checkable
class FlutterSolver(Protocol):
    """The seam. A backend writes a forced-oscillation case, optionally runs it, and
    reads back the aero-damping derivative. write/run/read are split so an offline
    backend can WRITE correct Fluent/STAR-CCM+/OpenFOAM input a team runs on their
    own cluster, then READ the result — KinematiK never holding the license."""
    name: str

    def provenance(self) -> FlutterProvenance: ...

    def write_case(self, case: OscillationCase, workdir: str) -> str:
        """Write solver input for the forced-oscillation case; return entry path."""
        ...

    def run_case(self, case: OscillationCase, workdir: str) -> FlutterDerivative:
        """Run/submit the case and return the extracted derivative."""
        ...

    def read_result(self, case: OscillationCase, workdir: str) -> FlutterDerivative:
        """Parse an already-run case into a FlutterDerivative."""
        ...


# --------------------------------------------------------------------------- #
#  Reference backend — quasi-steady analytic. RUNNABLE, but trends-only.
# --------------------------------------------------------------------------- #
class QuasiSteadyFlutterModel:
    """A runnable REFERENCE backend so the seam is testable without a CFD license.

    It estimates the aero-damping derivative from quasi-steady strip theory: the
    instantaneous aerodynamic moment is taken as proportional to dynamic pressure and
    a moment-slope, and the damping derivative is the part proportional to the plate's
    angular velocity through the induced angle-of-attack rate. This captures the
    SIGN and TREND (how damping scales with speed and plate angle) but does NOT
    resolve the unsteady wake / vortex shedding that actually governs flutter.

    It is therefore labelled QUASI_STEADY and `converged=True` only in the trivial
    sense that an analytic formula "converges" — `is_correlated` stays False. Treat
    its number as a placeholder to exercise the loop, NOT as a flutter prediction.
    Swap in a URANS/DES backend for engineering trust.

    moment_slope_per_rad : dC_M/dalpha, the plate's moment coefficient slope. Default
        is a generic flat-plate-ish value; a real value comes from steady CFD or a
        flow bench. This is exposed, not hidden, because it drives the magnitude.
    reduces_stability : if the geometry/mounting is such that the quasi-steady term is
        destabilising (moment leads velocity), set True to return a NEGATIVE damping
        (flutter-prone) — the honest way to represent a known destabilising layout
        without pretending the analytic model discovered it.
    """
    name = "reference-quasisteady"

    def __init__(self, moment_slope_per_rad: float = 0.9,
                 reduces_stability: bool = False):
        self.moment_slope_per_rad = float(moment_slope_per_rad)
        self.reduces_stability = bool(reduces_stability)

    def provenance(self) -> FlutterProvenance:
        return FlutterProvenance(
            backend=self.name, fidelity=FlutterFidelity.QUASI_STEADY,
            is_correlated=False, converged=True,
            turbulence_model="none (quasi-steady analytic)",
            notes=("Trends/sign only — does NOT resolve the unsteady wake that "
                   "governs flutter. Placeholder for the co-sim loop; replace with "
                   "URANS/DES or a flow rig before trusting a flutter call."))

    def _derivative(self, case: OscillationCase) -> float:
        # dynamic pressure
        q = 0.5 * case.rho * case.intake_speed_ms ** 2
        A = max(case.plate_area_m2, 0.0)
        r = max(case.plate_radius_m, 0.0)
        # quasi-steady aero-damping magnitude ~ q*A*r * (dCM/dalpha) * (r / V)
        # (the r/V converts plate tip speed to an induced angle-of-attack rate)
        V = max(case.intake_speed_ms, 1e-6)
        mag = q * A * r * self.moment_slope_per_rad * (r / V)
        # sign: stabilising (positive damping) unless the layout is known destabilising
        return -mag if self.reduces_stability else +mag

    def write_case(self, case: OscillationCase, workdir: str) -> str:
        import json
        import os
        os.makedirs(workdir, exist_ok=True)
        path = os.path.join(workdir, "flutter_oscillation_case.json")
        with open(path, "w") as f:
            json.dump({"case": case.as_dict(), "backend": self.name,
                       "note": "forced-oscillation flutter-derivative case"}, f,
                      indent=2)
        return path

    def run_case(self, case: OscillationCase, workdir: str) -> FlutterDerivative:
        c = self._derivative(case)
        findings = [Finding(
            "flutter-cosim", Severity.WARN,
            "Aero-damping derivative from the QUASI-STEADY reference backend — trends "
            "and sign only, NOT a flutter prediction. It does not resolve the unsteady "
            "wake. Use it to exercise the flutter loop; run a URANS/DES forced-"
            "oscillation case (or a flow rig) for an engineering number.",
            subsystems=["brakes", "powertrain"],
            detail=dict(backend=self.name, c_aero=c))]
        return FlutterDerivative(
            c_aero_Nms=c, ref_speed_ms=case.intake_speed_ms,
            in_phase_moment_Nm=c * (2 * math.pi * case.frequency_hz)
            * math.radians(case.amplitude_deg),
            quad_moment_Nm=None,
            provenance=self.provenance(), findings=findings)

    def read_result(self, case: OscillationCase, workdir: str) -> FlutterDerivative:
        # analytic backend: reading == running (no external artefact to parse)
        return self.run_case(case, workdir)


# --------------------------------------------------------------------------- #
#  Real-solver stub — writes input, refuses to fake a solve
# --------------------------------------------------------------------------- #
class ExternalCFDFlutterBackend:
    """Skeleton for a real URANS/DES backend (Fluent / STAR-CCM+ / OpenFOAM).

    It writes a correct forced-oscillation case description the team runs on their
    cluster, and REFUSES to run here — raising SolverUnavailable rather than
    inventing a derivative. `read_result` parses a results file the team produced.
    This is the honest hand-off: KinematiK drives the setup and consumes the answer;
    the Navier–Stokes solve happens in the licensed binary.
    """
    def __init__(self, name: str = "external-urans",
                 fidelity: FlutterFidelity = FlutterFidelity.URANS):
        self.name = name
        self._fidelity = fidelity

    def provenance(self) -> FlutterProvenance:
        return FlutterProvenance(backend=self.name, fidelity=self._fidelity,
                                 is_correlated=False, converged=False,
                                 notes="External solve — provenance filled on read_result.")

    def write_case(self, case: OscillationCase, workdir: str) -> str:
        import json
        import os
        os.makedirs(workdir, exist_ok=True)
        path = os.path.join(workdir, f"{self.name}_flutter_case.json")
        with open(path, "w") as f:
            json.dump({"case": case.as_dict(), "backend": self.name,
                       "instructions": (
                           "Run a forced-oscillation study: prescribe plate motion "
                           "theta(t)=mean+amp*sin(2*pi*f*t); monitor aerodynamic moment "
                           "about the shaft; extract the component in phase with "
                           "theta_dot -> c_aero (N·m·s). Write it to "
                           f"{self.name}_result.json as {{'c_aero_Nms':..., "
                           "'converged':true, 'cell_count':..., 'turbulence_model':...}}."
                       )}, f, indent=2)
        return path

    def case_artifacts(self, case: OscillationCase) -> dict:
        """Return {filename: text_content} for the forced-oscillation case, in
        formats a team with ANSYS/SolidWorks can actually use — no /tmp, no API.

        Produces:
          * a Fluent journal (.jou)  — a TUI script the team runs/adapts in Fluent,
          * a parameters table (.csv) — the case values to read into any setup,
          * a setup sheet (.txt)      — plain instructions + boundary conditions,
          * the JSON (.json)          — machine round-trip for reading a result back.
        All are strings so the UI can offer them as direct downloads.
        """
        import json
        c = case
        omega = 2.0 * math.pi * c.frequency_hz
        amp_rad = math.radians(c.amplitude_deg)
        q = 0.5 * c.rho * c.intake_speed_ms ** 2

        # --- Fluent journal (TUI). Documented, editable; not a black box. --------
        jou = f"""; ============================================================
; ANSYS Fluent journal — throttle-plate forced-oscillation
; flutter aero-damping study, generated by KinematiK.
; Prescribe plate motion  theta(t) = {c.mean_angle_deg} + {c.amplitude_deg}*sin(2*pi*{c.frequency_hz}*t) [deg]
; in a {c.intake_speed_ms} m/s flow. Monitor the aerodynamic moment about the
; plate shaft; extract the component IN PHASE WITH theta_dot -> c_aero (N.m.s).
; NEGATIVE c_aero = aero feeds energy into the oscillation = flutter risk.
; Fill in the meshed case file, wall/shaft zone names, and moment centre below.
; ============================================================
; --- read your meshed case (edit the path) ---
/file/read-case "throttle_plate.msh"
; --- turbulence: URANS is the working point; DES for the resolved wake ---
/define/models/unsteady-2nd-order? yes
/define/models/viscous/kw-sst? yes
; --- freestream / inlet velocity ---
/define/boundary-conditions/velocity-inlet inlet () no {c.intake_speed_ms} no 0 no 300 no no
; --- reference values (for coefficient monitors) ---
/report/reference-values/area {c.plate_area_m2}
/report/reference-values/length {c.plate_radius_m}
/report/reference-values/velocity {c.intake_speed_ms}
/report/reference-values/density {c.rho}
; --- prescribe the plate oscillation via a rigid-body/DEFINE_CG_MOTION UDF ---
;     theta(t) = mean + amp*sin(omega*t), omega = {omega:.4f} rad/s, amp = {amp_rad:.5f} rad
;     hook your compiled UDF here:
; /define/user-defined/compiled-functions load "libudf"
; /define/dynamic-mesh/... (assign the plate zone to the CG motion UDF)
; --- moment monitor about the shaft (edit zone name + moment centre) ---
/solve/monitors/force/set-moment-monitor moment_z yes plate () moment-center 0 0 0 moment-axis 0 0 1 yes "moment_z.out" yes
; --- time step: resolve the oscillation (>=40 steps/cycle) ---
/solve/set/time-step {1.0/(40.0*c.frequency_hz):.6e}
/solve/dual-time-iterate {int(40*4)} 20
; --- write the moment history; post-process the in-phase component offline ---
/file/write-transient-...
; ============================================================
; POST: c_aero = -(work per cycle)/(pi*amp^2*omega)  [N.m.s]
;   work per cycle = closed integral of  M(t) * dtheta   over one cycle
;   (the moment component in phase with theta_dot). Sign convention: c_aero<0 => flutter.
; Put the extracted value in {self.name}_result.json (see the JSON file).
; ============================================================
"""

        # --- parameters CSV (read into any setup / SolidWorks Flow study) -------
        csv = (
            "parameter,value,unit,note\n"
            f"mean_plate_angle,{c.mean_angle_deg},deg,operating angle to oscillate about\n"
            f"oscillation_amplitude,{c.amplitude_deg},deg,+/- about mean\n"
            f"oscillation_frequency,{c.frequency_hz},Hz,forced motion frequency\n"
            f"angular_frequency_omega,{omega:.6f},rad/s,2*pi*frequency\n"
            f"amplitude_rad,{amp_rad:.6f},rad,amplitude in radians\n"
            f"intake_speed,{c.intake_speed_ms},m/s,freestream/inlet velocity\n"
            f"dynamic_pressure_q,{q:.3f},Pa,0.5*rho*V^2\n"
            f"plate_radius,{c.plate_radius_m},m,moment/reference length\n"
            f"plate_area,{c.plate_area_m2},m^2,reference area\n"
            f"air_density,{c.rho},kg/m^3,freestream density\n"
            f"suggested_time_step,{1.0/(40.0*c.frequency_hz):.6e},s,>=40 steps per cycle\n"
            "extract,c_aero_Nms,N.m.s,moment component in phase with theta_dot (NEG=flutter)\n"
        )

        # --- setup sheet (plain, for whoever meshes it) -------------------------
        txt = f"""THROTTLE-PLATE FLUTTER — forced-oscillation CFD setup
(generated by KinematiK; run in ANSYS Fluent/CFX or a SolidWorks Flow transient study)

GOAL
  Measure the plate's aerodynamic DAMPING coefficient c_aero (N.m.s). This is the
  one number the flutter screen needs and the only part that must come from CFD.

WHAT TO DO
  1. Prescribe the plate rotating as:  theta(t) = {c.mean_angle_deg} deg + {c.amplitude_deg} deg * sin(2*pi*{c.frequency_hz} Hz * t)
     (small oscillation about the operating angle), in a {c.intake_speed_ms} m/s flow.
  2. Run it time-accurate (URANS k-omega SST is the working point; DES/LES resolves
     the wake if you have the budget). Use >= 40 time steps per oscillation cycle
     (suggested dt = {1.0/(40.0*c.frequency_hz):.3e} s) and run >= 4 cycles so the
     transient settles.
  3. Monitor the aerodynamic MOMENT about the plate shaft over time.
  4. Extract the moment component IN PHASE WITH the angular velocity theta_dot:
        c_aero = -(work per cycle) / (pi * amp_rad^2 * omega)
     with amp_rad = {amp_rad:.5f} rad, omega = {omega:.4f} rad/s.
     A NEGATIVE c_aero means the air is feeding energy into the oscillation -> FLUTTER.

GEOMETRY / REFERENCE
  plate radius   {c.plate_radius_m} m
  plate area     {c.plate_area_m2} m^2
  air density    {c.rho} kg/m^3
  dynamic press. {q:.1f} Pa  (0.5*rho*V^2)

REPORT BACK
  Put the extracted number in "{self.name}_result.json":
     {{"c_aero_Nms": <value>, "converged": true, "cell_count": <N>,
       "turbulence_model": "kOmegaSST-URANS", "is_correlated": false}}
  Then feed it back into KinematiK's flutter screen. If the run didn't converge,
  set "converged": false — the tool will flag it and not trust the number.
"""

        js = json.dumps({"case": c.as_dict(), "backend": self.name,
                         "instructions": (
                             "Forced-oscillation flutter-derivative case. Prescribe "
                             "theta(t)=mean+amp*sin(2*pi*f*t); monitor moment about the "
                             "shaft; extract the component in phase with theta_dot -> "
                             "c_aero (N.m.s). Report in the result JSON.")},
                        indent=2)

        return {
            f"{self.name}_flutter_case.jou": jou,
            f"{self.name}_flutter_params.csv": csv,
            f"{self.name}_flutter_setup.txt": txt,
            f"{self.name}_flutter_case.json": js,
        }

    def run_case(self, case: OscillationCase, workdir: str) -> FlutterDerivative:
        raise SolverUnavailable(
            f"Backend '{self.name}' does not solve inside KinematiK. It has written the "
            f"forced-oscillation case to '{workdir}'. Run it in your CFD solver on your "
            f"cluster, then call read_result() to parse the derivative. KinematiK will "
            f"not fabricate an aero-damping number.")

    def read_result(self, case: OscillationCase, workdir: str) -> FlutterDerivative:
        import json
        import os
        path = os.path.join(workdir, f"{self.name}_result.json")
        if not os.path.exists(path):
            raise SolverUnavailable(
                f"No result file at '{path}'. Run the case written by write_case() in "
                f"your CFD solver and save its extracted c_aero there first.")
        with open(path) as f:
            data = json.load(f)
        c = data.get("c_aero_Nms")
        prov = FlutterProvenance(
            backend=self.name, fidelity=self._fidelity,
            is_correlated=bool(data.get("is_correlated", False)),
            converged=bool(data.get("converged", False)),
            turbulence_model=data.get("turbulence_model", ""),
            cell_count=data.get("cell_count"),
            correlated_against=data.get("correlated_against", ""))
        findings = []
        if not prov.converged:
            findings.append(Finding(
                "flutter-cosim", Severity.WARN,
                "External flutter case parsed but marked UNCONVERGED — an unconverged "
                "aero moment is not a derivative. Do not trust the flutter call.",
                subsystems=["brakes", "powertrain"]))
        return FlutterDerivative(
            c_aero_Nms=c, ref_speed_ms=case.intake_speed_ms,
            provenance=prov, findings=findings)


# --------------------------------------------------------------------------- #
#  Convenience: run the seam and hand a ready FlutterParams back
# --------------------------------------------------------------------------- #
def extract_flutter_derivative(case: OscillationCase,
                               backend: Optional[FlutterSolver] = None,
                               workdir: str = "/tmp/kinematik_flutter") -> FlutterDerivative:
    """Run a backend through write→run and return the FlutterDerivative.

    Defaults to the runnable quasi-steady reference backend so the loop works today.
    A real backend that can't solve here raises SolverUnavailable (caught and turned
    into an honest finding), never a fabricated number.
    """
    if backend is None:
        backend = QuasiSteadyFlutterModel()
    backend.write_case(case, workdir)
    try:
        return backend.run_case(case, workdir)
    except SolverUnavailable as e:
        return FlutterDerivative(
            c_aero_Nms=None, ref_speed_ms=case.intake_speed_ms,
            provenance=backend.provenance(),
            findings=[Finding("flutter-cosim", Severity.WARN, str(e),
                              subsystems=["brakes", "powertrain"])])
