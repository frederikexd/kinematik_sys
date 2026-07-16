# ============================================================================
#  KinematiK — Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
# ============================================================================
"""Electrics myth rules (HV pack, wiring, tractive-system safety). Checks
against gas-law/Ohm/IPC physics; ties to tractive_system, electronics, harness."""
from __future__ import annotations
import math
from typing import Any, Optional
from ..mythbuster import CheckOutcome, ParsedClaim, Rule, Verdict, register


# Higher voltage = more power (at fixed cap)
def _r_voltage_power(claim: ParsedClaim, context: Any) -> Optional[CheckOutcome]:
    if not (claim.has("voltage", "higher voltage", "more volts", "raise voltage") and
            claim.has("more power", "faster", "more kw", "higher power")):
        return None
    return CheckOutcome(
        Verdict.MYTH,
        ("At a fixed FSAE 80 kW tractive cap, higher pack voltage does NOT give more "
         "power \u2014 P = VI is capped either way. What higher voltage buys is LOWER "
         "CURRENT for the same power (I = P/V), which means thinner cables, less "
         "I\u00b2R loss and heating, smaller contactors, and staying under the motor/"
         "inverter current limit. Choose voltage for current/efficiency and packaging "
         "within the rules, not to chase power you're not allowed to use."),
        provenance="P=VI capped at 80 kW; higher V \u2192 lower I, less I\u00b2R loss")
_r_voltage_power.reference_claim = "A higher-voltage pack gives us more power."


# Thicker wire is always safer
def _r_thicker_wire(claim: ParsedClaim, context: Any) -> Optional[CheckOutcome]:
    if not (claim.has("thicker wire", "bigger wire", "thicker gauge", "bigger gauge",
                      "heavier gauge", "thicker cable") and
            claim.has("safer", "always", "better", "cooler", "less resistance")):
        return None
    return CheckOutcome(
        Verdict.DEPENDS,
        ("Thicker wire lowers resistance, heating and voltage drop \u2014 good \u2014 "
         "but only to the point the AMPACITY covers your worst-case continuous "
         "current with margin. Past that it's wasted copper mass, harder bend radii, "
         "and connector strain, while the real failure points are usually crimps, "
         "connectors and bundle derating, not the wire cross-section. Size each run "
         "from its actual current (IPC-2221 / ampacity) with the harness model, then "
         "stop. Bigger isn't safer once you've cleared the current with margin."),
        provenance="ampacity sizing per run; needs harness current map")
_r_thicker_wire.reference_claim = "A thicker wire is always safer."


# Precharge: just close the contactor
def _r_precharge(claim: ParsedClaim, context: Any) -> Optional[CheckOutcome]:
    if not (claim.has("precharge", "pre-charge", "contactor", "close the contactor") and
            claim.has("not needed", "skip", "just close", "don't need", "unnecessary",
                      "instant")):
        return None
    return CheckOutcome(
        Verdict.MYTH,
        ("Skipping precharge can weld the contactor and is a tech-inspection fail. "
         "The inverter's DC-link capacitance looks like a near-short at the instant "
         "of connection; closing the main contactor straight onto the pack drives a "
         "huge inrush (I = C\u00b7dV/dt) that pits or welds the contacts. The precharge "
         "resistor limits that inrush until the link is charged (the 'R-C on a DC "
         "source, switch shorts R after ~2s' experiment). It's mandatory and rules-"
         "checked \u2014 validate it with the tractive-system precharge model."),
        provenance="capacitor inrush I=C\u00b7dV/dt; precharge required + rules-checked")
_r_precharge.reference_claim = "We can just close the main contactor without precharge."


RULES = [
    Rule("elec.voltage_power", "electrics", _r_voltage_power,
         keywords_any=("voltage", "higher voltage", "more volts", "raise voltage"),
         priority=10),
    Rule("elec.thicker_wire", "electrics", _r_thicker_wire,
         keywords_any=("thicker wire", "bigger wire", "thicker gauge", "bigger gauge",
                       "heavier gauge", "thicker cable"), priority=20),
    Rule("elec.precharge", "electrics", _r_precharge,
         keywords_any=("precharge", "pre-charge", "contactor"), priority=30),
]
for _rule in RULES:
    register(_rule)
