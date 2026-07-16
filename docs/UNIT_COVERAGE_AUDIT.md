# Unit-system coverage audit — metric ⇄ US/Imperial

Goal: when the unit switch is enabled, every user-facing feature displays in
the active system, with no half-converted strings ("180 MPa > 120 psi").

## How coverage is achieved

Three layers, in order of preference:

1. **Explicit converters at the site** — `uval()`, `uconv_series()` + `ulabel()`
   on charts, `unum()` / `uslider` for inputs. ~350 call sites already use
   these. This is the preferred pattern; new code should use it.

2. **A render-time choke point for message channels** —
   `_install_unit_aware_messages()` wraps `st.metric/caption/error/warning/
   success/info` (on every container) so their strings pass through
   `ulabel` + `usentence` at display time. This exists because ~250 older
   message strings splice values next to hardcoded metric tokens and had
   drifted (e.g. bolt-bearing "MPa", fade-test "°C" summaries); editing each
   would drift again. Both helpers are **no-ops in metric mode** and
   **idempotent** — a string already converted via `uval` ("507 lb") is not
   touched, because `usentence` only matches `<number> <metric-token>` pairs
   and the token is already imperial. Test: `test_usentence.py::
   test_already_us_string_is_stable`.

3. **`usentence()`** — the new sentence converter powering layer 2, also
   usable directly for any free-text (history diffs, generated notes).

## Deliberate mm-native exceptions (NOT bugs)

Two 3D geometry scenes — the harness router (≈L19315) and the chassis
land/quad viewer (≈L20868) — keep millimetre axes and plot raw mm geometry
regardless of the toggle. Rationale: these are CAD coordinate viewers; the
hardpoint editor itself is explicitly mm-native ("ISO 4130-style, x-rear
y-right z-up" — see its header), and every CAD/DXF export downstream is mm.
Converting deep 3D vertex arrays across dozens of traces would add regression
risk for a context where millimetres are the correct working unit. The axis
titles state "mm" truthfully, so nothing is mislabelled.

Energy in **kWh** stays kWh in both systems (no common US motorsport swap;
kJ/J → BTU/ft·lbf exist in the table for thermodynamic contexts). This is
intentional, not a gap.

## Adding a feature later

Use `uval`/`uconv_series`/`unum` at the site. If you must emit a free-text
sentence with an embedded value, either build it with `uval` or wrap the
final string in `units.usentence(...)`. Do not hardcode a unit label next to
a raw metric number without one of these.
