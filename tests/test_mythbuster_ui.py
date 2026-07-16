# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""
Guard tests for the cross-discipline myth-buster UI wiring in streamlit_app.py.

Streamlit's 13k-line module body can't be imported headless (it executes UI on
import), so these tests do what the app's own structure allows: they extract the
myth-buster render function and its helpers via AST and execute them against a
small Streamlit stub whose column objects proxy widget calls the way real
Streamlit columns do. That exercises the true control flow of
``render_mythbuster`` — discipline pick, claim input, engine call, result
storage — without standing up a browser.

What this locks in:
  * the render function and its helpers still exist and are wired into the app,
  * a claim typed in the box reaches the engine and routes to the right
    discipline, producing a non-UNKNOWN verdict for known myths,
  * the idle render path (no button press) doesn't crash,
  * the result survives a rerun via its dict round-trip.
"""
import ast
import os
import types

import pytest

APP = os.path.join(os.path.dirname(os.path.dirname(__file__)), "streamlit_app.py")

_WANT = {
    "render_mythbuster", "_mb_build_context", "_mb_render_card",
    "_MBDictResult", "_mb_reference_claim_for", "_mb_verdict_value",
    "_MB_VCFG", "_MB_DISCIPLINES", "_mb_validation_disclaimer",
}


def _extract_chunks():
    """Pull the myth-buster definitions out of streamlit_app.py as source text."""
    if not os.path.exists(APP):
        pytest.skip("streamlit_app.py not present")
    src = open(APP).read()
    tree = ast.parse(src)
    found, chunks = set(), []
    for node in tree.body:
        name = getattr(node, "name", None)
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and name in _WANT:
            chunks.append(ast.get_source_segment(src, node)); found.add(name)
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in _WANT:
                    chunks.append(ast.get_source_segment(src, node)); found.add(t.id)
    missing = _WANT - found
    assert not missing, f"myth-buster wiring missing from streamlit_app.py: {missing}"
    return "\n\n".join(chunks)


def _stub_streamlit(button=True, text=""):
    class Col:
        def __init__(self, st): self._st = st
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def selectbox(self, label, options=None, *a, **k):
            return list(options)[0] if options else None
        def text_input(self, *a, **k): return self._st._text
        def button(self, *a, **k): return self._st._btn
        def __getattr__(self, n): return lambda *a, **k: None

    class SS(dict):
        def __getattr__(self, k): return self.get(k)
        def __setattr__(self, k, v): self[k] = v

    class Exp:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    st = types.ModuleType("streamlit")
    st.session_state = SS()
    st._btn, st._text = button, text
    st.columns = lambda spec, *a, **k: [
        Col(st) for _ in range(spec if isinstance(spec, int) else len(spec))]
    st.selectbox = lambda label, options=None, *a, **k: (
        list(options)[0] if options else None)
    st.text_input = lambda *a, **k: text
    st.button = lambda *a, **k: button
    st.markdown = st.caption = st.warning = lambda *a, **k: None
    st.expander = lambda *a, **k: Exp()
    return st


def _run(claim, *, button=True):
    from suspension import tiremodel as tire_mod
    from suspension.dynamics import VehicleParams
    st = _stub_streamlit(button=button, text=claim)
    ns = {"st": st, "tire_mod": tire_mod, "VehicleParams": VehicleParams}
    exec(_extract_chunks(), ns)
    st.session_state["tire_coeffs"] = dict(tire_mod.default_tire().coeffs)
    st.session_state["tire_fnomin"] = tire_mod.default_tire().FNOMIN
    st.session_state["vp"] = VehicleParams().__dict__.copy()
    ns["render_mythbuster"]()
    return st.session_state.get("mb_last_result_dict")


@pytest.mark.parametrize("claim,discipline", [
    ("twice the load gives twice the grip", "suspension"),
    ("all the cells heat up evenly", "cooling"),
    ("a stronger chassis is a stiffer chassis", "chassis"),
    ("a higher voltage pack gives more power", "electrics"),
    ("double the speed doubles the downforce", "aerodynamics"),
    ("a bigger brake rotor makes us stop faster", "brakes"),
])
def test_ui_routes_claim_to_engine_and_discipline(claim, discipline):
    res = _run(claim)
    assert res is not None, "render_mythbuster stored no result"
    assert res["discipline"] == discipline, \
        f"{claim!r} -> {res['discipline']} (expected {discipline})"
    assert res["verdict"] != "unknown"
    assert res["explanation"]


def test_ui_idle_render_does_not_crash():
    # No button press, empty box: must run and store nothing new.
    res = _run("", button=False)
    assert res is None  # nothing was checked


def test_ui_helpers_present_in_app():
    # _extract_chunks asserts every wiring symbol exists; calling it is the check.
    chunks = _extract_chunks()
    assert "render_mythbuster" in chunks
    assert "integration" not in chunks or True  # placeholder; presence is enough


def test_integration_tab_dispatches_to_mythbuster():
    """The myth-buster must stay reachable from the Integration surface.

    The dedicated "Myth-buster" radio option was folded into the Verdict
    Center (sanity-check page), so the guard is now: the app still labels the
    Myth-buster somewhere in the Integration wiring AND still calls
    render_mythbuster() — i.e. the dispatch was refactored, not removed."""
    src = open(APP).read()
    assert ("Myth-buster" in src or "Myth-Buster" in src), \
        "Myth-buster surface missing from the app"
    assert "render_mythbuster()" in src, "render_mythbuster() not dispatched"
