# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""
Guard tests for the lazy package __init__ (PEP 562).

These lock in the property that motivated the refactor: the cross-discipline
interface ledger and the powertrain myth-checker depend on nothing heavy, so
``import suspension`` and the light submodules must import without the
scientific/visualisation stack present. They also keep ``__all__`` and the
lazy-resolution table (``_FROM`` / ``_SUBMODULES``) in sync, so a future
contributor cannot silently reintroduce an unresolvable public name or the old
eager-import coupling.
"""
import importlib
import importlib.abc
import subprocess
import sys

import pytest


# Heavy third-party deps that the light import path must NOT pull in.
# scipy is deliberately excluded: it is a legitimate compute dependency that the
# kinematics/dynamics/lap-sim core genuinely needs, and listing it here would
# make this test assert something false about the package.
HEAVY_OPTIONAL = [
    "plotly", "trimesh", "reportlab", "supabase",
    "cascadio", "fast_simplification", "rtree",
]

# Repo root, injected into each subprocess's sys.path so the child can import
# `suspension` regardless of what the parent's working directory happens to be.
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_import_suspension_is_free_of_heavy_optional_deps():
    """`import suspension` must succeed with the heavy optional deps blocked.

    Run in a clean subprocess so the already-imported package in this session
    doesn't mask the behaviour.
    """
    code = (
        "import sys, importlib.abc\n"
        f"sys.path.insert(0, {_ROOT!r})\n"
        f"HEAVY={HEAVY_OPTIONAL!r}\n"
        "class B(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in HEAVY:\n"
        "            raise ImportError('blocked:'+name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, B())\n"
        "import suspension\n"
        "from suspension.interfaces import Severity, SubsystemInterface\n"
        "from suspension import pt_integration\n"
        "from suspension import check_assumption, motor_envelope\n"
        "print('OK')\n"
    )
    r = subprocess.run([sys.executable, "-c", code],
                       capture_output=True, text=True)
    assert r.returncode == 0, (
        "Importing suspension (and its light submodules) pulled in a heavy "
        f"optional dependency.\nSTDERR:\n{r.stderr}"
    )
    assert r.stdout.strip().endswith("OK")


def test_heavy_feature_is_deferred_not_eager():
    """A plotly-backed symbol must only fail when actually accessed, proving the
    import is deferred rather than paid at package load."""
    code = (
        "import sys, importlib.abc\n"
        f"sys.path.insert(0, {_ROOT!r})\n"
        "class B(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0]=='plotly':\n"
        "            raise ImportError('blocked:plotly')\n"
        "        return None\n"
        "sys.meta_path.insert(0, B())\n"
        "import suspension\n"            # must succeed
        "raised=False\n"
        "try:\n"
        "    suspension.build_full_car_figure\n"   # plotly-backed: must raise now
        "except ImportError:\n"
        "    raised=True\n"
        "print('RAISED' if raised else 'NOTRAISED')\n"
    )
    r = subprocess.run([sys.executable, "-c", code],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().endswith("RAISED")


def test_all_names_resolve():
    """Every name in __all__ must resolve through __getattr__ (no dangling
    re-exports).

    A missing THIRD-PARTY dependency is not a dangling re-export: the lazy
    __getattr__ found and dispatched the name correctly, and the ImportError
    is the documented contract for optional features on machines without the
    extra (see requirements.txt — the app boots without plotly/trimesh etc.,
    each just unlocks its feature). So a ModuleNotFoundError whose missing
    module lives OUTSIDE the suspension package is collected separately and
    turns the test into a SKIP (environment gap, honestly reported), while
    anything else — AttributeError, or an import failure originating inside
    the package — still fails hard, because that IS a broken re-export.
    """
    import pytest
    import suspension
    unresolved = []
    missing_deps = set()
    for name in suspension.__all__:
        try:
            getattr(suspension, name)
        except ModuleNotFoundError as exc:
            _missing = (exc.name or "").split(".")[0]
            if _missing and _missing != "suspension":
                missing_deps.add(_missing)       # env gap, not a wiring bug
            else:
                unresolved.append((name, type(exc).__name__, str(exc)[:80]))
        except Exception as exc:  # noqa: BLE001 - we want the name + reason
            unresolved.append((name, type(exc).__name__, str(exc)[:80]))
    assert not unresolved, f"Unresolvable public names: {unresolved}"
    if missing_deps:
        pytest.skip("lazy wiring OK; third-party deps not installed here: "
                    + ", ".join(sorted(missing_deps)))


def test_all_and_resolution_table_in_sync():
    """Each __all__ entry must be either a submodule or a mapped re-export, and
    every mapped name should be advertised in __all__ (so dir()/help stay
    truthful)."""
    import suspension
    submodules = set(suspension._ATTR_SUBMODULES)
    from_map = set(suspension._FROM)
    known = submodules | from_map

    missing = [n for n in suspension.__all__ if n not in known]
    assert not missing, (
        "These __all__ names are not in _SUBMODULES or _FROM and cannot be "
        f"lazily resolved: {missing}"
    )

    # NOTE (reverse direction): the package has always re-exported a number of
    # names that are resolvable but were never listed in __all__ (e.g.
    # GGVGenerator, check_assumption, MotorEnvelope, build_full_car_figure).
    # The lazy refactor preserves that historical surface exactly rather than
    # silently changing the public API. We surface the gap as information, not a
    # failure, so the leads can decide whether to promote any of these into
    # __all__ — that is a deliberate API decision, not a refactor side effect.
    public = set(suspension.__all__)
    undeclared = sorted(n for n in known if n not in public)
    if undeclared:
        print(
            "\n[info] resolvable but not in __all__ "
            f"({len(undeclared)} names): {undeclared}"
        )


def test_dir_advertises_full_surface():
    import suspension
    d = set(dir(suspension))
    for name in suspension.__all__:
        assert name in d, f"{name} missing from dir(suspension)"


def test_unknown_attribute_raises_attribute_error():
    import suspension
    with pytest.raises(AttributeError):
        suspension.this_symbol_does_not_exist  # noqa: B018
