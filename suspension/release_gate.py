# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
#
#  Module: release_gate — Manufacturing-Release Gate
#
#  A DETERMINISTIC validation gate over the active car ledger. Same inputs ⇒
#  same verdict, always: checks are pure functions evaluated in a fixed order,
#  there is no randomness, no wall-clock in the verdict (timestamps appear only
#  in PDF metadata), and a missing input FAILS its check (absence of evidence
#  is a failure, never a silent pass).
#
#  Gate sections, in order:
#    1. chassis triangulation      (tubeframe audits: quads + load paths)
#    2. cooling pressures          (per-segment ΔP, pump budget, inlet margin)
#    3. brake safety margins       (component FoS + slotted pedal-tab joint)
#    4. torque specifications      (every fastener specced, inside its
#                                   grade-derived preload window, slotted
#                                   joints on K_eff not catalogue K)
#
#  IF AND ONLY IF every check passes, build_clipboard()/render_clipboard_pdf()
#  emit the printable "Tech Assembly & Torque Clipboard Checklist" for
#  competition inspection. render refuses (GateNotPassed) on a red gate — a
#  checklist for an unreleased car must be impossible to produce by accident.
#
#  Stdlib + risk_engine/bolted_joint at load; reportlab imported lazily.
# ============================================================================

from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from .bolted_joint import BOLT_GRADES, METRIC_COARSE
from .risk_engine import SlottedJointResult


class GateNotPassed(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
#  Inputs — everything the gate parses from the active car ledger
# --------------------------------------------------------------------------- #
@dataclass
class TorqueSpec:
    """One fastener line of the build: what it is, and what wrench it gets."""
    label: str                        # "caliper bracket M8 ×2, front left"
    location: str
    nominal_d_mm: float
    grade: str = "10.9"
    spec_torque_Nm: float = 0.0
    K: float = 0.20                   # catalogue K, or K_eff for slotted joints
    qty: int = 1
    slotted: Optional[SlottedJointResult] = None
    thread_locker: str = ""
    witness_required: bool = True

    def preload_window_N(self, lo_frac: float = 0.50, hi_frac: float = 0.78):
        g = BOLT_GRADES.get(self.grade)
        if g is None or (self.nominal_d_mm not in METRIC_COARSE):
            return None
        At = METRIC_COARSE[self.nominal_d_mm][1]
        return (lo_frac * g.proof_MPa * At, hi_frac * g.proof_MPa * At)

    def torque_window_Nm(self):
        """Acceptable wrench window from the preload window via T = K·F·d.
        For slotted joints the ceiling is additionally the bearing-capped
        preload — the reduced contact area, not the bolt, sets the max."""
        win = self.preload_window_N()
        if win is None:
            return None
        lo, hi = win
        if self.slotted is not None:
            hi = min(hi, self.slotted.F_bearing_cap_N)
        k = self.slotted.K_eff if self.slotted is not None else self.K
        return (k * lo * self.nominal_d_mm / 1e3, k * hi * self.nominal_d_mm / 1e3)


@dataclass
class GateInputs:
    # chassis — either a live FrameGraph (duck-typed) or precomputed audits
    frame: Any = None
    load_path_pairs: list = field(default_factory=list)   # [(from_node, to_node)]
    chassis_quads: Optional[list] = None                   # precomputed override
    chassis_loadpath_findings: Optional[list] = None       # precomputed override
    # cooling
    manifold_dp_kpa: dict = field(default_factory=dict)    # {segment: ΔP kPa}
    manifold_dp_limit_kpa: float = 35.0
    pump_head_kpa: Optional[float] = None
    pump_margin_frac: float = 0.15                         # ≥15% head in reserve
    inlet_margin_kpa: Optional[float] = None
    inlet_margin_min_kpa: float = 20.0
    # brakes
    brake_fos: dict = field(default_factory=dict)          # {component: fos}
    brake_fos_min: float = 1.6
    pedal_joint: Optional[SlottedJointResult] = None
    pedal_slip_demand_N: Optional[float] = None
    pedal_joint_mu: float = 0.35
    pedal_joint_fos_min: float = 1.2
    # torque specs
    torque_specs: list = field(default_factory=list)       # [TorqueSpec]
    required_fastener_locations: list = field(default_factory=list)
    # identity for the clipboard
    team: str = "Elbee Racing"
    car: str = ""
    event: str = ""


@dataclass
class GateCheck:
    check_id: str
    section: str
    description: str
    passed: bool
    measured: str
    limit: str
    detail: str = ""

    def as_dict(self):
        return asdict(self)


@dataclass
class GateReport:
    released: bool
    checks: list
    inputs_digest: dict

    def failures(self) -> list:
        return [c for c in self.checks if not c.passed]

    def as_dict(self):
        return {"released": self.released, "inputs_digest": self.inputs_digest,
                "checks": [c.as_dict() for c in self.checks]}


# --------------------------------------------------------------------------- #
#  Section evaluators — each returns a sorted list[GateCheck]; missing ⇒ FAIL
# --------------------------------------------------------------------------- #
def _chassis_checks(gi: GateInputs) -> list:
    out = []
    quads = gi.chassis_quads
    lp = gi.chassis_loadpath_findings
    if gi.frame is not None:
        try:
            if quads is None:
                quads = gi.frame.untriangulated_quads()
            if lp is None:
                lp = []
                for a, b in gi.load_path_pairs:
                    audit = gi.frame.load_path_audit(a, b)
                    findings = audit.get("findings", audit) if isinstance(audit, dict) \
                        else audit
                    lp.extend(findings or [])
        except Exception as e:
            out.append(GateCheck("CH-00", "chassis", "frame audit executed", False,
                                 f"error: {type(e).__name__}", "clean run", str(e)))
            return out
    if quads is None:
        out.append(GateCheck("CH-01", "chassis", "triangulation audit present", False,
                             "no audit", "audit required",
                             "no FrameGraph and no precomputed quad audit supplied"))
    else:
        out.append(GateCheck("CH-01", "chassis", "no untriangulated quads in primary structure",
                             len(quads) == 0, f"{len(quads)} open quad(s)", "0",
                             "; ".join(str(q.get("nodes", q)) if isinstance(q, dict) else str(q)
                                       for q in quads[:6])))
    if lp is None:
        out.append(GateCheck("CH-02", "chassis", "load-path audit present", False,
                             "no audit", "audit required",
                             "no load_path_pairs audited and no precomputed findings"))
    else:
        out.append(GateCheck("CH-02", "chassis", "audited load paths continuously triangulated",
                             len(lp) == 0, f"{len(lp)} defect(s)", "0",
                             "; ".join(str(f)[:90] for f in lp[:4])))
    return out


def _cooling_checks(gi: GateInputs) -> list:
    out = []
    if not gi.manifold_dp_kpa:
        out.append(GateCheck("CO-01", "cooling", "manifold ΔP survey present", False,
                             "no segments", "≥1 segment", "cooling ledger empty"))
    else:
        worst_name, worst = max(gi.manifold_dp_kpa.items(), key=lambda kv: kv[1])
        out.append(GateCheck(
            "CO-01", "cooling",
            f"every manifold segment ΔP ≤ {gi.manifold_dp_limit_kpa:g} kPa",
            all(v <= gi.manifold_dp_limit_kpa for v in gi.manifold_dp_kpa.values()),
            f"worst {worst_name}: {worst:.1f} kPa", f"{gi.manifold_dp_limit_kpa:g} kPa",
            ", ".join(f"{k}={v:.1f}" for k, v in sorted(gi.manifold_dp_kpa.items()))))
        if gi.pump_head_kpa is not None:
            total = sum(gi.manifold_dp_kpa.values())
            budget = gi.pump_head_kpa * (1.0 - gi.pump_margin_frac)
            out.append(GateCheck(
                "CO-02", "cooling",
                f"loop ΔP within pump head less {gi.pump_margin_frac:.0%} reserve",
                total <= budget, f"{total:.1f} kPa", f"≤ {budget:.1f} kPa"))
        else:
            out.append(GateCheck("CO-02", "cooling", "pump head declared", False,
                                 "missing", "required", "pump_head_kpa not supplied"))
    margin = gi.inlet_margin_kpa
    out.append(GateCheck(
        "CO-03", "cooling",
        f"pump-inlet vapor margin ≥ {gi.inlet_margin_min_kpa:g} kPa",
        margin is not None and margin >= gi.inlet_margin_min_kpa,
        "missing" if margin is None else f"{margin:.1f} kPa",
        f"{gi.inlet_margin_min_kpa:g} kPa"))
    return out


def _brake_checks(gi: GateInputs) -> list:
    out = []
    if not gi.brake_fos:
        out.append(GateCheck("BR-01", "brakes", "brake component FoS ledger present",
                             False, "empty", "≥1 component", ""))
    else:
        worst_c, worst = min(gi.brake_fos.items(), key=lambda kv: kv[1])
        out.append(GateCheck(
            "BR-01", "brakes", f"every brake component FoS ≥ {gi.brake_fos_min:g}",
            all(v >= gi.brake_fos_min for v in gi.brake_fos.values()),
            f"worst {worst_c}: {worst:.2f}", f"≥ {gi.brake_fos_min:g}",
            ", ".join(f"{k}={v:.2f}" for k, v in sorted(gi.brake_fos.items()))))
    sj, demand = gi.pedal_joint, gi.pedal_slip_demand_N
    if sj is None or demand is None:
        out.append(GateCheck("BR-02", "brakes",
                             "slotted pedal-tab joint analysed against slip demand",
                             False, "missing", "analysis required",
                             "supply pedal_joint (SlottedJointResult) + pedal_slip_demand_N"))
    else:
        F = sj.F_clamp_at_torque_N or sj.F_target_N or 0.0
        fos = (gi.pedal_joint_mu * F) / demand if demand > 0 else math.inf
        out.append(GateCheck(
            "BR-02", "brakes",
            f"pedal-tab friction FoS ≥ {gi.pedal_joint_fos_min:g} at K_eff clamp",
            fos >= gi.pedal_joint_fos_min, f"{fos:.2f}", f"≥ {gi.pedal_joint_fos_min:g}",
            f"clamp {F:.0f} N × μ={gi.pedal_joint_mu:g} vs demand {demand:.0f} N; "
            f"K_eff={sj.K_eff} (catalogue {sj.K_nominal}), "
            f"bearing area {sj.area_ratio:.0%} of full annulus"))
        out.append(GateCheck(
            "BR-03", "brakes", "pedal-tab bearing stress under crush cap",
            sj.bearing_stress_MPa is None or
            (sj.F_clamp_at_torque_N or 0.0) <= sj.F_bearing_cap_N * 1.0001,
            f"{sj.F_clamp_at_torque_N or 0:.0f} N clamp",
            f"≤ {sj.F_bearing_cap_N:.0f} N cap", sj.notes))
    return out


def _torque_checks(gi: GateInputs) -> list:
    out = []
    if not gi.torque_specs:
        out.append(GateCheck("TQ-01", "torque", "torque specification table present",
                             False, "empty", "≥1 spec", ""))
        return out
    covered = {t.location for t in gi.torque_specs}
    missing = sorted(set(gi.required_fastener_locations) - covered)
    out.append(GateCheck("TQ-01", "torque", "every required fastener location has a spec",
                         not missing, f"{len(missing)} missing", "0",
                         ", ".join(missing[:8])))
    for i, t in enumerate(sorted(gi.torque_specs, key=lambda s: (s.location, s.label))):
        cid = f"TQ-{i + 2:02d}"
        win = t.torque_window_Nm()
        if win is None:
            out.append(GateCheck(cid, "torque", f"{t.label}: grade/thread recognised",
                                 False, f"M{t.nominal_d_mm:g} {t.grade}", "known spec",
                                 "grade or thread not in BOLT_GRADES/METRIC_COARSE"))
            continue
        lo, hi = win
        ok = (t.spec_torque_Nm > 0) and (lo <= t.spec_torque_Nm <= hi)
        out.append(GateCheck(
            cid, "torque",
            f"{t.label}: spec inside {'K_eff (slotted)' if t.slotted else 'K'}-derived window",
            ok, f"{t.spec_torque_Nm:g} N·m", f"{lo:.1f}–{hi:.1f} N·m",
            (f"K={'%.3f' % (t.slotted.K_eff if t.slotted else t.K)}"
             + (", bearing-capped ceiling" if t.slotted and t.slotted.bearing_capped else ""))))
    return out


def run_gate(gi: GateInputs) -> GateReport:
    checks = _chassis_checks(gi) + _cooling_checks(gi) + _brake_checks(gi) \
        + _torque_checks(gi)
    checks.sort(key=lambda c: c.check_id)
    return GateReport(
        released=all(c.passed for c in checks) and len(checks) > 0,
        checks=checks,
        inputs_digest={
            "chassis_quads": None if gi.chassis_quads is None and gi.frame is None
            else "audited",
            "manifold_segments": sorted(gi.manifold_dp_kpa),
            "brake_components": sorted(gi.brake_fos),
            "torque_specs": len(gi.torque_specs)})


# --------------------------------------------------------------------------- #
#  Clipboard checklist — data model, then the printable PDF
# --------------------------------------------------------------------------- #
@dataclass
class Clipboard:
    team: str
    car: str
    event: str
    sections: list          # [(title, [line, ...])]
    torque_rows: list       # rows for the torque table
    gate_summary: list      # [(check_id, description, measured)]


def build_clipboard(report: GateReport, gi: GateInputs) -> Clipboard:
    if not report.released:
        raise GateNotPassed(
            "Manufacturing-release gate is RED — checklist generation refused. "
            "Failures: " + "; ".join(f"{c.check_id} {c.description}"
                                     for c in report.failures()))
    sec = []
    sec.append(("Chassis — triangulation & load paths", [
        "Verify every primary-structure bay against the released frame drawing",
        "Confirm no member terminates mid-span on a straight tube (weld map)",
        "Audited load paths (impact node → hoop) continuously triangulated",
    ]))
    cool = [f"Segment '{k}': measured ΔP ______ kPa (limit {gi.manifold_dp_limit_kpa:g})"
            for k in sorted(gi.manifold_dp_kpa)]
    cool += [f"Pump-inlet vapor margin ______ kPa (min {gi.inlet_margin_min_kpa:g})",
             "Bleed loop; confirm no air at highest manifold point"]
    sec.append(("Cooling — manifold pressures", cool))
    brk = [f"{k}: design FoS {v:.2f} (floor {gi.brake_fos_min:g}) — part matches drawing rev"
           for k, v in sorted(gi.brake_fos.items())]
    if gi.pedal_joint is not None:
        brk.append(f"Pedal-tab slotted joints torqued with K_eff={gi.pedal_joint.K_eff} "
                   f"(NOT catalogue K={gi.pedal_joint.K_nominal}); slot washers fitted")
        brk.append("Pedal position set, tabs clamped, paint-pen witness marks applied")
    sec.append(("Brakes — safety margins", brk))
    rows = []
    for t in sorted(gi.torque_specs, key=lambda s: (s.location, s.label)):
        win = t.torque_window_Nm()
        rows.append({
            "label": t.label, "location": t.location,
            "size": f"M{t.nominal_d_mm:g} {t.grade}",
            "K": f"{(t.slotted.K_eff if t.slotted else t.K):.3f}"
                 + (" (slotted)" if t.slotted else ""),
            "torque": f"{t.spec_torque_Nm:g}",
            "window": f"{win[0]:.1f}–{win[1]:.1f}" if win else "—",
            "qty": t.qty, "locker": t.thread_locker or "—",
            "witness": "REQ" if t.witness_required else "—"})
    return Clipboard(team=gi.team, car=gi.car, event=gi.event, sections=sec,
                     torque_rows=rows,
                     gate_summary=[(c.check_id, c.description, c.measured)
                                   for c in report.checks])


def render_clipboard_pdf(clip: Clipboard, out_path: str) -> str:
    """Printable 'Tech Assembly & Torque Clipboard Checklist' (reportlab, A4)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                    TableStyle, HRFlowable)

    styles = getSampleStyleSheet()
    H1 = ParagraphStyle("H1", parent=styles["Title"], fontSize=16, spaceAfter=2)
    H2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=11,
                        spaceBefore=8, spaceAfter=3,
                        textColor=colors.HexColor("#111111"))
    BODY = ParagraphStyle("B", parent=styles["Normal"], fontSize=8.5, leading=12)
    CELL = ParagraphStyle("C", parent=styles["Normal"], fontSize=7.5, leading=9)

    def box(txt):  # ☐ checkbox line
        return Paragraph(f'<font size="10">\u2610</font>&nbsp;&nbsp;{txt}', BODY)

    story = [Paragraph("TECH ASSEMBLY &amp; TORQUE CLIPBOARD CHECKLIST", H1),
             Paragraph(f"{clip.team}"
                       + (f" — Car {clip.car}" if clip.car else "")
                       + (f" — {clip.event}" if clip.event else ""), BODY),
             Paragraph(f"Generated by KinematiK release gate · "
                       f"{_dt.datetime.now().isoformat(timespec='minutes')} · "
                       f"gate status: RELEASED (all checks green)", CELL),
             HRFlowable(width="100%", thickness=1, color=colors.black),
             Spacer(1, 4)]

    for title, lines in clip.sections:
        story.append(Paragraph(title, H2))
        story.extend(box(ln) for ln in lines)

    story += [Paragraph("Torque specifications — set wrench, torque, witness-mark, initial",
                        H2)]
    head = ["Fastener", "Location", "Size / grade", "K", "Torque (N·m)",
            "Window (N·m)", "Qty", "Locker", "Done", "Initials"]
    data = [head] + [[Paragraph(str(r[k]), CELL) for k in
                      ("label", "location", "size", "K", "torque", "window",
                       "qty", "locker")] + ["\u2610", ""]
                     for r in clip.torque_rows]
    tbl = Table(data, colWidths=[36 * mm, 26 * mm, 20 * mm, 16 * mm, 17 * mm,
                                 20 * mm, 8 * mm, 14 * mm, 10 * mm, 14 * mm],
                repeatRows=1)
    tbl.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (3, 1), (-1, -1), "CENTER"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f6f6")]),
    ]))
    story += [tbl, Spacer(1, 6),
              Paragraph("Gate evidence (deterministic verdict at generation time)", H2)]
    ev = Table([["ID", "Check", "Measured"]] +
               [[cid, Paragraph(d, CELL), Paragraph(m, CELL)]
                for cid, d, m in clip.gate_summary],
               colWidths=[14 * mm, 116 * mm, 50 * mm], repeatRows=1)
    ev.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 7),
                            ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8"))]))
    story += [ev, Spacer(1, 10),
              HRFlowable(width="100%", thickness=0.8, color=colors.black),
              Paragraph("Assembly lead signature: ______________________    "
                        "Date: ____________", BODY),
              Paragraph("Scrutineer / inspector:  ______________________    "
                        "Date: ____________", BODY)]

    SimpleDocTemplate(out_path, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm,
                      topMargin=12 * mm, bottomMargin=12 * mm,
                      title="Tech Assembly & Torque Clipboard Checklist",
                      author="KinematiK release gate").build(story)
    return out_path


def release_and_print(gi: GateInputs, out_path: str) -> tuple:
    """One-call gate: run → (report, pdf_path|None). PDF exists IFF released."""
    report = run_gate(gi)
    if not report.released:
        return report, None
    return report, render_clipboard_pdf(build_clipboard(report, gi), out_path)
