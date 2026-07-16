# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================

"""
Phase-change-material (PCM) cooling buffer for the battery pack — the latent-heat
"liquid wax inside the cell holder" the cooling team put on the slides, and the
one effect `pack_thermal.py` cannot represent on its own.

WHY THIS MODULE EXISTS
----------------------
The cooling slide is explicit: a nylon-filament custom battery holder, "Inside:
PCM Liquid Wax", 140s3p = 420 cells, with "modeling temperature on ANSYS" as the
task. PCM (a paraffin/wax that melts around the cell's danger temperature)
absorbs a large slug of heat AT NEARLY CONSTANT TEMPERATURE while it melts — its
latent heat of fusion. That is the entire point of putting wax around cells: it
flattens the temperature spike on the hard corner-exit bursts and buys minutes
before the cells climb toward their limit.

`pack_thermal.py` models every cell as a sensible-heat lump: C=m·cp, temperature
rises smoothly with absorbed heat. It has NO latent term, so it literally cannot
show the flat plateau that is the reason the wax is there. A student asking "is
my wax enough for the endurance run, or do I still need the fan?" gets no answer
from the bare model. This module adds exactly that latent buffer and the one
sizing number the cooling team actually needs — and nothing it can't defend.

WHAT IT DOES
------------
  1. PCM MATERIAL — a small material record (melt temperature, latent heat of
     fusion, solid/liquid cp, density) with representative paraffin-wax defaults,
     flagged UNCALIBRATED exactly like CellParams until the team enters their
     actual wax's datasheet numbers.
  2. ENTHALPY BUFFER — an effective-heat-capacity / enthalpy-method wrapper: in a
     melt window around T_melt the material's *effective* cp is inflated by the
     latent heat spread over that window, so a standard sensible-heat integrator
     (the one pack_thermal already runs) reproduces the melt plateau without a
     moving-boundary Stefan solver. This is the standard, defensible apparent-heat
     -capacity method — labelled as such, not sold as a sharp-interface solve.
  3. MELT-FRACTION TRACK — given the heat a cell absorbs over the run, how much of
     its wax has melted, and (the headline) at what point is the wax FULLY MELTED,
     after which the plateau is gone and temperature climbs again. That instant is
     the honest "the wax has run out — now you need airflow" line.
  4. SIZING — the inverse question the slide implies: for a target hold time (the
     endurance stint) and the pack's heat-generation rate, how many grams of PCM
     per cell are needed so the wax does not fully melt before the stint ends.

HONEST SCOPE (same contract as pack_thermal / tire_thermal)
-----------------------------------------------------------
- The apparent-heat-capacity method smears the latent heat over a finite melt
  window (real waxes melt over a few °C anyway, so this is physical, not a fudge);
  it is NOT a sharp Stefan moving-boundary solve and does not model PCM natural
  convection in the liquid, supercooling on re-freeze, or wax leakage. Those need
  the ANSYS model the slide names; this is the planning-grade buffer that tells
  you whether that detailed model is even worth running and how much wax to draw.
- Absolute temperatures inherit pack_thermal's calibration contract: uncalibrated
  cell OR uncalibrated PCM ⇒ every output is flagged `synthesized`. The module is
  trustworthy for RANKING (more wax vs more fan, where the plateau ends) before
  any rig data, which is exactly the decision the cooling team faces now.
- Never raises; returns a typed result with `.warnings` and emits the same
  `Finding` objects the integration board renders.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Optional

import numpy as np

from .interfaces import Finding, Severity
from .pack_thermal import CellParams, PackLayout, PackThermalResult


# --------------------------------------------------------------------------- #
#  PCM material
# --------------------------------------------------------------------------- #
@dataclass
class PCMMaterial:
    """
    A phase-change material (paraffin/wax) record. Defaults are REPRESENTATIVE of
    a battery-grade paraffin chosen to melt just below the cell warn temperature
    — NOT measured on any specific wax. Enter the datasheet numbers and set
    `calibrated=True` to make the temperatures quotable.

        t_melt_c        : nominal melt temperature (centre of the window).
        melt_window_c   : full width of the melt range (real waxes melt over a
                          few °C); the latent heat is spread across this.
        latent_heat_j_per_g : enthalpy of fusion — the big slug of heat absorbed
                          at ~constant T. The whole reason for the wax.
        cp_solid_j_per_gk / cp_liquid_j_per_gk : sensible heat capacities each
                          side of the melt.
        density_g_per_cc : to convert a chosen mass to the volume that has to fit
                          in the holder.
    """
    t_melt_c: float = 45.0
    melt_window_c: float = 4.0
    latent_heat_j_per_g: float = 200.0     # typical paraffin ~180–230 J/g
    cp_solid_j_per_gk: float = 2.0
    cp_liquid_j_per_gk: float = 2.2
    density_g_per_cc: float = 0.90
    calibrated: bool = False
    fitted_to: str = ""

    def latent_heat_j_per_kg(self) -> float:
        return self.latent_heat_j_per_g * 1000.0

    def effective_cp_j_per_gk(self, t_c: float) -> float:
        """
        Apparent (effective) specific heat at temperature t_c. Inside the melt
        window the latent heat is added as if it were sensible heat, spread
        uniformly across the window width: cp_eff = cp_sensible + L / window.
        Outside the window it is the solid/liquid sensible cp. This is the
        standard apparent-heat-capacity (enthalpy) method.
        """
        lo = self.t_melt_c - 0.5 * self.melt_window_c
        hi = self.t_melt_c + 0.5 * self.melt_window_c
        w = max(self.melt_window_c, 1e-6)
        if t_c < lo:
            return self.cp_solid_j_per_gk
        if t_c > hi:
            return self.cp_liquid_j_per_gk
        cp_sens = 0.5 * (self.cp_solid_j_per_gk + self.cp_liquid_j_per_gk)
        return cp_sens + self.latent_heat_j_per_g / w

    def melt_fraction_at(self, t_c: float) -> float:
        """Fraction of the wax melted at temperature t_c (0 below window, 1 above)."""
        lo = self.t_melt_c - 0.5 * self.melt_window_c
        hi = self.t_melt_c + 0.5 * self.melt_window_c
        if t_c <= lo:
            return 0.0
        if t_c >= hi:
            return 1.0
        return (t_c - lo) / max(hi - lo, 1e-9)

    def as_dict(self):
        return asdict(self)


def default_pcm() -> PCMMaterial:
    """Representative, UNCALIBRATED battery-grade paraffin (calibrated=False)."""
    return PCMMaterial()


# --------------------------------------------------------------------------- #
#  Per-cell PCM allocation
# --------------------------------------------------------------------------- #
@dataclass
class PCMAllocation:
    """
    How much PCM surrounds each cell. `mass_per_cell_g` is the design variable the
    cooling team is choosing. The latent buffer per cell is mass·L.
    """
    material: PCMMaterial
    mass_per_cell_g: float = 15.0
    set_by: str = ""
    is_estimate: bool = True

    def latent_buffer_j_per_cell(self) -> float:
        """Total latent heat one cell's wax can absorb while melting (J)."""
        return self.mass_per_cell_g * self.material.latent_heat_j_per_g

    def volume_per_cell_cc(self) -> float:
        return self.mass_per_cell_g / max(self.material.density_g_per_cc, 1e-9)

    def as_dict(self):
        d = asdict(self)
        d["material"] = self.material.as_dict()
        d["latent_buffer_j_per_cell"] = self.latent_buffer_j_per_cell()
        return d


# --------------------------------------------------------------------------- #
#  Result of a PCM-buffered run / sizing
# --------------------------------------------------------------------------- #
@dataclass
class PCMResult:
    ok: bool
    # headline numbers
    hold_time_s: Optional[float]            # time until the wax is fully melted
                                            # (None ⇒ never melts out in the run)
    peak_cell_c_with_pcm: Optional[float]
    peak_cell_c_without_pcm: Optional[float]
    plateau_temp_c: float                   # ~T_melt, where the buffer holds
    fully_melted: bool
    latent_buffer_j_per_cell: float
    total_pcm_mass_kg: float
    total_pcm_volume_cc: float
    synthesized: bool = True
    provenance: str = ""
    warnings: list = field(default_factory=list)

    @staticmethod
    def failed(warnings: list) -> "PCMResult":
        return PCMResult(
            ok=False, hold_time_s=None, peak_cell_c_with_pcm=None,
            peak_cell_c_without_pcm=None, plateau_temp_c=float("nan"),
            fully_melted=False, latent_buffer_j_per_cell=0.0,
            total_pcm_mass_kg=0.0, total_pcm_volume_cc=0.0,
            synthesized=True, provenance="run failed", warnings=warnings)


def _provenance(cell_cal: bool, pcm_cal: bool) -> str:
    if cell_cal and pcm_cal:
        return "calibrated: cell + PCM parameters from rig/datasheet data"
    miss = []
    if not cell_cal:
        miss.append("cell")
    if not pcm_cal:
        miss.append("PCM")
    return (f"SYNTHESIZED — {', '.join(miss)} uncalibrated; use for ranking "
            f"layouts and finding where the wax runs out, not absolute °C")


def evaluate_pcm_buffer(result: PackThermalResult,
                        layout: PackLayout,
                        alloc: PCMAllocation,
                        material: Optional[PCMMaterial] = None) -> PCMResult:
    """
    Wrap a completed bare-cell `PackThermalResult` (no PCM) with the latent buffer
    and report the headline cooling-team numbers WITHOUT re-running the heavy
    solver: it reads the heat the hottest cell absorbed over the run and asks how
    far the declared wax pushes back the climb.

    Method: above the melt onset the bare model's heat that WOULD have raised the
    cell by sensible cp is instead spent melting wax until the latent buffer is
    exhausted; the cell is held near T_melt for that hold time, then resumes the
    bare trajectory. This is a post-process energy accounting on the seam
    pack_thermal already exposes (`temp_history_c`, `pack_current_a`), in the same
    spirit as how pack_thermal wraps the EV lap sim — it never reaches into the
    integrator.
    """
    mat = material or alloc.material
    warns: list[str] = []
    try:
        if not result.ok:
            return PCMResult.failed(["bare pack-thermal result was not ok"])

        cell = layout.cell
        hottest = int(result.hottest_cell_index)
        T_hist = np.asarray(result.temp_history_c, float)[:, hottest]
        t = np.asarray(result.time_s, float)
        if T_hist.size < 2:
            return PCMResult.failed(["temperature history too short"])

        peak_no_pcm = float(np.max(T_hist))
        C_cell = max(cell.mass_kg * cell.cp, 1e-9)         # J/K sensible
        buffer_j = alloc.latent_buffer_j_per_cell()        # J latent available

        # Heat the hottest cell accumulated ABOVE the melt onset in the bare run,
        # reconstructed from its sensible temperature rise above T_melt.
        onset = mat.t_melt_c - 0.5 * mat.melt_window_c
        # incremental sensible heat per step that took the cell above onset:
        dT = np.diff(T_hist)
        T_mid = 0.5 * (T_hist[1:] + T_hist[:-1])
        heating = np.clip(dT, 0.0, None)                    # only rising portions
        above = T_mid >= onset
        q_above = C_cell * heating * above                  # J per step above onset
        cum_q = np.cumsum(q_above)

        plateau = mat.t_melt_c
        total_above = float(cum_q[-1]) if cum_q.size else 0.0

        if buffer_j <= 0.0:
            warns.append("PCM mass is zero — no latent buffer; equals bare cell.")
            hold_time = 0.0
            fully = True
            peak_with = peak_no_pcm
        elif total_above <= buffer_j:
            # the wax never fully melts during the run — it holds the whole stint
            hold_time = None
            fully = False
            # with the buffer, the cell is pinned near the plateau once it reaches
            # onset; peak is ~the top of the melt window (it never melts out)
            reached_onset = np.any(T_hist >= onset)
            peak_with = (min(peak_no_pcm, mat.t_melt_c + 0.5 * mat.melt_window_c)
                         if reached_onset else peak_no_pcm)
        else:
            # find the step where cumulative heat above onset exhausts the buffer
            idx = int(np.searchsorted(cum_q, buffer_j))
            idx = min(max(idx, 0), t.size - 2)
            # hold time = time from first reaching onset to buffer exhaustion
            first_onset_i = int(np.argmax(T_hist >= onset))
            hold_time = float(t[idx + 1] - t[first_onset_i])
            fully = True
            peak_with = peak_no_pcm   # after melt-out it resumes the bare climb
            warns.append(
                f"Wax fully melts {hold_time:.1f}s into the high-load phase — "
                f"after that the plateau is gone and cells resume climbing. "
                f"Either add wax or bring airflow in before then.")

        synth = not (cell.calibrated and mat.calibrated)
        n = layout.n_cells
        total_mass_kg = alloc.mass_per_cell_g * n / 1000.0
        total_vol_cc = alloc.volume_per_cell_cc() * n

        return PCMResult(
            ok=True,
            hold_time_s=hold_time,
            peak_cell_c_with_pcm=peak_with,
            peak_cell_c_without_pcm=peak_no_pcm,
            plateau_temp_c=plateau,
            fully_melted=fully,
            latent_buffer_j_per_cell=buffer_j,
            total_pcm_mass_kg=total_mass_kg,
            total_pcm_volume_cc=total_vol_cc,
            synthesized=synth,
            provenance=_provenance(cell.calibrated, mat.calibrated),
            warnings=warns,
        )
    except Exception as exc:
        return PCMResult.failed(warns + [f"PCM evaluation crashed: {exc!r}"])


def size_pcm_for_hold(result: PackThermalResult,
                      layout: PackLayout,
                      material: PCMMaterial,
                      hold_time_s: float) -> dict:
    """
    The inverse, sizing question the slide implies: how many grams of wax per cell
    keep the hottest cell from melting out its PCM for `hold_time_s` of the run?

    Reads the hottest cell's heat-above-onset rate from the bare result and
    returns the required latent buffer and the corresponding wax mass/volume per
    cell and for the whole 140s3p pack. Never raises.
    """
    out = {"ok": False, "warnings": []}
    try:
        if not result.ok:
            out["warnings"].append("bare pack-thermal result was not ok")
            return out
        cell = layout.cell
        hottest = int(result.hottest_cell_index)
        T_hist = np.asarray(result.temp_history_c, float)[:, hottest]
        t = np.asarray(result.time_s, float)
        onset = material.t_melt_c - 0.5 * material.melt_window_c
        C_cell = max(cell.mass_kg * cell.cp, 1e-9)

        dT = np.diff(T_hist)
        T_mid = 0.5 * (T_hist[1:] + T_hist[:-1])
        q_above = C_cell * np.clip(dT, 0.0, None) * (T_mid >= onset)
        cum_q = np.cumsum(q_above)
        # heat absorbed above onset within the requested hold window
        first_onset_i = int(np.argmax(T_hist >= onset)) if np.any(T_hist >= onset) else 0
        t_target = t[first_onset_i] + hold_time_s
        end_i = int(np.searchsorted(t, t_target))
        end_i = min(max(end_i, first_onset_i), cum_q.size - 1)
        q_needed = float(cum_q[end_i] - (cum_q[first_onset_i - 1] if first_onset_i > 0 else 0.0))
        q_needed = max(q_needed, 0.0)

        mass_g = q_needed / max(material.latent_heat_j_per_g, 1e-9)
        n = layout.n_cells
        out.update({
            "ok": True,
            "required_buffer_j_per_cell": q_needed,
            "pcm_mass_per_cell_g": mass_g,
            "pcm_volume_per_cell_cc": mass_g / max(material.density_g_per_cc, 1e-9),
            "pack_pcm_mass_kg": mass_g * n / 1000.0,
            "pack_pcm_volume_cc": (mass_g / max(material.density_g_per_cc, 1e-9)) * n,
            "n_cells": n,
            "synthesized": not (cell.calibrated and material.calibrated),
            "provenance": _provenance(cell.calibrated, material.calibrated),
        })
        return out
    except Exception as exc:
        out["warnings"].append(f"PCM sizing crashed: {exc!r}")
        return out


def check_pcm(res: PCMResult,
              endurance_time_s: float = 1500.0) -> list:
    """
    Turn a PCM result into typed Findings for the integration board. The cooling
    team's headline question — does the wax hold for the stint — answered as a
    gate. `endurance_time_s` defaults to a ~25-minute FSAE endurance stint.
    """
    out: list[Finding] = []
    subs = ["cooling", "battery-pack"]
    if not res.ok:
        out.append(Finding("pcm", Severity.MISSING,
                           "PCM evaluation did not complete; "
                           + "; ".join(res.warnings),
                           subsystems=subs))
        return out

    flag = " (SYNTHESIZED — uncalibrated)" if res.synthesized else ""
    if not res.fully_melted:
        out.append(Finding(
            "pcm-hold", Severity.OK,
            f"The wax never fully melts over the run — it holds the hottest cell "
            f"near {res.plateau_temp_c:.0f}°C for the whole stint{flag}. "
            f"({res.total_pcm_mass_kg:.2f}kg / {res.total_pcm_volume_cc:.0f}cc "
            f"of PCM in the pack.)",
            subsystems=subs,
            detail={"plateau_c": res.plateau_temp_c,
                    "pack_pcm_kg": res.total_pcm_mass_kg}))
    elif res.hold_time_s is not None and res.hold_time_s >= endurance_time_s:
        out.append(Finding(
            "pcm-hold", Severity.OK,
            f"Wax holds for {res.hold_time_s:.0f}s ≥ the {endurance_time_s:.0f}s "
            f"stint before melting out{flag}.",
            subsystems=subs, detail={"hold_time_s": res.hold_time_s}))
    else:
        ht = res.hold_time_s if res.hold_time_s is not None else 0.0
        out.append(Finding(
            "pcm-hold", Severity.WARN,
            f"Wax fully melts {ht:.0f}s into the high-load phase — short of the "
            f"{endurance_time_s:.0f}s stint. After melt-out the hottest cell "
            f"resumes climbing toward {res.peak_cell_c_without_pcm:.0f}°C{flag}. "
            f"Add wax or bring the fan in before melt-out.",
            subsystems=subs,
            detail={"hold_time_s": ht,
                    "peak_after_meltout_c": res.peak_cell_c_without_pcm}))

    # volume reality check — wax has to fit in the holder
    if res.total_pcm_volume_cc > 0:
        out.append(Finding(
            "pcm-packaging", Severity.INFO,
            f"PCM volume to package: {res.total_pcm_volume_cc:.0f}cc "
            f"({res.total_pcm_mass_kg:.2f}kg) across the pack — check it fits the "
            f"nylon holder and counts against the mass budget.",
            subsystems=subs))
    return out
