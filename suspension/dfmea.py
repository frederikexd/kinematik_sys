"""DFMEA — Design Failure Mode & Effects Analysis for the Powertrain subsystem.

This module is the engine behind the in-app DFMEA Workbench. It exists so the
Powertrain team can stop hand-maintaining a loose ``FSAE_Powertrain_DFMEA.xlsx``
and instead keep a living risk log *inside* KinematiK, next to the FEA, lap-sim,
cooling and tractive-safety tools that actually generate the evidence a DFMEA
row needs to close.

Design goals (driven by the Powertrain Meeting #2 slides + the DFMEA User Guide):

* **Zero blank-page friction.** ``seed_rows()`` returns a starter log already
  populated with the exact failure modes from the User Guide's section-8 table
  *and* the live summer projects (cooling test rig, gear ratio / sprocket,
  motor & diff mount). A new member opens the tab and edits real rows instead of
  inventing a schema.
* **Scoring stays consistent.** The Severity / Occurrence / Detection scales from
  the guide are encoded once here (``SEVERITY_SCALE`` etc.) and surfaced as the
  column dropdowns + an in-app reference, so two people score the same risk the
  same way.
* **RPN is never hand-typed.** ``compute_rpn`` and ``classify_risk`` are pure
  functions; the table recomputes them live so a row can't drift out of sync.
* **It round-trips with their spreadsheet.** ``to_dataframe`` / ``from_records``
  use the User-Guide column names verbatim, so export lands in the same shape the
  judges and the rest of the team already expect, and an existing workbook can be
  imported without a rename.

Nothing here imports Streamlit, pandas or plotly at module load, so the logic is
unit-testable in isolation and the rest of KinematiK keeps importing cleanly.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


# --------------------------------------------------------------------------- #
# Canonical column order — matches the FSAE Powertrain DFMEA User Guide §6 so   #
# export/import is drop-in compatible with the team's existing workbook.        #
# --------------------------------------------------------------------------- #
COLUMNS: list[str] = [
    "Subsystem",
    "Item / Component",
    "Function / Requirement",
    "Failure Mode",
    "Effect of Failure",
    "Severity",
    "Potential Cause / Mechanism",
    "Occurrence",
    "Prevention Controls",
    "Detection Controls",
    "Detection",
    "RPN",
    "Recommended Action",
    "Owner",
    "Due Date",
    "Status",
    "Evidence / Notes",
]

# Subsystem options the Powertrain team realistically logs against. Mirrors the
# User Guide §6 ("Cooling, Motor Mounting, Accumulator Thermal, Drivetrain,
# HV/LV Interface, Sensors") rather than the raw interfaces.SUBSYSTEMS tags so
# the dropdown reads in the team's own language.
SUBSYSTEM_OPTIONS: list[str] = [
    "Cooling",
    "Motor Mounting",
    "Accumulator Thermal",
    "Drivetrain",
    "HV/LV Interface",
    "Sensors",
    "Pump / Coolant Loop",
    "Sprocket / Output Shaft",
]

STATUS_OPTIONS: list[str] = ["Open", "In Progress", "Closed", "Accepted"]


# --------------------------------------------------------------------------- #
# Rating scales (1..10) — straight from the User Guide §5 scoring guidance.     #
# Keeping the anchors in code means the in-app reference and the dropdown help  #
# can never disagree with each other.                                           #
# --------------------------------------------------------------------------- #
SEVERITY_SCALE: dict[int, str] = {
    1: "No effect — driver/team never notices.",
    2: "Very minor — slight serviceability annoyance.",
    3: "Minor — small maintenance delay, no performance loss.",
    4: "Low — minor performance loss, car still finishes.",
    5: "Moderate — noticeable derate or repair between runs.",
    6: "Significant — thermal derate / partial loss of function in event.",
    7: "High — likely DNF or failed scrutineering check.",
    8: "Very high — DNF plus damage to an expensive component.",
    9: "Serious — rule violation or potential safety hazard.",
    10: "Hazardous — fire, HV exposure, or injury risk.",
}

OCCURRENCE_SCALE: dict[int, str] = {
    1: "Remote — strong prevention control + prior proof it doesn't happen.",
    2: "Very low — robust design margin, documented.",
    3: "Low — good design practice, some supporting analysis.",
    4: "Low-moderate — reasonable design, no hard proof yet.",
    5: "Moderate — plausible cause, control exists but unproven.",
    6: "Moderate-high — similar parts have failed before.",
    7: "High — weak/aggressive design, thin margin.",
    8: "Very high — known marginal area, no prevention control.",
    9: "Frequent — expected to occur without a design change.",
    10: "Near-certain — failure is essentially guaranteed as drawn.",
}

DETECTION_SCALE: dict[int, str] = {
    1: "Almost certain — reliable test/inspection before every run.",
    2: "Very high — strong documented check before use.",
    3: "High — specific test or FEA/CFD catches it.",
    4: "Moderately high — inspection likely catches it.",
    5: "Moderate — generic check, may catch it.",
    6: "Low-moderate — only caught if someone looks carefully.",
    7: "Low — no dedicated check, relies on luck.",
    8: "Very low — hard to detect before failure.",
    9: "Remote — essentially only found after it fails on track.",
    10: "None — no possible detection before the car runs.",
}


class RiskBand(str, Enum):
    """Risk priority bands used by the dashboard. Severity is treated specially
    per the User Guide: a high-Severity row is always flagged even at moderate
    RPN, because 'do not ignore high-severity items just because RPN is moderate.'
    """

    CRITICAL = "Critical"   # high severity OR very high RPN — review first
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


# Thresholds are deliberately conservative for an FSAE log; tune in one place.
_RPN_HIGH = 200       # S·O·D above this is High regardless of severity
_RPN_CRITICAL = 300   # ...above this is Critical
_SEV_CRITICAL = 9     # any row at/above this severity is Critical (safety/rules)
_SEV_HIGH = 7         # any row at/above this severity is at least High


def _clamp_rating(v: Any) -> int:
    """Coerce a cell to a valid 1..10 rating, defaulting to 1 on garbage so the
    table never throws while someone is mid-edit."""
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return 1
    return max(1, min(10, n))


def compute_rpn(severity: Any, occurrence: Any, detection: Any) -> int:
    """RPN = Severity × Occurrence × Detection (User Guide §5). Inputs are
    clamped to 1..10 first so a partially-filled row still yields a sane number."""
    return (_clamp_rating(severity)
            * _clamp_rating(occurrence)
            * _clamp_rating(detection))


def classify_risk(severity: Any, rpn: Any) -> RiskBand:
    """Map (Severity, RPN) to a review band. Severity can promote a row on its
    own — a 9/10 severity safety item is Critical even if O and D are low."""
    s = _clamp_rating(severity)
    try:
        r = float(rpn)
    except (TypeError, ValueError):
        r = 0.0
    if s >= _SEV_CRITICAL or r >= _RPN_CRITICAL:
        return RiskBand.CRITICAL
    if s >= _SEV_HIGH or r >= _RPN_HIGH:
        return RiskBand.HIGH
    if r >= 80:
        return RiskBand.MEDIUM
    return RiskBand.LOW


@dataclass
class DFMEARow:
    """One DFMEA line. Field names map 1:1 onto COLUMNS via ``to_record``."""

    subsystem: str = "Cooling"
    item: str = ""
    function: str = ""
    failure_mode: str = ""
    effect: str = ""
    severity: int = 5
    cause: str = ""
    occurrence: int = 5
    prevention: str = ""
    detection_control: str = ""
    detection: int = 5
    action: str = ""
    owner: str = ""
    due_date: str = ""
    status: str = "Open"
    evidence: str = ""

    @property
    def rpn(self) -> int:
        return compute_rpn(self.severity, self.occurrence, self.detection)

    @property
    def band(self) -> RiskBand:
        return classify_risk(self.severity, self.rpn)

    def to_record(self) -> dict[str, Any]:
        """Flat dict keyed by the human COLUMNS names (for DataFrame/export)."""
        return {
            "Subsystem": self.subsystem,
            "Item / Component": self.item,
            "Function / Requirement": self.function,
            "Failure Mode": self.failure_mode,
            "Effect of Failure": self.effect,
            "Severity": _clamp_rating(self.severity),
            "Potential Cause / Mechanism": self.cause,
            "Occurrence": _clamp_rating(self.occurrence),
            "Prevention Controls": self.prevention,
            "Detection Controls": self.detection_control,
            "Detection": _clamp_rating(self.detection),
            "RPN": self.rpn,
            "Recommended Action": self.action,
            "Owner": self.owner,
            "Due Date": self.due_date,
            "Status": self.status,
            "Evidence / Notes": self.evidence,
        }


# Column-name -> DFMEARow field, for importing an existing workbook/CSV. Tolerant
# of case and surrounding whitespace; unknown columns are ignored.
_COL_TO_FIELD = {
    "subsystem": "subsystem",
    "item / component": "item",
    "item": "item",
    "function / requirement": "function",
    "function": "function",
    "failure mode": "failure_mode",
    "effect of failure": "effect",
    "effect": "effect",
    "severity": "severity",
    "potential cause / mechanism": "cause",
    "cause": "cause",
    "occurrence": "occurrence",
    "prevention controls": "prevention",
    "detection controls": "detection_control",
    "detection": "detection",
    "recommended action": "action",
    "action": "action",
    "owner": "owner",
    "due date": "due_date",
    "status": "status",
    "evidence / notes": "evidence",
    "evidence": "evidence",
}


def row_from_mapping(m: dict[str, Any]) -> DFMEARow:
    """Build a DFMEARow from a loose dict (e.g. a spreadsheet row). Keys are
    matched case-insensitively against the User-Guide column names; RPN is
    ignored on import because it is always recomputed."""
    kw: dict[str, Any] = {}
    for raw_key, val in m.items():
        key = str(raw_key).strip().lower()
        if key in ("rpn",):
            continue
        field_name = _COL_TO_FIELD.get(key)
        if field_name is None:
            continue
        if field_name in ("severity", "occurrence", "detection"):
            kw[field_name] = _clamp_rating(val)
        else:
            kw[field_name] = "" if val is None else str(val)
    return DFMEARow(**kw)


def from_records(records: list[dict[str, Any]]) -> list[DFMEARow]:
    return [row_from_mapping(r) for r in records]


# --------------------------------------------------------------------------- #
# Seed log — real content so the tab is useful on first open.                  #
#                                                                              #
# Sources:                                                                     #
#   * User Guide §8 "Powertrain-Specific Example Failure Modes" (the 7 rows)   #
#   * Meeting #2 summer plans: cooling test rig, gear ratio / sprocket design, #
#     motor & diff mount FEA.                                                  #
# Ratings are starting estimates the team is meant to challenge with evidence. #
# --------------------------------------------------------------------------- #
def seed_rows() -> list[dict[str, Any]]:
    rows = [
        DFMEARow(
            subsystem="Cooling",
            item="Printed coolant manifold (29 mm, 3/8 in bleed branch)",
            function="Route coolant and provide bleed branch without leakage at 1.5 bar gauge",
            failure_mode="Printed branch cracks at root",
            effect="Coolant leak, loss of cooling, possible DNF",
            severity=8, occurrence=5, detection=4,
            cause="Stress concentration at sharp branch root; weak inter-layer "
                  "adhesion from print orientation; thin wall section",
            prevention="Root fillet ≥ branch wall; print orientation review; "
                       "wall-thickness check in CAD",
            detection_control="Pressure test to 1.5× working pressure; visual "
                              "inspection at the root after each thermal cycle",
            action="Add ≥3 mm root fillet, re-orient print so layers don't split "
                   "the branch, pressure-test a prototype to 2.25 bar for 10 min",
            owner="", due_date="", status="Open",
            evidence="Link CFD/print run + pressure-test log here",
        ),
        DFMEARow(
            subsystem="Cooling",
            item="Coolant hose / barb joints",
            function="Retain coolant on barb fittings without blow-off",
            failure_mode="Hose not fully seated on barb",
            effect="Leak or hose blow-off under pressure",
            severity=7, occurrence=4, detection=3,
            cause="Insufficient seating depth; clamp positioned over the bead; "
                  "no positive stop on the barb",
            prevention="Hose stop collar / bead; specify seating depth; clamp "
                       "location standard",
            detection_control="Hose fitment test; clamp-location inspection before "
                              "every run",
            action="Add stop collars to all barbs, mark seating depth, inspect "
                   "clamp position during pre-run check",
            owner="", due_date="", status="Open",
            evidence="",
        ),
        DFMEARow(
            subsystem="Cooling",
            item="Radiator / shroud",
            function="Force endurance airflow through the radiator core",
            failure_mode="Air bypasses radiator core",
            effect="Insufficient cooling under endurance load; thermal derate",
            severity=6, occurrence=5, detection=4,
            cause="Gaps between shroud and core; poor sealing; low-pressure "
                  "recirculation around the core",
            prevention="Shroud sealing design; CFD of duct; foam/edge seals",
            detection_control="Smoke test of the duct; temperature logging on the "
                              "cooling test rig",
            action="Smoke-test the shroud, seal bypass gaps, log core inlet/outlet "
                   "ΔT on the test rig at representative flow",
            owner="", due_date="", status="Open",
            evidence="Tie to Cooling Test Rig (summer project)",
        ),
        DFMEARow(
            subsystem="Pump / Coolant Loop",
            item="Coolant pump",
            function="Maintain continuous coolant flow through the loop",
            failure_mode="Pump loses power or cavitates",
            effect="Loss of coolant flow; rapid overtemperature",
            severity=8, occurrence=4, detection=4,
            cause="LV power fault; air entrainment / poor bleed; undersized pump "
                  "for loop restriction; inlet starvation",
            prevention="Electrical fault check; expansion tank + bleed validation; "
                       "pump sized against measured loop ΔP",
            detection_control="Flow-bench test; flow/temperature sensor on the rig; "
                              "LV current monitor",
            action="Bench-test pump head vs loop restriction, validate bleed "
                   "procedure, add flow sensor + fault response",
            owner="", due_date="", status="Open",
            evidence="",
        ),
        DFMEARow(
            subsystem="Motor Mounting",
            item="Motor mount (bolt-hole ring)",
            function="Hold motor aligned under drive, regen, and bump loads",
            failure_mode="Fatigue crack at bolt-hole ring",
            effect="Motor misalignment or drivetrain failure; possible DNF",
            severity=8, occurrence=4, detection=5,
            cause="Stress concentration at bolt holes; cyclic torque + vibration; "
                  "thin land around the holes",
            prevention="Static FEA at peak drive/regen torque, 3 g bump, and "
                       "combined case; fatigue FEA; generous hole land",
            detection_control="Static + fatigue FEA; dye-penetrant / visual "
                              "inspection after each test session",
            action="Run static FEA for peak drive torque, peak regen torque, 3 g "
                   "vertical bump, and combined torque+bump; document min FoS and "
                   "max deflection; add fatigue check",
            owner="", due_date="", status="Open",
            evidence="Tie to Motor Mount FEA (in progress per Meeting #2)",
        ),
        DFMEARow(
            subsystem="Drivetrain",
            item="Drivetrain fasteners",
            function="Maintain clamp load and alignment under vibration",
            failure_mode="Fastener loosens under vibration",
            effect="Alignment loss, chain/belt issue, DNF",
            severity=7, occurrence=5, detection=5,
            cause="Insufficient or unverified bolt preload; no locking feature; "
                  "vibration backing-off",
            prevention="Bolt preload spec; thread-locker / locking method; "
                       "paint-mark datum",
            detection_control="Torque audit before each event; paint-mark "
                              "witness-line inspection",
            action="Define preload spec, add locking method, paint-mark and "
                   "torque-audit before every run",
            owner="", due_date="", status="Open",
            evidence="",
        ),
        DFMEARow(
            subsystem="HV/LV Interface",
            item="Cooling pump LV supply",
            function="Keep LV power to the cooling pump during operation",
            failure_mode="Cooling pump loses LV power",
            effect="Thermal derate or component overtemperature",
            severity=7, occurrence=3, detection=4,
            cause="Blown fuse; connector backout; LV harness fault",
            prevention="Fusing review; connector retention / locking; harness "
                       "strain relief",
            detection_control="Electrical test of the LV branch; defined fault "
                              "response (derate/shutdown) on flow loss",
            action="Review fuse rating, lock connectors, define and test the "
                   "fault response when flow is lost",
            owner="", due_date="", status="Open",
            evidence="Cross-check in the Tractive Safety tab",
        ),
        # ---- Live summer projects from Meeting #2 that aren't yet in §8 ----- #
        DFMEARow(
            subsystem="Sprocket / Output Shaft",
            item="Output-shaft sprocket",
            function="Transmit motor torque to the chain at the chosen gear ratio "
                     "without tooth failure",
            failure_mode="Tooth root yields / shears under peak chain tension",
            effect="Loss of drive, chain damage, DNF",
            severity=8, occurrence=4, detection=4,
            cause="Tooth forces from gear ratio + peak motor torque under-"
                  "estimated; thin root; material/heat-treat not verified",
            prevention="Size teeth from gear ratio, motor torque and chain "
                       "tension; pick proven material/heat-treat",
            detection_control="Static FEA at peak tooth force; verify against a "
                              "standard chain/sprocket rating",
            action="Compute tooth forces from gear ratio + motor specs + chain "
                   "tension, run FEA to target FoS, then choose manufacturing "
                   "method",
            owner="", due_date="", status="Open",
            evidence="Sprocket design owner needed (Meeting #2)",
        ),
        DFMEARow(
            subsystem="Drivetrain",
            item="Final-drive gear ratio",
            function="Match motor operating range to the event speed range",
            failure_mode="Gear ratio mis-sized (too short / too long)",
            effect="Top-speed limited or poor corner-exit accel; lap-time loss",
            severity=4, occurrence=5, detection=3,
            cause="Ratio chosen before output-shaft CAD and vehicle variables "
                  "are solidified",
            prevention="Sweep ratio in the EV Powertrain / Lap Time tabs against "
                       "the event speed trace",
            detection_control="Lap-sim ratio sweep; acceleration-event check",
            action="Solidify output-shaft CAD + variables, sweep ratio in lap "
                   "sim, lock the optimum within ~2 weeks (Meeting #2 goal)",
            owner="", due_date="", status="Open",
            evidence="Use KinematiK EV Powertrain + Lap Time tabs as evidence",
        ),
        DFMEARow(
            subsystem="Accumulator Thermal",
            item="Accumulator cooling",
            function="Keep cell/module temperatures within target over endurance",
            failure_mode="Accumulator cooling underperforms",
            effect="Thermal derate or cell over-temperature in endurance",
            severity=7, occurrence=4, detection=4,
            cause="Underestimated heat load; airflow/PCM buffer insufficient; "
                  "hot-spot between modules",
            prevention="Pack thermal model; PCM buffer sizing; airflow path "
                       "design",
            detection_control="Pack-thermal sim; temperature logging on the test "
                              "rig; PCM buffer check in Tractive Safety tab",
            action="Run pack-thermal model for the endurance duty cycle, size "
                   "the PCM/airflow buffer, validate on the rig",
            owner="", due_date="", status="Open",
            evidence="Tie to Accumulator + Tractive Safety (PCM) tabs",
        ),
    ]
    return [r.to_record() for r in rows]


# --------------------------------------------------------------------------- #
# Roll-ups for the dashboard.                                                  #
# --------------------------------------------------------------------------- #
@dataclass
class DashboardStats:
    total: int = 0
    by_band: dict[str, int] = field(default_factory=dict)
    by_status: dict[str, int] = field(default_factory=dict)
    by_subsystem: dict[str, int] = field(default_factory=dict)
    open_high_risk: int = 0          # Critical/High that are not Closed/Accepted
    high_sev_open: int = 0           # severity >= _SEV_HIGH and not closed
    actions_without_owner: int = 0   # open rows w/ an action but no owner
    closed_without_evidence: int = 0 # 'Closed' rows missing evidence (guide rule)
    top_rows: list[dict[str, Any]] = field(default_factory=list)


def _is_resolved(status: Any) -> bool:
    return str(status).strip().lower() in ("closed", "accepted")


def dashboard_stats(records: list[dict[str, Any]], top_n: int = 8) -> DashboardStats:
    """Compute the dashboard roll-up the User Guide §7 asks for: open high-risk
    items, risk concentration by component, and data-quality flags (closed
    without evidence, action without an owner)."""
    stats = DashboardStats(total=len(records))
    scored: list[tuple[int, dict[str, Any], RiskBand]] = []

    for rec in records:
        sev = rec.get("Severity", 1)
        rpn = compute_rpn(sev, rec.get("Occurrence", 1), rec.get("Detection", 1))
        band = classify_risk(sev, rpn)
        status = rec.get("Status", "Open")

        stats.by_band[band.value] = stats.by_band.get(band.value, 0) + 1
        stats.by_status[str(status)] = stats.by_status.get(str(status), 0) + 1
        sub = str(rec.get("Subsystem", "—")) or "—"
        stats.by_subsystem[sub] = stats.by_subsystem.get(sub, 0) + 1

        resolved = _is_resolved(status)
        if band in (RiskBand.CRITICAL, RiskBand.HIGH) and not resolved:
            stats.open_high_risk += 1
        if _clamp_rating(sev) >= _SEV_HIGH and not resolved:
            stats.high_sev_open += 1
        if (not resolved
                and str(rec.get("Recommended Action", "")).strip()
                and not str(rec.get("Owner", "")).strip()):
            stats.actions_without_owner += 1
        if (str(status).strip().lower() == "closed"
                and not str(rec.get("Evidence / Notes", "")).strip()):
            stats.closed_without_evidence += 1

        scored.append((rpn, rec, band))

    scored.sort(key=lambda t: (t[0], _clamp_rating(t[1].get("Severity", 1))),
                reverse=True)
    stats.top_rows = [
        {**rec, "RPN": rpn, "Risk Band": band.value}
        for rpn, rec, band in scored[:top_n]
    ]
    return stats


def action_items(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten DFMEA rows into an Action Tracker view (User Guide §7): every row
    that has a recommended action and is not yet resolved becomes a tracked item,
    sorted by risk so the most important work surfaces first."""
    items: list[dict[str, Any]] = []
    for rec in records:
        action = str(rec.get("Recommended Action", "")).strip()
        if not action:
            continue
        if _is_resolved(rec.get("Status", "Open")):
            continue
        rpn = compute_rpn(rec.get("Severity", 1), rec.get("Occurrence", 1),
                          rec.get("Detection", 1))
        band = classify_risk(rec.get("Severity", 1), rpn)
        items.append({
            "Risk Band": band.value,
            "RPN": rpn,
            "Subsystem": rec.get("Subsystem", ""),
            "Item / Component": rec.get("Item / Component", ""),
            "Recommended Action": action,
            "Owner": rec.get("Owner", ""),
            "Due Date": rec.get("Due Date", ""),
            "Status": rec.get("Status", "Open"),
            "Evidence / Notes": rec.get("Evidence / Notes", ""),
        })
    _order = {b.value: i for i, b in enumerate(
        [RiskBand.CRITICAL, RiskBand.HIGH, RiskBand.MEDIUM, RiskBand.LOW])}
    items.sort(key=lambda d: (_order.get(d["Risk Band"], 9), -int(d["RPN"])))
    return items
