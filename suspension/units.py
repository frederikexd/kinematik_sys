# ============================================================================
#  KinematiK — unit-system helper
#  Lets the UI display values in either Metric (SI, the model's native units)
#  or US / Imperial, without changing any internal computation. The whole
#  physics core stays metric; only the *presentation* layer and a handful of
#  user-facing input bounds are converted here.
# ============================================================================

"""Unit-system conversion utilities.

The KinematiK solver works entirely in metric units (mm, kg, m/s, N, N·m,
N/mm, bar, °C, …). To support a US/Imperial display mode we convert only at
the edges:

* ``display(value, unit)`` — convert a metric *number* to the active system,
  returning ``(converted_value, label)``.
* ``label(unit)`` — the unit label in the active system.
* ``to_metric(value, unit)`` / ``from_metric(value, unit)`` — convert a single
  scalar between the active system and the metric core. Used for input widgets
  (sliders / number_inputs) whose bounds and stored value must round-trip.

Angles (``°``), percentages (``%``), accelerations in ``g``, time in ``s`` and
dimensionless ratios are identical in both systems and pass through unchanged.

The active system is read from Streamlit ``session_state['unit_system']`` and
falls back to ``"metric"`` so the module is import-safe outside Streamlit.
"""

from __future__ import annotations

import re
from typing import Tuple

try:  # Streamlit is the runtime, but keep the module usable without it.
    import streamlit as st
except Exception:  # pragma: no cover
    st = None

METRIC = "metric"
US = "us"

# Conversion table keyed by the metric unit string used throughout the app.
# Each entry: metric_unit -> (us_label, metric->us factor, metric->us offset)
# value_us = value_metric * factor + offset
_CONVERSIONS = {
    "mm":      ("in",      1.0 / 25.4,        0.0),
    "cm":      ("in",      1.0 / 2.54,        0.0),
    "m":       ("ft",      3.280839895,       0.0),
    "kg":      ("lb",      2.2046226218,      0.0),
    "g_mass":  ("oz",      0.0352739619,      0.0),     # small mass (grams)
    "g":       ("g", 1.0, 0.0),                     # acceleration g — unchanged
    "m/s":     ("mph",     2.2369362921,      0.0),
    "km/h":    ("mph",     0.6213711922,      0.0),
    "N":       ("lbf",     0.2248089431,      0.0),
    "kgf":     ("lbf",     2.2046226218,      0.0),
    "N·m":     ("lbf·ft",  0.7375621493,      0.0),
    "Nm":      ("lbf·ft",  0.7375621493,      0.0),
    "N/mm":    ("lbf/in",  5.7101471627,      0.0),
    "N/mm³":   ("lbf/in³", 3683.6055,         0.0),    # cubic stiffness coeff
    "/mm²":    ("/in²",    645.16,            0.0),     # per-area hardening coeff
    "N·m/°":   ("lbf·ft/°", 0.7375621493,     0.0),
    "bar":     ("psi",     14.503773773,      0.0),
    "kPa":     ("psi",     0.1450377377,      0.0),
    "MPa":     ("ksi",     0.1450377377,      0.0),    # thermal/structural stress
    "kW":      ("hp",      1.3410220888,      0.0),     # power (mechanical hp)
    "W":       ("hp",      0.0013410221,      0.0),
    "kJ":      ("BTU",     0.9478171203,      0.0),     # energy
    "J":       ("ft·lbf",  0.7375621493,      0.0),
    "°C":      ("°F",      9.0 / 5.0,         32.0),
    "N·s/m":   ("lbf·s/in", 0.0057101471627, 0.0),     # damping coefficient
    "N·s/mm":  ("lbf·s/in", 5.7101471627,     0.0),
    "m³/s":    ("ft³/s",   35.314666721,      0.0),     # volumetric flow
    "L/s":     ("ft³/s",   0.0353146667,      0.0),
}

# Units that are identical in both systems (kept explicit for clarity / safety).
_PASSTHROUGH = {"", "°", "%", "g", "s", "psi", "spring/wheel", "fail", "—"}


def current_system() -> str:
    """Active unit system, defaulting to metric and safe outside Streamlit."""
    if st is not None:
        try:
            return st.session_state.get("unit_system", METRIC)
        except Exception:
            return METRIC
    return METRIC


def is_us() -> bool:
    return current_system() == US


def label(unit: str) -> str:
    """Return the unit label for the active system."""
    if not is_us():
        return unit
    if unit in _CONVERSIONS:
        return _CONVERSIONS[unit][0]
    return unit


def from_metric(value: float, unit: str) -> float:
    """Convert a metric scalar to the active-system value (for display/inputs)."""
    if not is_us() or unit not in _CONVERSIONS:
        return value
    _, factor, offset = _CONVERSIONS[unit]
    return value * factor + offset


def from_metric_delta(value: float, unit: str) -> float:
    """Convert a metric DIFFERENCE (gradient, margin, ΔT) to the active system.

    A difference scales by the factor but takes NO offset — a 10 °C rise is an
    18 °F rise, not (10·9/5+32). Use this for any quantity that is a delta rather
    than an absolute reading (temperature gradients, margins, spans)."""
    if not is_us() or unit not in _CONVERSIONS:
        return value
    _, factor, _offset = _CONVERSIONS[unit]
    return value * factor


def to_metric(value: float, unit: str) -> float:
    """Convert an active-system scalar back to metric (for storing inputs)."""
    if not is_us() or unit not in _CONVERSIONS:
        return value
    _, factor, offset = _CONVERSIONS[unit]
    return (value - offset) / factor


def display(value: float, unit: str) -> Tuple[float, str]:
    """Return ``(converted_value, label)`` for a metric number."""
    return from_metric(value, unit), label(unit)


# --- string-aware conversion for pre-formatted metric() values -------------- #

# Matches a leading signed number (incl. + sign, decimals) at the start of a
# value string such as "+1.50", "-0.03", "300", "35.0". Anything that isn't a
# plain number (e.g. "—", "∞", "rocker", "proxy") is left untouched.
_NUM_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)")


def _infer_decimals(num_text: str) -> int:
    if "." in num_text:
        return len(num_text.split(".", 1)[1])
    return 0


def convert_value_str(value_str: str, unit: str) -> str:
    """Convert the leading numeric token of a formatted value string.

    Preserves an existing leading ``+`` sign and the original decimal precision
    so the display style is unchanged. Non-numeric values pass through.
    """
    if not is_us() or unit not in _CONVERSIONS:
        return value_str
    m = _NUM_RE.match(value_str)
    if not m:
        return value_str
    num_text = m.group(1)
    try:
        metric_val = float(num_text)
    except ValueError:
        return value_str
    us_val = from_metric(metric_val, unit)
    decimals = _infer_decimals(num_text)
    signed = num_text.lstrip().startswith("+")
    fmt = f"{{:+.{decimals}f}}" if signed else f"{{:.{decimals}f}}"
    converted = fmt.format(us_val)
    return value_str[: m.start(1)] + converted + value_str[m.end(1):]


# Compound / annotated labels that embed a metric unit (e.g. "°/10mm",
# "N/mm @35"). These need bespoke handling so both the number and the unit
# convert sensibly. Returns (converted_value_str, converted_unit_label).
def convert_compound(value_str: str, unit: str) -> Tuple[str, str]:
    if not is_us():
        return value_str, unit

    # "°/10mm" -> camber/bump gain per 10 mm of travel -> per inch.
    if unit == "°/10mm":
        m = _NUM_RE.match(value_str)
        if m:
            try:
                per_10mm = float(m.group(1))
                per_in = per_10mm * (25.4 / 10.0)  # °/10mm -> °/in
                decimals = _infer_decimals(m.group(1))
                signed = m.group(1).lstrip().startswith("+")
                fmt = f"{{:+.{decimals}f}}" if signed else f"{{:.{decimals}f}}"
                return fmt.format(per_in), "°/in"
            except ValueError:
                pass
        return value_str, "°/in"

    # "N/mm @35" style: wheel-rate value in N/mm with a spring annotation.
    if unit.startswith("N/mm"):
        suffix = unit[len("N/mm"):]  # e.g. " @35"
        new_val = convert_value_str(value_str, "N/mm")
        # Convert the embedded "@<rate>" spring annotation too.
        mm = re.search(r"@\s*([+-]?\d+(?:\.\d+)?)", suffix)
        if mm:
            try:
                rate = float(mm.group(1))
                rate_us = from_metric(rate, "N/mm")
                suffix = suffix[: mm.start(1)] + f"{rate_us:.0f}" + suffix[mm.end(1):]
            except ValueError:
                pass
        return new_val, "lbf/in" + suffix

    return convert_value_str(value_str, unit), label(unit)


# --- relabel unit TOKENS inside any header / title / widget-label string ---- #

# Longest-first so multi-char units (km/h, N·m, N/mm) match before their parts.
_TOKEN_ORDER = sorted(_CONVERSIONS.keys(), key=len, reverse=True)


def ulabel(text: str) -> str:
    """Rewrite metric unit tokens inside a free-text label/title/header to the
    active unit system. Leaves everything else untouched.

    Handles units wherever they appear: in parentheses ("Mass (kg)" -> "Mass
    (lb)"), after a space or slash, etc. In metric mode it is a no-op, so it is
    safe to wrap every title and label unconditionally.

    Examples (US active):
        "rotor mass (g)"               -> "rotor mass (g)"        (g passes through)
        "single-stop peak temp (°C)"   -> "single-stop peak temp (°F)"
        "travel (mm, + bump)"          -> "travel (in, + bump)"
        "downforce (N) @ 80 km/h"      -> "downforce (lbf) @ 80 mph"
        "ride height (mm)  ← lower …"  -> "ride height (in)  ← lower …"
    """
    if not is_us() or not text:
        return text
    out = text
    for metric_u in _TOKEN_ORDER:
        us_u = _CONVERSIONS[metric_u][0]
        if us_u == metric_u:
            continue  # passthrough (e.g. "g")
        # Replace the token only on a word/symbol boundary so we don't corrupt
        # substrings (e.g. don't turn "Nm" inside a word, or "m" inside "mm").
        pattern = r"(?<![A-Za-z0-9·/°])" + re.escape(metric_u) + r"(?![A-Za-z0-9·/])"
        out = re.sub(pattern, us_u, out)
    return out


# --- convert every "<number> <unit>" pair inside a free-text sentence ------- #

_PAIR_RES = None


def _pair_res():
    """Compiled per-unit regexes for number+unit pairs, longest token first."""
    global _PAIR_RES
    if _PAIR_RES is None:
        out = []
        for metric_u in _TOKEN_ORDER:
            us_u = _CONVERSIONS[metric_u][0]
            if us_u == metric_u:
                continue
            pat = re.compile(
                r"([+-]?\d+(?:\.\d+)?)\s*"
                + r"(?<![A-Za-z0-9·/°])" + re.escape(metric_u)
                + r"(?![A-Za-z0-9·/])")
            out.append((metric_u, pat))
        _PAIR_RES = out
    return _PAIR_RES


def usentence(text: str) -> str:
    """Convert every ``<number> <metric-unit>`` pair inside a sentence to the
    active system — number AND label, preserving each number's decimal
    precision. Metric mode is a no-op, so it is safe to wrap any user-facing
    sentence unconditionally.

    Examples (US active):
        "mass 42.00 kg → 41.00 kg"    -> "mass 92.60 lb → 90.39 lb"
        "target mass: 230 kg → 228 kg"-> "target mass: 507 lb → 503 lb"
        "shifted 1530.0 mm"           -> "shifted 60.2 in"
    Unitless numbers and unknown tokens pass through untouched.
    """
    if not is_us() or not text:
        return text

    def _sub_for(metric_u):
        us_u, factor, offset = _CONVERSIONS[metric_u]

        def _sub(m):
            num_text = m.group(1)
            try:
                v = float(num_text) * factor + offset
            except ValueError:
                return m.group(0)
            decimals = _infer_decimals(num_text)
            signed = num_text.lstrip().startswith("+")
            fmt = f"{{:+.{decimals}f}}" if signed else f"{{:.{decimals}f}}"
            return fmt.format(v) + " " + us_u

        return _sub

    out = text
    for metric_u, pat in _pair_res():
        out = pat.sub(_sub_for(metric_u), out)
    return out
