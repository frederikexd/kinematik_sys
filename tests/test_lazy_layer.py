"""Verifies the lazy-import architecture using the REAL classes extracted from
streamlit_app.py and the real suspension.tubeframe module."""
import ast, json, sys, time

import os
import subprocess

_HERE = os.path.abspath(__file__)
_ROOT = os.path.dirname(os.path.dirname(_HERE))


def main():
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    # -- extract _LazyModule/_LazySymbol/_purge helper verbatim from the app ----- #
    src = open(os.path.join(_ROOT, "streamlit_app.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    wanted = {"_LazyModule", "_LazySymbol"}
    ns = {"importlib": __import__("importlib")}
    for node in tree.body:
        if isinstance(node, (ast.ClassDef,)) and node.name in wanted:
            exec(compile(ast.Module([node], []), "streamlit_app.py", "exec"), ns)
    _LazyModule, _LazySymbol = ns["_LazyModule"], ns["_LazySymbol"]

    # 1 — package import is inert: no submodule, no numpy pulled in.
    for m in list(sys.modules):
        if m.startswith("suspension") or m == "numpy":
            del sys.modules[m]
    t0 = time.perf_counter()
    import suspension
    dt = time.perf_counter() - t0
    assert "suspension.tubeframe" not in sys.modules
    assert "numpy" not in sys.modules, "package init must not drag numpy in"
    print(f"1. `import suspension` inert, {dt*1e3:.2f} ms")

    # 2 — _LazyModule defers until first attribute touch, imports exactly once.
    tf = _LazyModule("suspension.tubeframe")
    assert "suspension.tubeframe" not in sys.modules
    g = tf.demo_frame()
    assert "suspension.tubeframe" in sys.modules and "numpy" in sys.modules
    assert tf._load() is sys.modules["suspension.tubeframe"]
    print("2. _LazyModule: deferred, then delegates to real module")

    # 3 — _LazySymbol: call, classmethod attr, dict protocols.
    FrameGraph = _LazySymbol("suspension.tubeframe", "FrameGraph")
    g2 = FrameGraph.from_dict(g.as_dict())            # attr -> classmethod
    assert isinstance(g2, tf.FrameGraph)
    sizes = _LazySymbol("suspension.tubeframe", "MEMBER_CLASS_MIN_SIZE")
    assert list(sizes) and ("side_impact" in sizes) and sizes["side_impact"]
    assert list(sizes.keys())                          # .keys() via __getattr__
    print("3. _LazySymbol: __call__/__getattr__/__getitem__/__contains__/__iter__ OK")

    # 4 — post-import hook fires exactly once.
    hits = []
    pm = _LazyModule("suspension.tubeframe", post=lambda m: hits.append(m.__name__))
    pm.TubeSpec; pm.FrameGraph
    assert hits == ["suspension.tubeframe"]
    print("4. post-import hook: exactly once")

    # 5 — cached-audit body: canonical-JSON key round-trips and audits run.
    key = json.dumps(g.as_dict(), sort_keys=True)
    ga = tf.FrameGraph.from_dict(json.loads(key))
    audit = {"quads": ga.untriangulated_quads(),
             "landings": ga.midspan_landings(),
             "tris": ga.triangulated_nodes()}
    assert isinstance(audit["tris"], set) and audit["quads"], "demo frame has defects by design"
    print(f"5. frame audit via JSON key: {len(audit['quads'])} quads, "
          f"{len(audit['landings'])} landings, {len(audit['tris'])} triangulated nodes")

    # 6 — __init__ fallback scan degrades gracefully (missing modules skipped).
    try:
        suspension.Hardpoints
        print("6. symbol resolved (kinematics module present)")
    except AttributeError as e:
        assert "_SYMBOL_HOME" in str(e)
        print("6. missing-module symbol -> clean AttributeError with fix hint (no crash)")

    print("ALL LAZY-LAYER CHECKS PASSED")


# The script purges `suspension*` and `numpy` from sys.modules to prove the
# package import is inert. That is destructive to an in-process pytest session
# (later tests would see freshly re-imported, non-identical module objects),
# so under pytest the whole scenario runs in a clean subprocess instead.
def test_lazy_layer_script_in_subprocess():
    r = subprocess.run([sys.executable, _HERE], cwd=_ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0, (
        f"lazy-layer script failed\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}")
    assert "ALL LAZY-LAYER CHECKS PASSED" in r.stdout


if __name__ == "__main__":
    main()
