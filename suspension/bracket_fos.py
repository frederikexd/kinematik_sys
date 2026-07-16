# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================

"""
Bracket Factor-of-Safety screening — the FoS ≥ 1.5-on-yield gate the chassis
team set as the standing rule for every mount it now has to design.

WHY THIS MODULE EXISTS
----------------------
The chassis lead's own brief is blunt about it: the main frame is "more or less
figured out", and the whole team's job from here is *mounting bracket design and
strength validation*, against a single hard rule — a **minimum Factor of Safety
of 1.5, computed on YIELD strength, not ultimate**. That rule has to be applied,
the same way, to a long tail of brackets nobody has drawn yet: driver's-seat
mounting, harness attachment, chassis floor tabs, aero attachment points, the
firewall, steering-column mounting, accumulator mounts, suspension pickups, and
"much more". Each member doing that by hand, in their own spreadsheet, with their
own idea of which stress to divide by which strength, is exactly the friction —
and the inconsistency — this module removes.

So this is the screening calculator that sits *upstream* of FEA. A member sizes a
bracket foot, picks a material, states the worst-case load the part sees, and gets
back — instantly, the same way every time — the governing stress in each standard
mounting-bracket failure mode, the resulting FoS on yield, and a single PASS / TIGHT
/ FAIL verdict against the 1.5 rule. It does NOT replace the SolidWorks / Ansys
simulation the brief also asks for; it tells you whether a bracket is even in the
right ballpark *before* you spend an afternoon meshing it, and it makes the cheap,
obviously-undersized ones fail in five seconds instead of in a sim queue.

WHAT IS CLOSED-FORM HERE (and therefore honest)
-----------------------------------------------
Every failure mode below is a standard, defensible hand calculation — the same
ones an FSAE structures review or a design judge expects to see on a bracket:

  * direct tension / shear of the bracket cross-section,
  * bending of a cantilevered bracket foot (σ = M·c / I), the mode that actually
    governs most tab brackets because the load sits on a lever arm,
  * bearing at the bolt hole (P / (d·t)) and tear-out / shear-out of the material
    between the hole and the free edge,
  * a fillet-weld throat-shear check for the weld that attaches the bracket to the
    frame — the "design for welding" half of the same brief.

None of these needs an FEA to be correct, and inventing one wouldn't make them more
correct. What this module deliberately does NOT do is claim to capture stress
concentration at the fillet root, weld-toe HAZ behaviour, or the true 3-D stiffness
of a contoured bracket — those are the genuinely FEA-shaped parts, and the result
carries `screening_only=True` so nobody mistakes a green light here for a passed
simulation. This is the same non-goal the rest of KinematiK keeps (see
bolted_joint.py, flex.py): do the part that is genuinely closed-form, be loud about
the part that isn't.

MATERIAL LIBRARY
----------------
Yield strengths for the structural steels the chassis team is actually choosing
between — including the live 4130 → 1018 cold-rolled decision in the brief — plus
the aluminium grades brackets get cut from. The brief's own argument for 1018
(rigidity/density close to 4130, strength lower "but not by much", far cheaper,
far easier to weld, no post-weld heat treat) is exactly the trade this library
lets you quantify: the FoS on a 1018 bracket vs the same bracket in 4130, side by
side, in the numbers rather than the hand-wave. Critically, 4130's *welded*
(as-welded, no post-weld heat treatment) yield is carried separately from its
tubing yield, because an un-normalised 4130 weld does not keep the parent
strength — the very pain the team is switching away from.

UNITS: mm, N, MPa (N/mm²), N·mm. Consistent with the rest of KinematiK.

REFERENCES: Shigley, *Mechanical Engineering Design* (direct/bending stress,
bearing, factor of safety on yield); AWS D1.1-style fillet-weld throat shear
(0.707·leg effective throat); standard FSAE structural-bracket hand methods.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Optional

from .interfaces import Finding, Severity


# --------------------------------------------------------------------------- #
#  Material library — yield strength is the number the 1.5 rule divides into.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class BracketMaterial:
    """A structural material for bracket screening.

    yield_MPa  : 0.2 % yield strength — the FoS rule's denominator.
    uts_MPa    : ultimate tensile, carried for reference / reporting only.
    shear_yield_factor : fraction of tensile yield taken as shear yield. 0.577 is
                 the von-Mises (distortion-energy) value used for ductile steels
                 and aluminium; the shear modes below divide the *shear* yield, not
                 the tensile yield, so a shear-governed bracket isn't flattered.
    weld_note  : why a welded value differs from the parent, where it does.
    """
    name: str
    yield_MPa: float
    uts_MPa: float
    rho_kg_m3: float
    shear_yield_factor: float = 0.577
    weldable_no_pwht: bool = True
    weld_note: str = ""

    @property
    def shear_yield_MPa(self) -> float:
        return self.yield_MPa * self.shear_yield_factor


# Values are typical published minimums for the condition named. They are
# screening figures: confirm against your actual cert / supplier data for a final
# design, the same way you would for any hand calc that precedes an FEA.
MATERIALS: dict = {
    # The live decision in the chassis brief, both sides of it:
    "Steel 1018 CR (cold-rolled)": BracketMaterial(
        "Steel 1018 CR (cold-rolled)", yield_MPa=370.0, uts_MPa=440.0, rho_kg_m3=7870.0,
        weldable_no_pwht=True,
        weld_note="Low-carbon mild steel — welds readily and needs no post-weld "
                  "heat treat; as-welded strength stays close to parent. This is the "
                  "team's switch-to material."),
    "Steel 1018 HR (hot-rolled)": BracketMaterial(
        "Steel 1018 HR (hot-rolled)", yield_MPa=235.0, uts_MPa=400.0, rho_kg_m3=7870.0,
        weldable_no_pwht=True,
        weld_note="Hot-rolled 1018 has notably lower yield than cold-rolled — make "
                  "sure the SolidWorks material matches what you'll actually buy."),
    "Steel 4130 (normalized tube)": BracketMaterial(
        "Steel 4130 (normalized tube)", yield_MPa=460.0, uts_MPa=670.0, rho_kg_m3=7850.0,
        weldable_no_pwht=False,
        weld_note="Parent-tube yield. 4130 needs post-weld stress relief / "
                  "normalising to keep this strength near a weld — see the welded row."),
    "Steel 4130 (as-welded, no PWHT)": BracketMaterial(
        "Steel 4130 (as-welded, no PWHT)", yield_MPa=360.0, uts_MPa=560.0, rho_kg_m3=7850.0,
        weldable_no_pwht=False,
        weld_note="As-welded 4130 with NO post-weld heat treatment: the HAZ loses a "
                  "chunk of the parent yield. Use THIS row for a 4130 bracket you "
                  "won't heat-treat — it's close to 1018 CR, which is the brief's "
                  "whole argument for switching."),
    # Aluminium brackets (CNC / waterjet):
    "Aluminium 6061-T6": BracketMaterial(
        "Aluminium 6061-T6", yield_MPa=276.0, uts_MPa=310.0, rho_kg_m3=2700.0,
        weldable_no_pwht=False,
        weld_note="Welding 6061-T6 drops the HAZ toward T4/annealed (~the 6061-T4 "
                  "row) unless re-aged. Don't screen a welded 6061 bracket on T6."),
    "Aluminium 6061-T4 (welded HAZ)": BracketMaterial(
        "Aluminium 6061-T4 (welded HAZ)", yield_MPa=110.0, uts_MPa=240.0, rho_kg_m3=2700.0,
        weldable_no_pwht=True,
        weld_note="Conservative as-welded 6061 HAZ strength — use for a welded "
                  "aluminium bracket you won't artificially age."),
    "Aluminium 7075-T6": BracketMaterial(
        "Aluminium 7075-T6", yield_MPa=503.0, uts_MPa=572.0, rho_kg_m3=2810.0,
        weldable_no_pwht=False,
        weld_note="High strength but essentially NOT weldable for structure — bolt "
                  "it, don't weld it."),
}


# --------------------------------------------------------------------------- #
#  Bracket geometry + load
# --------------------------------------------------------------------------- #
@dataclass
class Bracket:
    """A simple plate / tab mounting bracket, the shape most FSAE mounts actually are.

    A rectangular plate cross-section of `width_mm` × `thickness_mm`, carrying a
    load `P_N` whose line of action sits `lever_arm_mm` from the bracket's built-in
    (welded) root — so the root sees a bending moment P·lever in addition to the
    direct load. The load is bolted through a single hole of diameter `hole_dia_mm`
    whose centre is `edge_dist_mm` from the nearest free edge (tear-out path).

    `weld_leg_mm` is the fillet-weld leg size attaching the bracket to the frame,
    and `weld_length_mm` the total effective weld length (sum of both sides of the
    foot, say). Leave weld fields 0 to skip the weld check (a bolted-only bracket).

    `load_is_shear` picks how the direct in-plane load is resolved: True = the load
    shears the cross-section (load in the plane of the plate), False = the load
    tensions it (load pulling along the plate). Bending from the lever arm is added
    in either case, since most tab loads are off the root.
    """
    name: str
    material: str
    width_mm: float
    thickness_mm: float
    P_N: float
    lever_arm_mm: float = 0.0
    hole_dia_mm: float = 0.0
    edge_dist_mm: float = 0.0
    weld_leg_mm: float = 0.0
    weld_length_mm: float = 0.0
    load_is_shear: bool = True
    n_bolts: int = 1
    is_estimate: bool = True
    set_by: str = ""
    notes: str = ""

    def as_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d) -> "Bracket":
        d = dict(d)
        valid = Bracket.__dataclass_fields__.keys()
        return Bracket(**{k: v for k, v in d.items() if k in valid})


@dataclass
class ModeResult:
    """One failure mode's stress and FoS on yield."""
    mode: str
    stress_MPa: float
    allow_MPa: float        # the yield (or shear-yield) this mode is checked against
    fos: float
    governs: bool = False

    def as_dict(self):
        return asdict(self)


@dataclass
class BracketResult:
    """Full screening outcome for one bracket against the FoS rule."""
    name: str
    material: str
    min_fos: float
    governing_mode: str
    verdict: str                       # PASS / TIGHT / FAIL / INVALID
    fos_target: float
    modes: list = field(default_factory=list)   # list[ModeResult]
    findings: list = field(default_factory=list)  # list[Finding]
    screening_only: bool = True
    notes: list = field(default_factory=list)

    def as_dict(self):
        d = asdict(self)
        d["modes"] = [m.as_dict() if isinstance(m, ModeResult) else m for m in self.modes]
        d["findings"] = [f.as_dict() if isinstance(f, Finding) else f for f in self.findings]
        return d


# --------------------------------------------------------------------------- #
#  The screening calculation
# --------------------------------------------------------------------------- #
def screen_bracket(br: Bracket, fos_target: float = 1.5,
                   tight_band: float = 0.15) -> BracketResult:
    """Screen one bracket against the FoS-on-yield rule (default 1.5).

    Computes the governing stress in each standard mounting-bracket failure mode,
    the FoS = allowable_yield / stress for each, and the single worst (minimum) FoS.
    Verdict:
        FAIL   min FoS < fos_target
        TIGHT  fos_target ≤ min FoS < fos_target·(1+tight_band)   (passes, no margin)
        PASS   min FoS ≥ fos_target·(1+tight_band)
        INVALID geometry that can't be screened (zero area, etc.)

    Every mode divides the YIELD strength (per the brief — not ultimate), and shear
    modes divide the *shear* yield so a shear-critical bracket isn't over-credited.
    Findings are the same typed objects the integration board renders, owned by
    "chassis".
    """
    mat = MATERIALS.get(br.material)
    notes: list = []
    if mat is None:
        return BracketResult(
            name=br.name, material=br.material, min_fos=0.0,
            governing_mode="material", verdict="INVALID", fos_target=fos_target,
            findings=[Finding("bracket-fos", Severity.MISSING,
                              f"Unknown material '{br.material}' — pick one from the "
                              f"bracket material library.", subsystems=["chassis"])],
            notes=["material not in library"])

    P = abs(float(br.P_N))
    w = float(br.width_mm)
    t = float(br.thickness_mm)
    Sy = mat.yield_MPa
    Ssy = mat.shear_yield_MPa

    if w <= 0 or t <= 0:
        return BracketResult(
            name=br.name, material=br.material, min_fos=0.0,
            governing_mode="geometry", verdict="INVALID", fos_target=fos_target,
            findings=[Finding("bracket-fos", Severity.MISSING,
                              "Bracket width and thickness must be > 0 to screen.",
                              subsystems=["chassis"])],
            notes=["non-physical cross-section"])

    area = w * t                       # gross cross-section, mm²
    modes: list = []

    # 1) Direct stress on the gross section -------------------------------------
    direct = P / area
    if br.load_is_shear:
        modes.append(ModeResult("direct shear (section)", direct, Ssy, Ssy / direct
                                if direct > 0 else math.inf))
    else:
        modes.append(ModeResult("direct tension (section)", direct, Sy, Sy / direct
                                if direct > 0 else math.inf))

    # 2) Bending of the cantilever foot, σ = M·c / I ----------------------------
    #    Plate bending about its strong axis: I = w·t³/12 if the load bends it
    #    across the thickness (typical thin tab), c = t/2.
    if br.lever_arm_mm and br.lever_arm_mm > 0:
        M = P * float(br.lever_arm_mm)         # N·mm
        I = w * t**3 / 12.0                     # mm⁴
        c = t / 2.0
        bend = M * c / I if I > 0 else math.inf
        modes.append(ModeResult("root bending", bend, Sy,
                                Sy / bend if bend > 0 else math.inf))

    # 3) Bolt-hole bearing, P / (d·t) -------------------------------------------
    if br.hole_dia_mm and br.hole_dia_mm > 0:
        nb = max(int(br.n_bolts), 1)
        bearing = (P / nb) / (br.hole_dia_mm * t)
        # bearing is a compressive crushing check vs yield (ductile bearing allow
        # is often >1×Sy, but screening on Sy is the conservative, defensible call)
        modes.append(ModeResult("bolt-hole bearing", bearing, Sy,
                                Sy / bearing if bearing > 0 else math.inf))

    # 4) Tear-out / shear-out between hole and free edge ------------------------
    #    Two shear planes of length edge_dist through thickness t carry the load.
    if br.hole_dia_mm and br.hole_dia_mm > 0 and br.edge_dist_mm and br.edge_dist_mm > 0:
        nb = max(int(br.n_bolts), 1)
        shear_area = 2.0 * br.edge_dist_mm * t      # two planes
        tear = (P / nb) / shear_area
        modes.append(ModeResult("hole tear-out (shear)", tear, Ssy,
                                Ssy / tear if tear > 0 else math.inf))

    # 5) Fillet-weld throat shear (the "design for welding" check) --------------
    if br.weld_leg_mm and br.weld_leg_mm > 0 and br.weld_length_mm and br.weld_length_mm > 0:
        throat = 0.707 * br.weld_leg_mm
        weld_area = throat * br.weld_length_mm
        # direct throat shear from the load; add the bending couple as an extra
        # throat shear over the weld group's length (screening-level superposition)
        weld_shear = P / weld_area if weld_area > 0 else math.inf
        if br.lever_arm_mm and br.lever_arm_mm > 0 and br.weld_length_mm > 0:
            # crude couple → extra line shear = 6·M / (throat·L²) for a line group
            weld_bend = 6.0 * (P * br.lever_arm_mm) / (throat * br.weld_length_mm**2)
            weld_shear = math.hypot(weld_shear, weld_bend)
        # weld metal checked on the (shear) yield of the WEAKER of parent / a
        # generic matching filler ~ parent; conservative: use parent shear yield.
        modes.append(ModeResult("weld throat shear", weld_shear, Ssy,
                                Ssy / weld_shear if weld_shear > 0 else math.inf))

    # ---- governing mode + verdict --------------------------------------------
    if not modes:
        return BracketResult(
            name=br.name, material=br.material, min_fos=0.0,
            governing_mode="none", verdict="INVALID", fos_target=fos_target,
            findings=[Finding("bracket-fos", Severity.MISSING,
                              "Nothing to check — give at least a load and a section.",
                              subsystems=["chassis"])])

    gov = min(modes, key=lambda m: m.fos)
    gov.governs = True
    min_fos = gov.fos

    tight_ceiling = fos_target * (1.0 + tight_band)
    if min_fos < fos_target:
        verdict = "FAIL"
    elif min_fos < tight_ceiling:
        verdict = "TIGHT"
    else:
        verdict = "PASS"

    # ---- findings (owned by chassis, board-renderable) -----------------------
    findings: list = []
    est_tag = " (estimated geometry/load)" if br.is_estimate else ""
    govlabel = gov.mode
    if verdict == "FAIL":
        findings.append(Finding(
            "bracket-fos", Severity.FAIL,
            f"chassis bracket '{br.name}' FAILS the FoS rule: governing mode is "
            f"{govlabel} at FoS {min_fos:.2f} vs required {fos_target:.2f} "
            f"({mat.name}){est_tag}. Thicken/widen, shorten the lever, add weld/bolt "
            f"area, or move to a stronger material before fab.",
            subsystems=["chassis"],
            detail=dict(bracket=br.name, material=mat.name, governing_mode=govlabel,
                        fos=min_fos, fos_target=fos_target,
                        stress_MPa=gov.stress_MPa, allow_MPa=gov.allow_MPa,
                        estimate=br.is_estimate)))
    elif verdict == "TIGHT":
        findings.append(Finding(
            "bracket-fos", Severity.WARN,
            f"chassis bracket '{br.name}' passes but is TIGHT: {govlabel} at FoS "
            f"{min_fos:.2f}, just over the {fos_target:.2f} floor ({mat.name})"
            f"{est_tag}. No margin for the rushed-manufacturing variation the FoS "
            f"buffer is meant to absorb — worth one more pass or an FEA confirm.",
            subsystems=["chassis"],
            detail=dict(bracket=br.name, material=mat.name, governing_mode=govlabel,
                        fos=min_fos, fos_target=fos_target, estimate=br.is_estimate)))
    else:
        findings.append(Finding(
            "bracket-fos", Severity.OK,
            f"chassis bracket '{br.name}' meets FoS ≥ {fos_target:.2f} on yield "
            f"(governing {govlabel}, FoS {min_fos:.2f}, {mat.name}). Screening only — "
            f"confirm with SolidWorks/FEA before sign-off.",
            subsystems=["chassis"],
            detail=dict(bracket=br.name, material=mat.name, governing_mode=govlabel,
                        fos=min_fos, fos_target=fos_target)))

    # material/weld provenance notes the brief cares about
    if mat.weld_note and (br.weld_leg_mm or "4130" in mat.name or "6061-T6" in mat.name):
        notes.append(mat.weld_note)
    if "4130 (normalized" in mat.name and br.weld_leg_mm:
        notes.append("You're screening a WELDED 4130 bracket on parent-tube yield. "
                     "If you won't post-weld heat-treat, re-run on '4130 (as-welded, "
                     "no PWHT)' — that's the apples-to-apples vs 1018 CR.")

    return BracketResult(
        name=br.name, material=mat.name, min_fos=min_fos,
        governing_mode=govlabel, verdict=verdict, fos_target=fos_target,
        modes=modes, findings=findings, screening_only=True, notes=notes)


# --------------------------------------------------------------------------- #
#  Material trade helper — the 1018 vs 4130 question, in numbers
# --------------------------------------------------------------------------- #
def compare_materials(br: Bracket, materials: Optional[list] = None,
                      fos_target: float = 1.5) -> list:
    """Screen the SAME bracket across several materials so the 4130→1018 decision
    in the brief is a table of FoS numbers, not a hand-wave. Returns a list of
    dicts (material, min_fos, governing_mode, verdict, mass-proxy), sorted by FoS.

    The 'mass-proxy' is just the material density — the bracket geometry is fixed
    across the comparison, so density is the only thing moving the part mass, and
    it lets you see the strength-per-gram trade (the brief notes 1018 and 4130 are
    nearly the same density, so for equal geometry it's almost purely a yield and
    cost/weldability call)."""
    if materials is None:
        materials = list(MATERIALS.keys())
    rows = []
    for m in materials:
        if m not in MATERIALS:
            continue
        trial = Bracket.from_dict({**br.as_dict(), "material": m})
        r = screen_bracket(trial, fos_target=fos_target)
        rows.append({
            "material": MATERIALS[m].name,
            "yield_MPa": MATERIALS[m].yield_MPa,
            "min_fos": r.min_fos,
            "governing_mode": r.governing_mode,
            "verdict": r.verdict,
            "density_kg_m3": MATERIALS[m].rho_kg_m3,
            "weldable_no_pwht": MATERIALS[m].weldable_no_pwht,
        })
    rows.sort(key=lambda d: d["min_fos"], reverse=True)
    return rows


def bracket_report(res: BracketResult) -> str:
    """A plain-text screening report the structures member can paste into a design
    review or the handover log — every mode, its stress, and its FoS, with the
    governing one marked, plus the honest screening-only caveat."""
    lines = [f"KinematiK bracket FoS screening — {res.name}",
             f"material: {res.material}   target FoS (yield): {res.fos_target:.2f}",
             f"VERDICT: {res.verdict}   min FoS: {res.min_fos:.2f} "
             f"(governing: {res.governing_mode})", ""]
    lines.append("mode,stress_MPa,allowable_MPa,FoS,governs")
    for m in res.modes:
        mm = m if isinstance(m, ModeResult) else ModeResult(**m)
        lines.append(f"{mm.mode},{mm.stress_MPa:.1f},{mm.allow_MPa:.1f},"
                     f"{mm.fos:.2f},{'<<' if mm.governs else ''}")
    if res.notes:
        lines += ["", "notes:"]
        lines += [f"  - {n}" for n in res.notes]
    lines += ["",
              "Screening only: standard closed-form hand checks (Shigley / AWS "
              "throat shear). NOT a substitute for the SolidWorks/Ansys FEA the "
              "design rule also requires — confirm the governing mode there before "
              "sign-off."]
    return "\n".join(lines)
