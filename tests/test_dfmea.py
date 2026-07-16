# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================

"""
Tests for the Powertrain DFMEA engine (suspension/dfmea.py).

These pin the behaviour the Powertrain team relies on when it keeps its risk log
in the app instead of a loose spreadsheet:

  - RPN is always S x O x D, with ratings clamped to 1..10 and string/garbage
    cells tolerated (the table never throws mid-edit),
  - risk banding treats Severity specially: a high-severity row is Critical even
    at low RPN (the User-Guide rule "do not ignore high-severity items"),
  - the seed log is non-empty, uses the exact User-Guide columns, and its RPNs
    are internally consistent,
  - an existing workbook round-trips: messy/lower-cased column names import, the
    RPN column is ignored on import (always recomputed),
  - the dashboard roll-up and action tracker count the things the User Guide §7
    asks for (open high-risk, closed-without-evidence, action-without-owner).

Loads the engine module directly (no package __init__, no plotly), like the
other engine tests.

Run:  python tests/test_dfmea.py
"""

import importlib
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _load():
    # Import through the real (lazy) package so pytest and standalone runs see
    # the same module objects — no stub `suspension` in sys.modules.
    return importlib.import_module("suspension.dfmea")


DF = _load()

_PASS, _FAIL = [], []


def check(name, cond):
    (_PASS if cond else _FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


# --- RPN + clamping --------------------------------------------------------- #
check("rpn is S*O*D", DF.compute_rpn(8, 5, 4) == 160)
check("rpn tolerates strings", DF.compute_rpn("8", "5", "4") == 160)
check("rpn clamps high ratings to 10", DF.compute_rpn(99, 5, 4) == 10 * 5 * 4)
check("rpn clamps low ratings to 1", DF.compute_rpn(0, 5, 4) == 1 * 5 * 4)
check("rpn tolerates garbage", DF.compute_rpn(None, "x", 4) == 1 * 1 * 4)

# --- risk banding ----------------------------------------------------------- #
check("severity promotes to Critical", DF.classify_risk(9, 10).value == "Critical")
check("rpn promotes to Critical", DF.classify_risk(5, 320).value == "Critical")
check("high severity is at least High", DF.classify_risk(7, 10).value == "High")
check("moderate is Medium", DF.classify_risk(3, 90).value == "Medium")
check("benign is Low", DF.classify_risk(2, 10).value == "Low")

# --- seed log --------------------------------------------------------------- #
_seed = DF.seed_rows()
check("seed log is non-empty", len(_seed) >= 7)
check("seed rows use canonical columns",
      all(set(DF.COLUMNS) == set(r) for r in _seed))
check("seed RPNs are internally consistent",
      all(r["RPN"] == DF.compute_rpn(r["Severity"], r["Occurrence"], r["Detection"])
          for r in _seed))
check("seed covers the summer projects",
      any("sprocket" in (r["Item / Component"] + r["Failure Mode"]).lower()
          for r in _seed)
      and any("gear ratio" in r["Failure Mode"].lower() for r in _seed)
      and any("motor mount" in r["Item / Component"].lower() for r in _seed))

# --- import round-trip ------------------------------------------------------ #
_row = DF.row_from_mapping({"Subsystem ": "Cooling", "SEVERITY": "7",
                            "rpn": 999, "Owner": None})
check("import matches messy/cased column names",
      _row.subsystem == "Cooling" and _row.severity == 7)
check("import ignores RPN column (recomputed)",
      _row.rpn == DF.compute_rpn(7, _row.occurrence, _row.detection))
check("import coerces missing owner to empty string", _row.owner == "")
_rt = DF.from_records(_seed)
check("from_records preserves count", len(_rt) == len(_seed))

# --- dashboard roll-up ------------------------------------------------------ #
_stats = DF.dashboard_stats(_seed)
check("dashboard counts all rows", _stats.total == len(_seed))
check("dashboard flags open high-risk", _stats.open_high_risk >= 1)
check("seed actions all lack owners (template state)",
      _stats.actions_without_owner == len(_seed))

# closed-without-evidence is flagged
_one_closed = [dict(_seed[0])]
_one_closed[0]["Status"] = "Closed"
_one_closed[0]["Evidence / Notes"] = ""
check("dashboard flags Closed-without-evidence",
      DF.dashboard_stats(_one_closed).closed_without_evidence == 1)

# a Closed row WITH evidence is not flagged and drops out of the tracker
_closed_ok = [dict(_seed[0])]
_closed_ok[0]["Status"] = "Closed"
_closed_ok[0]["Evidence / Notes"] = "pressure-test log attached"
check("Closed+evidence is not flagged",
      DF.dashboard_stats(_closed_ok).closed_without_evidence == 0)

# --- action tracker --------------------------------------------------------- #
_items = DF.action_items(_seed)
check("tracker lists open actioned rows", len(_items) == len(_seed))
check("tracker drops resolved rows", len(DF.action_items(_closed_ok)) == 0)
check("tracker is risk-sorted (Critical/High first)",
      _items[0]["Risk Band"] in ("Critical", "High"))


print(f"\n{len(_PASS)} passed, {len(_FAIL)} failed")


# --- pytest bridge: expose every module-level check as a test case ---------- #
import pytest  # noqa: E402


@pytest.mark.parametrize("name", _PASS + _FAIL)
def test_check(name):
    assert name not in _FAIL, f"check failed: {name}"


if __name__ == "__main__":
    if _FAIL:
        print("FAILED:", _FAIL)
        sys.exit(1)
