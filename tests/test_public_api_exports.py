# ============================================================================
#  KinematiK — public-API export guard
# ============================================================================
"""Every name in suspension.__init__._FROM and _SUBMODULES must actually
resolve. The lazy-import layer means a stale entry (or a symbol added to a
module but never registered) does NOT fail at boot — it fails when a user
touches the feature. This test moves that failure to CI time.

This is the guard for the exact bug class that broke 41 tests in July 2026:
rotor-thermal and throttle symbols existed in their home modules but were
never added to the re-export table.
"""
import importlib

import pytest

import suspension


def test_every_from_entry_resolves():
    bad = []
    for public_name, (submod, original) in suspension._FROM.items():
        try:
            mod = importlib.import_module(f"suspension.{submod}")
        except Exception as exc:
            bad.append(f"{public_name}: submodule suspension.{submod} failed to "
                       f"import ({type(exc).__name__}: {exc})")
            continue
        if not hasattr(mod, original):
            bad.append(f"{public_name}: suspension.{submod} has no '{original}'")
    assert not bad, "stale _FROM entries:\n  " + "\n  ".join(bad)


def test_every_submodule_imports():
    bad = []
    for submod in suspension._SUBMODULES:
        try:
            importlib.import_module(f"suspension.{submod}")
        except Exception as exc:
            bad.append(f"suspension.{submod}: {type(exc).__name__}: {exc}")
    assert not bad, "broken _SUBMODULES entries:\n  " + "\n  ".join(bad)


def test_lazy_getattr_actually_serves_every_public_name():
    """Touch every public name through the package itself, the way the app and
    tests do (suspension.X), so the __getattr__ path is exercised end-to-end."""
    bad = []
    for public_name in suspension._FROM:
        try:
            getattr(suspension, public_name)
        except Exception as exc:
            bad.append(f"suspension.{public_name}: {type(exc).__name__}: {exc}")
    assert not bad, "public names that raise on access:\n  " + "\n  ".join(bad)
