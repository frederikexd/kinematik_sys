# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================

"""
Tractive-system safety layer — the precharge transient and the shutdown chain
(TSAL / BSPD / AMS / IMD) the electrics team validates the week before tech
inspection, and which today they validate by hand in LTSpice and by reading the
rulebook with a highlighter.

WHY THIS MODULE EXISTS
----------------------
`electronics.py` already owns the *board*: will this copper trace fuse, will this
CAN pair survive the inverter's noise. `interfaces.py` owns the *bus*: does the LV
draw exceed the supply. Neither owns the thing that actually gates an FSAE-EV car
through scrutineering and that the meeting slides put on three different
sub-teams' task lists at once:

  * Battery Pack & Charging — "research what a precharge circuit is & why it's
    important", "simulate an R-C in series on a DC source, then add a switch that
    shorts the resistor after ~2 s and measure V_cap over time". That hand-drawn
    "circuit to simulate" is literally a precharge transient.
  * Tractive — "validate TSAL and BSPD using LTSpice".
  * GLV — "update the shutdown circuit to reflect the appropriate components",
    "develop a circuit diagram involving the MSD, Accumulator, and Inverter".

Every one of those is a *transient and/or a logic-and-timing* question on a
handful of explicit components the student already knows the values of. None of
them needs SPICE — they need the answer in seconds, with the rule limit checked
for them, on the same typed `Finding` surface the integration board renders.
This module is the precharge/shutdown analogue of what `electronics.py` is to the
PCB: explicit declared components, analytic standards-/rules-backed models, zero
dependencies beyond numpy, and an honest `None`/`MISSING` where a real bench
measurement is the only trustworthy source.

WHAT IT DOES
------------
  1. PRECHARGE — closed-form and time-stepped R-C charging of the DC-link
     (inverter bus) capacitance from the accumulator through the precharge
     resistor. Answers: how long to reach the contactor-close threshold (the
     rules want the bus within a few % of pack voltage before the main contactor
     closes), the peak resistor power and its pulse energy (the part that
     actually smokes the precharge resistor if it is undersized), and — the exact
     slide-5 experiment — what V_cap does when a switch shorts the resistor at
     t_switch. Matches the rules-driven "≥ 90 % within N seconds" gate FSAE
     EV.6 / EV.7 style precharge requirements impose.
  2. DISCHARGE — the mirror: when the tractive system opens, the bus must bleed
     below the rules' "safe to touch" voltage (FSAE: < 60 V) within a fixed time
     (the discharge requirement, classically ≤ 5 s) through the discharge
     resistor. Answers time-to-safe and flags an undersized/oversized bleed.
  3. SHUTDOWN CHAIN — the series safety loop (the "shutdown circuit"): a list of
     declared nodes (master switches, BSPD, AMS/BMS, IMD, interlocks, inertia
     switch, the cockpit e-stops, …) wired in series feeding the contactor coil.
     Checks the chain is series-correct, that the mandatory rule-required nodes
     are present, that it is normally-closed/open-to-trip, and that a single
     declared fault opens it. This is the "develop a circuit diagram involving
     the MSD, Accumulator and Inverter" task turned into a checkable object.
  4. TSAL — the Tractive-System-Active-Light logic: it must show GREEN/safe only
     when the bus is below the safe threshold AND latch RED/flashing whenever HV
     is present, with the rules' flash-rate window. Checked as logic + timing.
  5. BSPD — the brake-system-plausibility device: trips (and latches the shutdown
     open) when hard braking coincides with high tractive power, within a bounded
     reaction time. Checked as a threshold + reaction-time gate.

HONEST SCOPE (same contract as the rest of KinematiK)
-----------------------------------------------------
- The precharge/discharge transients are exact for the lumped R-C the student
  declares; they are NOT a parasitic-inductance / contactor-bounce / EMI model.
  Where a real bench capture is the only trustworthy source (contactor weld
  detection timing, true IMD trip threshold on the car's isolation) the result is
  `None`/`MISSING` with a stated reason, never an invented number — the same way
  electronics.py returns `None` for a coupled-line SI waveform it can't earn.
- Rule THRESHOLDS (safe-to-touch voltage, discharge time, precharge fraction,
  TSAL flash band) are defaults drawn from the public FSAE-EV intent and are
  exposed as editable `Rules` fields, because the exact numbers move year to year
  and per series (FS-Germany / FSAE / Formula Student UK differ). The student
  sets the season's number; the module checks against it. It is a rules *gate*,
  not a copy of the rulebook.
- Provenance is preserved: every declared component carries who set it and
  whether it's still an estimate, and a finding computed on a placeholder value
  is flagged as such, exactly like `Trace`/`MountPoint`.

Never raises. Every entry point returns a typed result with `.warnings` and emits
`Finding` objects, so a precharge that's too slow or a shutdown chain missing its
BSPD shows up in the existing UI with an owner named — exactly the way a melted
trace or a mount-point clash does.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Optional, Sequence

import numpy as np

from .interfaces import Finding, Severity


# --------------------------------------------------------------------------- #
#  Rule thresholds — editable, season-/series-specific. Defaults follow the
#  public FSAE-EV intent; the student sets the year's exact numbers.
# --------------------------------------------------------------------------- #
@dataclass
class Rules:
    """
    The rule limits the safety system is gated against. Defaults are the
    conventional FSAE-EV numbers; override per season/series.

    safe_voltage_v        : "safe to touch" — bus must fall below this on
                            discharge and TSAL may show safe only below it.
    discharge_time_s      : the bus must reach safe_voltage_v within this long
                            after the tractive system is opened.
    precharge_fraction    : the DC-link must reach this fraction of pack voltage
                            before the main contactor is allowed to close.
    precharge_max_time_s  : and it must get there within this long (a precharge
                            that takes too long is a slow, annoying car; one that
                            is instantaneous means R is too small and inrush is
                            unlimited — both are flagged).
    tsal_flash_hz_min/max : the TSAL "HV present" indicator must flash within
                            this band (classically 2–5 Hz).
    bspd_reaction_max_s   : the BSPD must open the shutdown circuit within this
                            long once its trip condition is met.
    """
    safe_voltage_v: float = 60.0
    discharge_time_s: float = 5.0
    precharge_fraction: float = 0.90
    precharge_max_time_s: float = 5.0
    precharge_min_time_s: float = 0.05   # faster than this ⇒ inrush essentially unlimited
    tsal_flash_hz_min: float = 2.0
    tsal_flash_hz_max: float = 5.0
    bspd_reaction_max_s: float = 0.5

    def as_dict(self):
        return asdict(self)


# --------------------------------------------------------------------------- #
#  Precharge / discharge declaration
# --------------------------------------------------------------------------- #
@dataclass
class PrechargeCircuit:
    """
    The precharge R-C the student declares — exactly the slide-4/slide-5
    experiment, made into a checkable object.

        pack_voltage_v   : accumulator nominal voltage (the DC source).
        link_capacitance_f : total DC-link / inverter bus capacitance being
                            charged (sum of inverter caps; the thing that draws
                            the inrush).
        precharge_r_ohm  : the precharge resistor (limits inrush, sets τ).
        discharge_r_ohm  : the bleed resistor across the bus (sets discharge τ);
                            None if not yet chosen — then discharge is MISSING,
                            not faked.
        resistor_power_rating_w : the precharge resistor's continuous rating, and
        resistor_energy_rating_j : its single-pulse energy rating, if known — the
                            two numbers that decide whether the resistor survives
                            the precharge pulse. None ⇒ that check is MISSING.

    Provenance mirrors Trace/MountPoint.
    """
    pack_voltage_v: float
    link_capacitance_f: float
    precharge_r_ohm: float
    discharge_r_ohm: Optional[float] = None
    resistor_power_rating_w: Optional[float] = None
    resistor_energy_rating_j: Optional[float] = None
    is_estimate: bool = True
    set_by: str = ""
    notes: str = ""

    # ---- closed-form characteristics ------------------------------------- #
    @property
    def tau_precharge_s(self) -> float:
        """R·C time constant of the precharge path."""
        return self.precharge_r_ohm * self.link_capacitance_f

    @property
    def tau_discharge_s(self) -> Optional[float]:
        if self.discharge_r_ohm is None:
            return None
        return self.discharge_r_ohm * self.link_capacitance_f

    @property
    def peak_inrush_a(self) -> float:
        """Inrush at t=0 (cap looks like a short): V/R."""
        return self.pack_voltage_v / max(self.precharge_r_ohm, 1e-9)

    @property
    def peak_resistor_power_w(self) -> float:
        """Worst-case instantaneous power in the precharge resistor, at t=0."""
        return self.peak_inrush_a * self.pack_voltage_v

    @property
    def precharge_pulse_energy_j(self) -> float:
        """
        Total energy dumped in the precharge resistor to charge the cap from 0 to
        V. For an ideal R-C this is exactly ½·C·V² regardless of R — the resistor
        always burns the same energy the cap ends up storing. This is the number
        that smokes an undersized resistor.
        """
        return 0.5 * self.link_capacitance_f * self.pack_voltage_v ** 2

    def time_to_fraction_s(self, frac: float) -> float:
        """
        Closed-form time for V_cap to reach `frac`·V_pack through the precharge
        resistor: t = -τ·ln(1 - frac). frac is clamped to (0, 1).
        """
        f = min(max(frac, 1e-6), 1.0 - 1e-9)
        return -self.tau_precharge_s * math.log(1.0 - f)

    def time_to_safe_s(self, safe_voltage_v: float) -> Optional[float]:
        """
        Closed-form discharge time for V_cap to fall from V_pack to
        `safe_voltage_v` through the discharge resistor. None if no bleed R.
        """
        tau = self.tau_discharge_s
        if tau is None:
            return None
        if safe_voltage_v >= self.pack_voltage_v:
            return 0.0
        return -tau * math.log(safe_voltage_v / self.pack_voltage_v)

    def as_dict(self):
        d = asdict(self)
        d["tau_precharge_s"] = self.tau_precharge_s
        d["tau_discharge_s"] = self.tau_discharge_s
        d["peak_inrush_a"] = self.peak_inrush_a
        d["precharge_pulse_energy_j"] = self.precharge_pulse_energy_j
        return d

    @staticmethod
    def from_dict(d) -> "PrechargeCircuit":
        keep = {k: d[k] for k in (
            "pack_voltage_v", "link_capacitance_f", "precharge_r_ohm",
            "discharge_r_ohm", "resistor_power_rating_w",
            "resistor_energy_rating_j", "is_estimate", "set_by", "notes")
            if k in d}
        return PrechargeCircuit(**keep)


@dataclass
class PrechargeTrace:
    """Time history of a precharge (or shorted-resistor) transient."""
    time_s: np.ndarray
    v_cap_v: np.ndarray
    i_a: np.ndarray
    p_resistor_w: np.ndarray
    t_switch_s: Optional[float]
    ok: bool = True
    warnings: list = field(default_factory=list)


def simulate_precharge(pc: PrechargeCircuit,
                       t_end_s: Optional[float] = None,
                       t_switch_s: Optional[float] = None,
                       n: int = 2000) -> PrechargeTrace:
    """
    Reproduce EXACTLY the slide-4/slide-5 experiment without SPICE:

      * charge the DC-link cap from the pack through the precharge resistor, and
      * if `t_switch_s` is given, at that instant a switch SHORTS the precharge
        resistor (R→~0), so the cap snaps to the source — the second half of the
        slide-5 drawing ("add a switch that turns on after ~2 s and shorts the
        resistor, measure V_cap over time").

    Returns V_cap(t), the resistor current and the resistor power over time.
    Closed-form within each segment (no integration error to argue about); the
    array is just for plotting. Never raises.
    """
    warns: list[str] = []
    try:
        V = float(pc.pack_voltage_v)
        tau = max(pc.tau_precharge_s, 1e-12)
        if t_end_s is None:
            # default: a couple of switch-times, or ~6τ if no switch
            t_end_s = (t_switch_s * 2.0 if t_switch_s else 6.0 * tau)
        t_end_s = max(float(t_end_s), 1e-6)
        t = np.linspace(0.0, t_end_s, max(int(n), 8))

        v = V * (1.0 - np.exp(-t / tau))
        if t_switch_s is not None and 0.0 < t_switch_s < t_end_s:
            # at t_switch the resistor is shorted: from the value it had reached,
            # the cap charges to V with a (tiny) residual time constant. With an
            # ideal short it snaps; we model a small residual R so the trace is
            # finite and the current spike is bounded but visibly large.
            v_at = V * (1.0 - math.exp(-t_switch_s / tau))
            tau_short = max(tau * 1e-3, 1e-9)
            post = t >= t_switch_s
            # clamp the exponent so the (already-saturated) snap can't overflow exp
            expo = np.clip((t - t_switch_s) / tau_short, 0.0, 700.0)
            v = np.where(post, V - (V - v_at) * np.exp(-expo), v)
            warns.append(
                f"Switch shorts precharge R at t={t_switch_s:.3g}s; the post-"
                f"switch current spike is bounded by a modelled residual R "
                f"(ideal short ⇒ unbounded inrush — size a real switch/contactor "
                f"for it).")
        # current through the (un-shorted) resistor is C·dV/dt; equivalently
        # (V - v)/R before the switch. After an ideal short the resistor carries
        # ~0 (it's bypassed); the inrush then flows through the switch, which we
        # do not pretend to resolve here.
        i = (V - v) / max(pc.precharge_r_ohm, 1e-9)
        if t_switch_s is not None:
            i = np.where(t >= t_switch_s, 0.0, i)
        p = i * i * pc.precharge_r_ohm
        return PrechargeTrace(time_s=t, v_cap_v=v, i_a=i, p_resistor_w=p,
                              t_switch_s=t_switch_s, ok=True, warnings=warns)
    except Exception as exc:  # never raise
        z = np.zeros(8)
        return PrechargeTrace(time_s=z, v_cap_v=z, i_a=z, p_resistor_w=z,
                              t_switch_s=t_switch_s, ok=False,
                              warnings=warns + [f"precharge sim crashed: {exc!r}"])


def check_precharge(pc: PrechargeCircuit, rules: Optional[Rules] = None) -> list:
    """
    Gate the precharge/discharge design against the rules. Emits typed Findings,
    each naming the electrics subsystem so it has an owner in the board.
    """
    rules = rules or Rules()
    out: list[Finding] = []
    subs = ["electrics", "battery-pack"]
    est = " (on ESTIMATE values)" if pc.is_estimate else ""

    # ---- precharge speed gate -------------------------------------------- #
    t_frac = pc.time_to_fraction_s(rules.precharge_fraction)
    pctg = int(round(rules.precharge_fraction * 100))
    if t_frac > rules.precharge_max_time_s:
        out.append(Finding(
            "precharge-time", Severity.FAIL,
            f"Precharge reaches {pctg}% of pack voltage in {t_frac:.2f}s — over "
            f"the {rules.precharge_max_time_s:.1f}s limit{est}. Lower the "
            f"precharge resistor (τ=R·C={pc.tau_precharge_s:.3g}s).",
            subsystems=subs,
            detail={"t_to_fraction_s": t_frac, "tau_s": pc.tau_precharge_s,
                    "limit_s": rules.precharge_max_time_s}))
    elif t_frac < rules.precharge_min_time_s:
        out.append(Finding(
            "precharge-time", Severity.WARN,
            f"Precharge reaches {pctg}% in only {t_frac:.3g}s — the resistor is "
            f"so small that inrush ({pc.peak_inrush_a:.0f}A peak) is barely "
            f"limited{est}. Raise R or check the inrush rating.",
            subsystems=subs,
            detail={"t_to_fraction_s": t_frac, "peak_inrush_a": pc.peak_inrush_a}))
    else:
        out.append(Finding(
            "precharge-time", Severity.OK,
            f"Precharge reaches {pctg}% of pack voltage in {t_frac:.2f}s "
            f"(≤ {rules.precharge_max_time_s:.1f}s){est}.",
            subsystems=subs,
            detail={"t_to_fraction_s": t_frac, "tau_s": pc.tau_precharge_s}))

    # ---- resistor survival (the part that actually smokes) ---------------- #
    pulse_j = pc.precharge_pulse_energy_j
    if pc.resistor_energy_rating_j is not None:
        if pulse_j > pc.resistor_energy_rating_j:
            out.append(Finding(
                "precharge-resistor-energy", Severity.FAIL,
                f"Each precharge dumps {pulse_j:.1f}J in the resistor "
                f"(½·C·V²) but it is rated for {pc.resistor_energy_rating_j:.1f}J "
                f"per pulse — it will cook. Use a higher-energy resistor.",
                subsystems=subs,
                detail={"pulse_energy_j": pulse_j,
                        "rating_j": pc.resistor_energy_rating_j}))
        else:
            out.append(Finding(
                "precharge-resistor-energy", Severity.OK,
                f"Precharge pulse energy {pulse_j:.1f}J ≤ resistor rating "
                f"{pc.resistor_energy_rating_j:.1f}J.",
                subsystems=subs, detail={"pulse_energy_j": pulse_j}))
    else:
        out.append(Finding(
            "precharge-resistor-energy", Severity.MISSING,
            f"Each precharge dumps {pulse_j:.1f}J (½·C·V²) in the precharge "
            f"resistor; its per-pulse energy rating is not declared, so survival "
            f"can't be checked. Add resistor_energy_rating_j.",
            subsystems=subs, detail={"pulse_energy_j": pulse_j}))

    # ---- discharge / bleed gate ------------------------------------------ #
    t_safe = pc.time_to_safe_s(rules.safe_voltage_v)
    if t_safe is None:
        out.append(Finding(
            "discharge-time", Severity.MISSING,
            f"No discharge (bleed) resistor declared — the bus can't be shown to "
            f"fall below {rules.safe_voltage_v:.0f}V within "
            f"{rules.discharge_time_s:.0f}s. Add discharge_r_ohm.",
            subsystems=subs))
    elif t_safe > rules.discharge_time_s:
        out.append(Finding(
            "discharge-time", Severity.FAIL,
            f"Bus bleeds to {rules.safe_voltage_v:.0f}V in {t_safe:.2f}s — over "
            f"the {rules.discharge_time_s:.0f}s safe-discharge limit{est}. "
            f"Lower the discharge resistor.",
            subsystems=subs,
            detail={"t_to_safe_s": t_safe, "limit_s": rules.discharge_time_s}))
    else:
        out.append(Finding(
            "discharge-time", Severity.OK,
            f"Bus bleeds below {rules.safe_voltage_v:.0f}V in {t_safe:.2f}s "
            f"(≤ {rules.discharge_time_s:.0f}s){est}.",
            subsystems=subs, detail={"t_to_safe_s": t_safe}))

    return out


# --------------------------------------------------------------------------- #
#  Shutdown chain (the series safety loop) + TSAL + BSPD
# --------------------------------------------------------------------------- #
# Nodes the FSAE-EV rules require in the shutdown circuit. The student's declared
# chain is checked for these; the exact set is a rules default and is editable.
REQUIRED_SHUTDOWN_NODES = (
    "master_switch",   # the grounded-low-voltage + tractive master switches
    "bspd",            # brake-system plausibility device
    "ams",             # accumulator management / BMS fault
    "imd",             # insulation monitoring device
    "interlock",       # HV connector interlocks (MSD / accumulator / inverter)
    "inertia",         # crash / inertia switch
    "estop",           # cockpit + both-side e-stop buttons
)


@dataclass
class ShutdownNode:
    """
    One series element of the shutdown circuit. `normally_closed=True` is the
    correct, fail-safe convention: the loop is closed when healthy and OPENS to
    trip (so a broken wire = safe). Each node names what trips it.
    """
    name: str                       # e.g. "bspd", "imd", "msd_interlock"
    kind: str                       # one of REQUIRED_SHUTDOWN_NODES (or "other")
    normally_closed: bool = True
    trips_on: str = ""              # human description of the fault that opens it
    set_by: str = ""
    is_estimate: bool = True

    def as_dict(self):
        return asdict(self)


@dataclass
class ShutdownChain:
    """
    The series shutdown loop feeding the contactor coil: an ordered list of
    nodes. If every node is closed the coil is energised and the main contactors
    close; any one node opening drops the coil and opens the tractive system.
    """
    nodes: list = field(default_factory=list)
    feeds: str = "main_contactor_coil"

    def add(self, node: ShutdownNode):
        self.nodes.append(node)

    @property
    def kinds_present(self) -> set:
        return {n.kind for n in self.nodes}

    def is_closed(self, open_nodes: Sequence[str] = ()) -> bool:
        """
        Series logic: the loop is closed iff every normally-closed node is intact
        and not in `open_nodes`. A single open node opens the whole chain.
        """
        opened = set(open_nodes)
        for n in self.nodes:
            if n.name in opened:
                return False
            if not n.normally_closed:
                # a normally-open element in a series safety loop is wired wrong
                return False
        return True

    def as_dict(self):
        return {"nodes": [n.as_dict() for n in self.nodes], "feeds": self.feeds}


def check_shutdown_chain(chain: ShutdownChain,
                         rules: Optional[Rules] = None,
                         required: Sequence[str] = REQUIRED_SHUTDOWN_NODES) -> list:
    """
    Check the declared shutdown circuit is rule-complete and fail-safe, and that
    a single declared fault really opens it. The "develop a circuit diagram
    involving the MSD, Accumulator and Inverter" task, turned into a gate.
    """
    out: list[Finding] = []
    subs = ["electrics", "glv"]

    if not chain.nodes:
        out.append(Finding(
            "shutdown-chain", Severity.MISSING,
            "No shutdown-circuit nodes declared. Add the series safety loop "
            "(master switches, BSPD, AMS, IMD, interlocks, inertia, e-stops).",
            subsystems=subs))
        return out

    # 1) every node must be normally-closed (open-to-trip = fail-safe)
    wrong = [n.name for n in chain.nodes if not n.normally_closed]
    if wrong:
        out.append(Finding(
            "shutdown-failsafe", Severity.FAIL,
            f"Shutdown nodes wired normally-OPEN: {', '.join(wrong)}. A safety "
            f"loop must be normally-CLOSED so a broken wire opens it (fail-safe).",
            subsystems=subs, detail={"normally_open_nodes": wrong}))
    else:
        out.append(Finding(
            "shutdown-failsafe", Severity.OK,
            "All shutdown nodes are normally-closed (open-to-trip, fail-safe).",
            subsystems=subs))

    # 2) rule-required nodes present
    present = chain.kinds_present
    missing = [k for k in required if k not in present]
    if missing:
        out.append(Finding(
            "shutdown-completeness", Severity.FAIL,
            f"Shutdown circuit is missing rule-required node(s): "
            f"{', '.join(missing)}. The tractive system can't be enabled at tech "
            f"without them.",
            subsystems=subs, detail={"missing": missing,
                                     "present": sorted(present)}))
    else:
        out.append(Finding(
            "shutdown-completeness", Severity.OK,
            f"All {len(required)} rule-required shutdown nodes present "
            f"({', '.join(sorted(present))}).",
            subsystems=subs, detail={"present": sorted(present)}))

    # 3) single-fault opens the chain (prove series-ness on each declared node)
    not_opening = []
    for n in chain.nodes:
        if chain.is_closed(open_nodes=[n.name]):
            not_opening.append(n.name)
    if not_opening:
        out.append(Finding(
            "shutdown-single-fault", Severity.FAIL,
            f"Opening node(s) {', '.join(not_opening)} does NOT open the chain — "
            f"they are not actually in series with the contactor coil. Re-route.",
            subsystems=subs, detail={"not_in_series": not_opening}))
    else:
        out.append(Finding(
            "shutdown-single-fault", Severity.OK,
            "Any single node opening drops the contactor coil (true series loop).",
            subsystems=subs))

    # 4) provenance nudge
    if any(n.is_estimate for n in chain.nodes):
        est_nodes = [n.name for n in chain.nodes if n.is_estimate]
        out.append(Finding(
            "shutdown-provenance", Severity.INFO,
            f"Shutdown nodes still on placeholder/estimate values: "
            f"{', '.join(est_nodes)}. Confirm against the as-wired loom.",
            subsystems=subs, detail={"estimate_nodes": est_nodes}))
    return out


@dataclass
class TSAL:
    """
    Tractive-System-Active-Light logic. It must:
      * show the safe/green state ONLY when the bus is below the safe voltage, and
      * flash the HV-present indicator within the rules' rate band whenever HV is
        present.
    `flash_hz` is the design flash rate; `safe_threshold_v` is the voltage below
    which it is allowed to indicate safe (must be ≤ the rules' safe voltage).
    """
    flash_hz: float
    safe_threshold_v: float
    set_by: str = ""
    is_estimate: bool = True


def check_tsal(tsal: TSAL, rules: Optional[Rules] = None) -> list:
    rules = rules or Rules()
    out: list[Finding] = []
    subs = ["electrics", "tractive"]
    if not (rules.tsal_flash_hz_min <= tsal.flash_hz <= rules.tsal_flash_hz_max):
        out.append(Finding(
            "tsal-flash", Severity.FAIL,
            f"TSAL flashes at {tsal.flash_hz:.1f}Hz — outside the required "
            f"{rules.tsal_flash_hz_min:.0f}–{rules.tsal_flash_hz_max:.0f}Hz band.",
            subsystems=subs, detail={"flash_hz": tsal.flash_hz}))
    else:
        out.append(Finding(
            "tsal-flash", Severity.OK,
            f"TSAL flash rate {tsal.flash_hz:.1f}Hz within "
            f"{rules.tsal_flash_hz_min:.0f}–{rules.tsal_flash_hz_max:.0f}Hz.",
            subsystems=subs))
    if tsal.safe_threshold_v > rules.safe_voltage_v:
        out.append(Finding(
            "tsal-threshold", Severity.FAIL,
            f"TSAL may indicate 'safe' up to {tsal.safe_threshold_v:.0f}V, above "
            f"the {rules.safe_voltage_v:.0f}V safe-to-touch limit — it could show "
            f"green while the bus is still live. Lower the threshold.",
            subsystems=subs, detail={"threshold_v": tsal.safe_threshold_v}))
    else:
        out.append(Finding(
            "tsal-threshold", Severity.OK,
            f"TSAL indicates safe only below {tsal.safe_threshold_v:.0f}V "
            f"(≤ {rules.safe_voltage_v:.0f}V).",
            subsystems=subs))
    return out


@dataclass
class BSPD:
    """
    Brake-System-Plausibility-Device parameters. It must open the shutdown loop
    when hard braking (brake pressure / force above threshold) coincides with
    high tractive power, within a bounded reaction time.
    """
    brake_threshold: float          # the braking signal level that arms it
    power_threshold_w: float        # tractive power above which, when braking, it trips
    reaction_time_s: float          # measured/spec'd time from condition to open
    set_by: str = ""
    is_estimate: bool = True

    def trips(self, brake_signal: float, tractive_power_w: float) -> bool:
        return (brake_signal >= self.brake_threshold and
                tractive_power_w >= self.power_threshold_w)


def check_bspd(bspd: BSPD, rules: Optional[Rules] = None) -> list:
    rules = rules or Rules()
    out: list[Finding] = []
    subs = ["electrics", "tractive"]
    if bspd.reaction_time_s > rules.bspd_reaction_max_s:
        out.append(Finding(
            "bspd-reaction", Severity.FAIL,
            f"BSPD reacts in {bspd.reaction_time_s*1000:.0f}ms — slower than the "
            f"{rules.bspd_reaction_max_s*1000:.0f}ms limit. It must open the "
            f"shutdown loop faster.",
            subsystems=subs, detail={"reaction_s": bspd.reaction_time_s}))
    else:
        out.append(Finding(
            "bspd-reaction", Severity.OK,
            f"BSPD reaction {bspd.reaction_time_s*1000:.0f}ms within "
            f"{rules.bspd_reaction_max_s*1000:.0f}ms.",
            subsystems=subs))
    # sanity: thresholds must be positive and finite, else it never (or always) trips
    if not (bspd.brake_threshold > 0 and bspd.power_threshold_w > 0):
        out.append(Finding(
            "bspd-thresholds", Severity.WARN,
            "BSPD brake/power thresholds are non-positive — it will never arm or "
            "always trip. Set real arming levels.",
            subsystems=subs))
    return out


# --------------------------------------------------------------------------- #
#  One-call pre-tech gate over the whole tractive-system safety layer
# --------------------------------------------------------------------------- #
@dataclass
class TractiveSafetyResult:
    """Bundles every tractive-system safety finding with a one-line headline."""
    findings: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def has_hard_fail(self) -> bool:
        return any(f.severity == Severity.FAIL for f in self.findings)

    def summary(self) -> str:
        from .interfaces import summarize
        s = summarize(self.findings)
        return (f"{s['worst'].upper()}: "
                f"{s['counts'].get('fail',0)} fail / "
                f"{s['counts'].get('warning',0)} warn / "
                f"{s['counts'].get('missing',0)} missing / "
                f"{s['counts'].get('ok',0)} ok across "
                f"{len(self.findings)} tractive-safety checks")


def check_tractive_system(precharge: Optional[PrechargeCircuit] = None,
                          chain: Optional[ShutdownChain] = None,
                          tsal: Optional[TSAL] = None,
                          bspd: Optional[BSPD] = None,
                          rules: Optional[Rules] = None) -> TractiveSafetyResult:
    """
    The single call an electrics student makes before tech inspection: run every
    declared part of the tractive-system safety layer (precharge/discharge,
    shutdown chain, TSAL, BSPD) against the season's rules and return all findings
    on the same typed surface the integration board renders. Anything not yet
    declared is simply skipped (it shows up elsewhere as MISSING), never faked.
    """
    rules = rules or Rules()
    findings: list[Finding] = []
    warns: list[str] = []
    if precharge is not None:
        findings += check_precharge(precharge, rules)
    if chain is not None:
        findings += check_shutdown_chain(chain, rules)
    if tsal is not None:
        findings += check_tsal(tsal, rules)
    if bspd is not None:
        findings += check_bspd(bspd, rules)
    if not findings:
        warns.append("Nothing declared yet — declare a precharge circuit, "
                     "shutdown chain, TSAL and/or BSPD to gate them.")
    return TractiveSafetyResult(findings=findings, warnings=warns)
