# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
#
#  Module: risk_engine — Integrated DFMEA Risk Engine
#  Rule-based risk propagation MATRIX over live chassis / brakes / powertrain
#  parameters. Where risk_propagation.py answers "someone edited an interface,
#  what moved?", this module answers "the car's CURRENT numbers are what they
#  are — which failure modes are hot RIGHT NOW, at what severity, and what is
#  the live RPN?". Severity is ELEVATED programmatically when a factor of
#  safety drops below its threshold or a cooling-manifold segment develops a
#  dangerous localized pressure drop; occurrence is bumped along declared
#  cross-subsystem propagation edges. RPN math is delegated to dfmea.py so a
#  live row and a hand-curated row can never disagree on arithmetic.
#
#  Also home to the SLOTTED-HOLE fastener torque & preload calculator (brake
#  pedal tabs): non-standard joint geometry reduces the bearing contact area
#  under the head/washer, which shifts the effective under-head friction
#  radius and therefore the torque coefficient K — so the same wrench setting
#  no longer delivers the catalogue clamp force. This calculator adjusts K and
#  the clamping force from the actual contact geometry (Motosh long-form
#  torque equation + numeric contact-patch integration) and caps preload at
#  the reduced bearing area's crush limit.
#
#  Stdlib + dfmea.py + bolted_joint.py only. No Streamlit/pandas at load.
# ============================================================================

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Callable, Optional

from .dfmea import compute_rpn, classify_risk, RiskBand, COLUMNS
from .bolted_joint import BOLT_GRADES, METRIC_COARSE, Fastener


# --------------------------------------------------------------------------- #
#  Live parameter readings — the engine's only input format
# --------------------------------------------------------------------------- #
SUBSYSTEMS = ("chassis", "brakes", "powertrain", "cooling")


@dataclass(frozen=True)
class Reading:
    """One monitored parameter as it stands NOW (from a solver or the ledger)."""
    subsystem: str
    channel: str            # e.g. "bracket_fos", "manifold_dp_kpa:seg3"
    value: float
    unit: str = ""
    source: str = "ledger"  # provenance label carried into the risk row


def readings(*triples) -> list[Reading]:
    """Terse constructor: readings(("brakes","caliper_bracket_fos",1.3), ...)."""
    return [Reading(t[0], t[1], float(t[2]), *(t[3:])) for t in triples]


# --------------------------------------------------------------------------- #
#  Severity elevation — the deterministic escalation law
# --------------------------------------------------------------------------- #
def elevate_severity(base: int, deficit_ratio: float, *, gain: float = 6.0) -> int:
    """
    Elevate a base DFMEA severity by how far past the limit the parameter is.

    deficit_ratio = (limit - value)/limit for minimum limits (FoS floors), or
                    (value - limit)/limit for maximum limits (ΔP ceilings);
    <= 0 means compliant (no elevation).  severity = base + ceil(gain·deficit),
    clamped to 1..10 — a 10% FoS shortfall lifts severity by one step at the
    default gain; a 50%+ shortfall pins it near hazardous.
    """
    if deficit_ratio <= 0.0:
        return max(1, min(10, int(base)))
    return max(1, min(10, int(base) + math.ceil(gain * deficit_ratio)))


def occurrence_from_deficit(deficit_ratio: float, *, floor: int = 3) -> int:
    # Occurrence rises with margin erosion: at the limit → floor; 30% past → ~8.
    if deficit_ratio <= 0.0:
        return max(1, floor - 1)
    return max(1, min(10, floor + math.ceil(15.0 * deficit_ratio)))


# --------------------------------------------------------------------------- #
#  Rules — one row of the risk matrix
# --------------------------------------------------------------------------- #
class LimitKind(str, Enum):
    MIN = "min"   # value must stay ABOVE limit (factors of safety)
    MAX = "max"   # value must stay BELOW limit (pressure drops, temps)


@dataclass(frozen=True)
class RiskRule:
    rule_id: str
    subsystem: str
    channel: str                 # exact channel, or prefix ending ':' (per-segment)
    kind: LimitKind
    limit: float
    failure_mode: str
    effect: str
    base_severity: int
    detection: int               # how detectable BEFORE failure (dfmea scale)
    item: str = ""
    cause: str = ""
    severity_gain: float = 6.0

    def matches(self, r: Reading) -> bool:
        if r.subsystem != self.subsystem:
            return False
        return (r.channel == self.channel or
                (self.channel.endswith(":") and r.channel.startswith(self.channel)))

    def deficit(self, value: float) -> float:
        if self.limit == 0:
            return 0.0
        d = (self.limit - value) / abs(self.limit) if self.kind is LimitKind.MIN \
            else (value - self.limit) / abs(self.limit)
        return float(d)


# --------------------------------------------------------------------------- #
#  Propagation matrix — (subsystem, channel-prefix) → downstream consequences.
#  Architecture rule: propagation NEVER raises downstream severity (severity
#  belongs to the downstream failure's own consequences); it raises downstream
#  OCCURRENCE, because an upstream breach makes the downstream mode more likely.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PropagationEdge:
    src_subsystem: str
    src_channel: str             # prefix match (":"-terminated) or exact
    dst_rule_id: str
    occurrence_bump: int
    mechanism: str


PROPAGATION: tuple[PropagationEdge, ...] = (
    PropagationEdge("chassis", "bracket_fos", "BRK-MOUNT-SHEAR", 2,
                    "under-margin chassis bracket softens the brake mount load path"),
    PropagationEdge("chassis", "node_fos", "PT-MOUNT-FATIGUE", 2,
                    "chassis node margin loss feeds alternating load into motor mounts"),
    PropagationEdge("cooling", "manifold_dp_kpa:", "PT-MOTOR-OVERTEMP", 3,
                    "starved branch behind the localized ΔP under-cools the motor loop"),
    PropagationEdge("cooling", "manifold_dp_kpa:", "PT-INV-DERATE", 2,
                    "flow imbalance from the localized ΔP derates the inverter branch"),
    PropagationEdge("powertrain", "shaft_fos", "BRK-BIAS-SHIFT", 1,
                    "driveline margin loss changes decel torque split assumptions"),
    PropagationEdge("brakes", "pedal_tab_joint_fos", "CHS-PEDALBOX-PEEL", 2,
                    "slipping slotted pedal-tab joint peels load into the pedal-box floor"),
)


# --------------------------------------------------------------------------- #
#  Default rule matrix — FoS floors + manifold ΔP ceilings, chassis/brakes/PT
# --------------------------------------------------------------------------- #
def default_rules(*, chassis_fos_min: float = 1.5, brake_fos_min: float = 1.6,
                  shaft_fos_min: float = 1.4, pedal_joint_fos_min: float = 1.2,
                  manifold_dp_max_kpa: float = 35.0,
                  vapor_margin_min_kpa: float = 20.0) -> list[RiskRule]:
    R, MIN, MAX = RiskRule, LimitKind.MIN, LimitKind.MAX
    return [
        R("CHS-NODE-BUCKLE", "chassis", "node_fos", MIN, chassis_fos_min,
          "primary-structure node buckles under combined load",
          "load path lost; frame deformation; likely DNF + driver hazard",
          8, 4, item="frame node", cause="FoS below floor at audited node"),
        R("CHS-BRACKET-YIELD", "chassis", "bracket_fos", MIN, chassis_fos_min,
          "bolted bracket yields at mount", "mount migrates; alignment lost mid-run",
          7, 3, item="chassis bracket", cause="bracket FoS below floor"),
        R("CHS-PEDALBOX-PEEL", "chassis", "pedalbox_base_fos", MIN, chassis_fos_min,
          "pedal-box base peels off floor under panic-stop load",
          "pedal feel lost; brake application compromised", 9, 4,
          item="pedal-box base", cause="prying-amplified pull-off exceeds margin"),
        R("BRK-MOUNT-SHEAR", "brakes", "caliper_bracket_fos", MIN, brake_fos_min,
          "caliper bracket shears", "corner loses braking; car pulls under braking",
          9, 3, item="caliper bracket", cause="bracket FoS below brake floor"),
        R("BRK-ROTOR-MARGIN", "brakes", "rotor_thermal_fos", MIN, brake_fos_min,
          "rotor exceeds thermal limit late in endurance", "fade / cracking",
          7, 4, item="brake rotor", cause="thermal margin below floor"),
        R("BRK-BIAS-SHIFT", "brakes", "bias_margin_fos", MIN, 1.1,
          "lock-up order inverts (rears lock first)", "spin under threshold braking",
          8, 5, item="brake bias", cause="bias margin below floor"),
        R("BRK-PEDALTAB-SLIP", "brakes", "pedal_tab_joint_fos", MIN, pedal_joint_fos_min,
          "slotted pedal-tab joint slips / loses preload",
          "pedal geometry shifts; ratio changes under the driver's foot", 9, 6,
          item="brake pedal tab (slotted holes)",
          cause="clamp force below slip demand at reduced bearing area"),
        R("PT-MOUNT-FATIGUE", "powertrain", "motor_mount_fos", MIN, chassis_fos_min,
          "motor mount fatigues at weld", "driveline misalignment; chain/belt loss",
          7, 4, item="motor mount", cause="mount FoS below floor"),
        R("PT-SHAFT-YIELD", "powertrain", "shaft_fos", MIN, shaft_fos_min,
          "output shaft yields at peak torque", "immediate DNF; debris hazard",
          8, 3, item="output shaft", cause="shaft FoS below floor"),
        R("PT-MOTOR-OVERTEMP", "powertrain", "motor_temp_margin_fos", MIN, 1.0,
          "motor exceeds winding temp limit", "derate or shutdown in endurance",
          6, 3, item="motor", cause="thermal margin consumed"),
        R("PT-INV-DERATE", "powertrain", "inverter_temp_margin_fos", MIN, 1.0,
          "inverter derates on junction temp", "power loss mid-lap",
          6, 3, item="inverter", cause="thermal margin consumed"),
        # --- cooling manifold: localized ΔP ceilings, per segment ------------ #
        R("COOL-MANIFOLD-DP", "cooling", "manifold_dp_kpa:", MAX, manifold_dp_max_kpa,
          "dangerous localized pressure drop across a manifold segment",
          "branch starvation downstream; hot spot on the starved consumer", 8, 5,
          item="cooling manifold segment",
          cause="local ΔP exceeds ceiling (blockage, kink, undersized branch)",
          severity_gain=8.0),
        R("COOL-CAVITATION", "cooling", "pump_inlet_margin_kpa", MIN, vapor_margin_min_kpa,
          "pump inlet pressure approaches vapor margin — cavitation",
          "flow collapse; pump damage; whole-loop overheating", 9, 6,
          item="coolant pump inlet", cause="suction-side ΔP eats the vapor margin",
          severity_gain=8.0),
    ]


# --------------------------------------------------------------------------- #
#  Live risk rows + the engine
# --------------------------------------------------------------------------- #
@dataclass
class LiveRisk:
    rule_id: str
    subsystem: str
    channel: str
    item: str
    failure_mode: str
    effect: str
    value: float
    limit: float
    kind: str
    deficit_ratio: float
    triggered: bool
    severity: int
    occurrence: int
    detection: int
    rpn: int
    band: str
    propagated_from: list[str] = field(default_factory=list)
    mechanisms: list[str] = field(default_factory=list)
    source: str = "ledger"

    def as_dict(self):
        return asdict(self)

    def to_dfmea_record(self) -> dict[str, Any]:
        """Shape a live row exactly like the DFMEA workbench (dfmea.COLUMNS)."""
        rec = {c: "" for c in COLUMNS}
        rec.update({
            "Subsystem": self.subsystem.title(),
            "Item / Component": self.item,
            "Function / Requirement": f"{self.channel} {self.kind} limit {self.limit:g}",
            "Failure Mode": self.failure_mode,
            "Effect of Failure": self.effect,
            "Severity": self.severity, "Occurrence": self.occurrence,
            "Detection": self.detection, "RPN": self.rpn,
            "Potential Cause / Mechanism": "; ".join(self.mechanisms) or self.channel,
            "Status": "Open" if self.triggered else "Closed",
            "Evidence / Notes": (f"live value {self.value:g} vs limit {self.limit:g} "
                                 f"({self.source}); deficit {self.deficit_ratio:+.1%}"),
        })
        return rec


@dataclass
class RiskReport:
    risks: list[LiveRisk]
    unmatched: list[Reading]

    def active(self) -> list[LiveRisk]:
        return sorted((r for r in self.risks if r.triggered),
                      key=lambda r: (-r.rpn, -r.severity, r.rule_id))

    def worst(self) -> Optional[LiveRisk]:
        a = self.active()
        return a[0] if a else None

    def as_records(self, triggered_only: bool = True) -> list[dict]:
        rows = self.active() if triggered_only else self.risks
        return [r.to_dfmea_record() for r in rows]

    def summary(self) -> dict:
        a = self.active()
        return {"monitored": len(self.risks), "triggered": len(a),
                "critical": sum(r.band == RiskBand.CRITICAL.value for r in a),
                "max_rpn": max((r.rpn for r in a), default=0),
                "unmatched_readings": [f"{u.subsystem}.{u.channel}" for u in self.unmatched]}


class RiskEngine:
    """The matrix. Feed it Readings; get live, propagated, RPN-scored risk."""

    def __init__(self, rules: Optional[list[RiskRule]] = None,
                 propagation: tuple[PropagationEdge, ...] = PROPAGATION):
        self.rules = list(rules) if rules is not None else default_rules()
        self.propagation = tuple(propagation)
        ids = [r.rule_id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate rule_id in risk matrix")

    def evaluate(self, rds: list[Reading]) -> RiskReport:
        rows: dict[str, LiveRisk] = {}
        unmatched: list[Reading] = []
        for rd in rds:
            hit = False
            for rule in self.rules:
                if not rule.matches(rd):
                    continue
                hit = True
                d = rule.deficit(rd.value)
                sev = elevate_severity(rule.base_severity, d, gain=rule.severity_gain)
                occ = occurrence_from_deficit(d)
                key = f"{rule.rule_id}|{rd.channel}"
                # Per-segment channels (prefix rules) keep one row per segment;
                # if the same channel reports twice, keep the WORST deficit.
                prev = rows.get(key)
                if prev is not None and prev.deficit_ratio >= d:
                    continue
                rows[key] = LiveRisk(
                    rule_id=rule.rule_id, subsystem=rule.subsystem, channel=rd.channel,
                    item=rule.item or rd.channel, failure_mode=rule.failure_mode,
                    effect=rule.effect, value=rd.value, limit=rule.limit,
                    kind=rule.kind.value, deficit_ratio=round(d, 6),
                    triggered=d > 0.0, severity=sev, occurrence=occ,
                    detection=rule.detection, rpn=compute_rpn(sev, occ, rule.detection),
                    band=classify_risk(sev, compute_rpn(sev, occ, rule.detection)).value,
                    mechanisms=[rule.cause] if rule.cause else [], source=rd.source)
            if not hit:
                unmatched.append(rd)
        self._propagate(rows)
        report = RiskReport(risks=sorted(rows.values(),
                                         key=lambda r: (r.subsystem, r.rule_id, r.channel)),
                            unmatched=unmatched)
        return report

    # -- occurrence propagation along the matrix edges ---------------------- #
    def _propagate(self, rows: dict[str, LiveRisk]):
        triggered = [r for r in rows.values() if r.triggered]
        for edge in self.propagation:
            srcs = [r for r in triggered if r.subsystem == edge.src_subsystem and
                    (r.channel == edge.src_channel or
                     (edge.src_channel.endswith(":") and
                      r.channel.startswith(edge.src_channel)))]
            if not srcs:
                continue
            for dst in [r for r in rows.values() if r.rule_id == edge.dst_rule_id]:
                if edge.mechanism in dst.mechanisms:
                    continue
                dst.occurrence = min(10, dst.occurrence + edge.occurrence_bump)
                dst.rpn = compute_rpn(dst.severity, dst.occurrence, dst.detection)
                dst.band = classify_risk(dst.severity, dst.rpn).value
                dst.propagated_from.extend(s.rule_id for s in srcs
                                           if s.rule_id not in dst.propagated_from)
                dst.mechanisms.append(edge.mechanism)
                # Propagation can make a compliant downstream row worth watching,
                # but it never fabricates a breach: triggered stays deficit-driven.


# --------------------------------------------------------------------------- #
#  Manifold helper — turn a powertrain CoolingNetwork (duck-typed) or raw
#  per-segment ΔP figures into Readings the matrix understands.
# --------------------------------------------------------------------------- #
def manifold_readings(segments: dict[str, float], *,
                      pump_head_kpa: Optional[float] = None,
                      inlet_abs_kpa: Optional[float] = None,
                      vapor_pressure_kpa: float = 12.3,
                      source: str = "cooling-solver") -> list[Reading]:
    """
    segments: {segment_name: local ΔP in kPa}. Optionally derive the pump-inlet
    vapor margin: inlet_abs − Σ(suction ΔP) − p_vapor is the honest cavitation
    number, but with only loop-lump data we report inlet_abs − p_vapor.
    """
    out = [Reading("cooling", f"manifold_dp_kpa:{name}", float(dp), "kPa", source)
           for name, dp in sorted(segments.items())]
    if inlet_abs_kpa is not None:
        out.append(Reading("cooling", "pump_inlet_margin_kpa",
                           float(inlet_abs_kpa) - vapor_pressure_kpa, "kPa", source))
    if pump_head_kpa is not None:
        # A single segment eating > 60% of total pump head is itself dangerous
        # even under a generous absolute ceiling — surface it as a reading the
        # UI can display; the MAX rule still applies to the absolute number.
        pass
    return out


def network_readings(net, *, q_m3s: Optional[float] = None,
                     source: str = "powertrain.engine") -> list[Reading]:
    """Duck-typed bridge to powertrain.engine.CoolingNetwork: audits each
    PipeSegment at the operating point and emits per-segment ΔP readings."""
    if q_m3s is None:
        q_m3s = float(net.operating_point()["q_m3s"])
    segs = {getattr(s, "name", f"seg{i}"):
            float(s.audit(q_m3s, net.fluid)["dp_pa"]) / 1e3
            for i, s in enumerate(net.segments)}
    return manifold_readings(segs, source=source)


# =========================================================================== #
#  SLOTTED-HOLE FASTENER TORQUE & PRELOAD CALCULATOR (brake pedal tabs)
# =========================================================================== #
@dataclass
class SlottedHoleJoint:
    """
    One bolt clamping a SLOTTED hole (adjustment slot on a brake-pedal tab).

    Geometry (mm): the washer/head bears on an annulus Do=washer_od,
    Di=slot_width across the slot; along the slot the hole runs slot_length,
    so a strip of width slot_width is missing from the contact patch wherever
    the slot passes under the washer. slot_offset_mm shifts the bolt from the
    slot's centre (the tab clamped at one end of its adjustment range).
    """
    fastener: Fastener = field(default_factory=lambda: Fastener(grade="10.9",
                                                                nominal_d_mm=6.0))
    slot_width_mm: float = 6.6
    slot_length_mm: float = 18.0
    slot_offset_mm: float = 0.0
    washer_od_mm: float = 18.0
    thread_mu: float = 0.15          # μ_t, thread friction
    head_mu: float = 0.15            # μ_h, under-head friction
    bearing_allow_MPa: float = 300.0  # crush limit of the SOFTER face (tab material)
    thread_half_angle_deg: float = 30.0


@dataclass
class SlottedJointResult:
    K_nominal: float
    K_eff: float
    bearing_area_full_mm2: float
    bearing_area_slotted_mm2: float
    area_ratio: float
    r_head_full_mm: float
    r_head_eff_mm: float
    F_clamp_at_torque_N: Optional[float]
    torque_for_target_Nm: Optional[float]
    F_target_N: Optional[float]
    F_bearing_cap_N: float
    bearing_stress_MPa: Optional[float]
    bearing_capped: bool
    is_estimate: bool
    notes: str

    def as_dict(self):
        return asdict(self)

    def pedal_tab_fos_reading(self, slip_demand_N: float,
                              n_interfaces: int = 1, mu_joint: float = 0.35) -> Reading:
        """Feed the risk matrix: friction capacity vs in-plane slip demand."""
        F = self.F_clamp_at_torque_N or self.F_target_N or 0.0
        cap = mu_joint * F * max(1, n_interfaces)
        fos = cap / slip_demand_N if slip_demand_N > 0 else float("inf")
        return Reading("brakes", "pedal_tab_joint_fos", fos, "", "slotted-joint-calc")


def _contact_patch(j: SlottedHoleJoint, n_theta: int = 720, n_r: int = 120):
    """
    Numeric polar integration of the under-head contact patch: area A = ∫dA and
    uniform-pressure friction radius r_h = ∫r·dA / A over the washer annulus
    minus the slot cut-out (rectangle w×L centred at x = −slot_offset in head
    coordinates, plus its semicircular ends). Deterministic fixed grid.
    """
    Ro, w, L, off = (j.washer_od_mm / 2.0, j.slot_width_mm,
                     j.slot_length_mm, j.slot_offset_mm)
    hw, hl = w / 2.0, max(L / 2.0 - w / 2.0, 0.0)   # slot = rectangle + end caps
    cx = -off
    A = M = 0.0
    dth = 2.0 * math.pi / n_theta
    dr = Ro / n_r
    for it in range(n_theta):
        th = (it + 0.5) * dth
        c, s = math.cos(th), math.sin(th)
        for ir in range(n_r):
            r = (ir + 0.5) * dr
            x, y = r * c - cx, r * s          # coords relative to slot centre
            # inside slot (no contact)?  rectangle |x|<=hl,|y|<=hw  OR end caps
            in_slot = (abs(y) <= hw and abs(x) <= hl) or \
                      ((abs(x) - hl) ** 2 + y * y <= hw * hw if abs(x) > hl else False)
            if in_slot:
                continue
            dA = r * dr * dth
            A += dA
            M += r * dA
    return A, (M / A if A > 0 else 0.0)


def _annulus(Ro: float, Ri: float):
    A = math.pi * (Ro * Ro - Ri * Ri)
    # uniform-pressure friction radius of an annulus: (2/3)(Ro³−Ri³)/(Ro²−Ri²)
    r = (2.0 / 3.0) * (Ro ** 3 - Ri ** 3) / (Ro ** 2 - Ri ** 2)
    return A, r


def analyze_slotted_joint(j: SlottedHoleJoint, *,
                          assembly_torque_Nm: Optional[float] = None,
                          target_preload_fraction_of_proof: float = 0.65,
                          bearing_safety: float = 1.5) -> SlottedJointResult:
    """
    Motosh long-form torque relation: T = F·[ p/2π + μ_t·r_t/cos α + μ_h·r_h ],
    so K = T/(F·d) = [ p/2π + μ_t·r_t/cos α + μ_h·r_h ] / d.

    The slot only changes the LAST term: the under-head friction radius r_h is
    recomputed over the actual (slot-reduced, possibly asymmetric) contact
    patch. Clamping force from a wrench setting is then F = T/(K_eff·d), and
    the target preload is capped so bearing stress on the REDUCED area stays
    under bearing_allow/bearing_safety — the physical reason slotted joints
    must run lower preload than the catalogue value for the same bolt.
    """
    f = j.fastener
    d = float(f.nominal_d_mm)
    if d not in METRIC_COARSE and f.stress_area_mm2 is None:
        raise ValueError(f"unknown thread M{d}; supply stress_area_mm2")
    pitch = METRIC_COARSE.get(d, (0.25 * d, None))[0]
    At = f.stress_area()
    grade = BOLT_GRADES.get(f.grade)
    if grade is None:
        raise ValueError(f"unknown bolt grade {f.grade!r}")
    if j.slot_width_mm <= d:
        raise ValueError("slot_width_mm must exceed the bolt diameter")
    if j.washer_od_mm <= j.slot_width_mm:
        raise ValueError("washer_od_mm must exceed slot_width_mm — no bearing land")

    r_t = 0.5 * (d - 0.649519 * pitch)              # thread pitch radius d2/2
    cos_a = math.cos(math.radians(j.thread_half_angle_deg))
    Ro = j.washer_od_mm / 2.0

    A0, r_h0 = _annulus(Ro, j.slot_width_mm / 2.0)  # round-hole baseline
    A1, r_h1 = _contact_patch(j)
    ratio = A1 / A0 if A0 > 0 else 0.0

    def K_of(r_h):  # per-mm-of-diameter torque coefficient
        return (pitch / (2.0 * math.pi) + j.thread_mu * r_t / cos_a
                + j.head_mu * r_h) / d

    K0, K1 = K_of(r_h0), K_of(r_h1)

    F_proof = target_preload_fraction_of_proof * grade.proof_MPa * At
    F_bear_cap = (j.bearing_allow_MPa / bearing_safety) * A1
    F_target = min(F_proof, F_bear_cap)
    capped = F_bear_cap < F_proof
    T_target = K1 * F_target * d / 1e3              # N·mm → N·m

    F_at_T = sig_bear = None
    if assembly_torque_Nm is not None:
        F_at_T = assembly_torque_Nm * 1e3 / (K1 * d)
        sig_bear = F_at_T / A1

    notes = []
    if capped:
        notes.append(f"preload BEARING-CAPPED: slotted contact ({ratio:.0%} of full "
                     f"annulus) crushes before {target_preload_fraction_of_proof:.0%}·proof")
    if ratio < 0.55:
        notes.append("contact area < 55% of full annulus — fit a slot washer / "
                     "load-spreader plate; hand-calc K is an estimate here")
    if j.slot_offset_mm:
        notes.append("bolt offset in slot: contact is asymmetric; K_eff uses the "
                     "uniform-pressure friction radius of the actual patch")

    return SlottedJointResult(
        K_nominal=round(K0, 4), K_eff=round(K1, 4),
        bearing_area_full_mm2=round(A0, 2), bearing_area_slotted_mm2=round(A1, 2),
        area_ratio=round(ratio, 4), r_head_full_mm=round(r_h0, 3),
        r_head_eff_mm=round(r_h1, 3),
        F_clamp_at_torque_N=round(F_at_T, 1) if F_at_T is not None else None,
        torque_for_target_Nm=round(T_target, 2), F_target_N=round(F_target, 1),
        F_bearing_cap_N=round(F_bear_cap, 1),
        bearing_stress_MPa=round(sig_bear, 1) if sig_bear is not None else None,
        bearing_capped=capped,
        is_estimate=ratio < 0.55 or bool(j.slot_offset_mm),
        notes="; ".join(notes))
