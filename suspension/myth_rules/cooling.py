# ============================================================================
#  KinematiK — Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
# ============================================================================
"""Cooling myth rules. Context (optional): dict with pack/PCM/fan numbers.
Ties to pack_thermal / pcm_cooling where sizing is asked."""
from __future__ import annotations
from typing import Any, Optional
from ..mythbuster import CheckOutcome, ParsedClaim, Rule, Verdict, register


# Bigger radiator/fan always cools better
def _r_bigger_rad(claim: ParsedClaim, context: Any) -> Optional[CheckOutcome]:
    if not (claim.has("bigger radiator", "larger radiator", "bigger fan",
                      "more airflow", "bigger rad") and
            claim.has("cooler", "better cooling", "always", "colder", "more cooling")):
        return None
    return CheckOutcome(
        Verdict.DEPENDS,
        ("More cooling capacity helps only up to where another resistance dominates. "
         "Heat rejection is limited by the WHOLE path \u2014 coolant flow, fin area, "
         "AND the air actually reaching the core through the duct. A bigger radiator "
         "behind a choked inlet, or a fan fighting back-pressure, adds mass and drag "
         "for little gain. Size it against the real heat load (motor + pack) and the "
         "duct's achievable airflow, which the pack-thermal / cooling model captures. "
         "Past the point where the duct or flow caps you, bigger does nothing."),
        provenance="rejection limited by full path; needs duct airflow + heat load")
_r_bigger_rad.reference_claim = "A bigger radiator always cools better."


# PCM / wax replaces the fan
def _r_pcm_replaces_fan(claim: ParsedClaim, context: Any) -> Optional[CheckOutcome]:
    if not (claim.has("pcm", "phase change", "wax", "phase-change") and
            claim.has("no fan", "replace the fan", "instead of a fan", "no cooling",
                      "don't need", "without a fan")):
        return None
    return CheckOutcome(
        Verdict.DEPENDS,
        ("Phase-change material BUFFERS heat, it doesn't REJECT it. The wax absorbs "
         "a burst of heat at its melt plateau \u2014 great for a short, high-current "
         "stint \u2014 but once fully melted it's saturated, and between runs it must "
         "re-freeze, which still needs a heat path out. Whether it replaces the fan "
         "for YOUR endurance length is exactly the sizing question pcm_cooling "
         "answers: enough latent heat to hold the cells for the stint, or do you "
         "still need active airflow? Run size_pcm_for_hold with your lap/current "
         "trace before betting on wax alone."),
        provenance="PCM = latent buffer, not a heat sink; needs pcm_cooling sizing")
_r_pcm_replaces_fan.reference_claim = "Phase-change wax means we don't need a cooling fan."


# Cells all cook evenly
def _r_even_cells(claim: ParsedClaim, context: Any) -> Optional[CheckOutcome]:
    if not (claim.has("cell", "cells", "pack") and
            claim.has("even", "uniform", "same temp", "all the same", "evenly")):
        return None
    return CheckOutcome(
        Verdict.MYTH,
        ("Cells do NOT heat evenly. The cells in the middle of the pack and furthest "
         "from airflow run hottest \u2014 the hot-spot can be 10\u201320\u00b0C above "
         "the coolest cell, and it's the HOTTEST cell that limits the whole pack and "
         "ages fastest. That's why fan placement matters and why a single average "
         "temperature hides the problem. Use the per-cell pack-thermal map to find "
         "which cell cooks first and put the airflow there."),
        provenance="lumped per-cell network \u2192 non-uniform; hottest cell governs")
_r_even_cells.reference_claim = "All the cells in the pack heat up evenly."


RULES = [
    Rule("cooling.bigger_rad", "cooling", _r_bigger_rad,
         keywords_any=("bigger radiator", "larger radiator", "bigger fan",
                       "more airflow", "bigger rad"), priority=10),
    Rule("cooling.pcm_replaces_fan", "cooling", _r_pcm_replaces_fan,
         keywords_any=("pcm", "phase change", "phase-change", "wax"), priority=20),
    Rule("cooling.even_cells", "cooling", _r_even_cells,
         keywords_any=("cell", "cells", "pack"), priority=30),
]
for _rule in RULES:
    register(_rule)
