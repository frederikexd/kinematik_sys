# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""
Tests for the Excel-as-database backend functions that implement Erick's
Discord feedback (26-06-2026):

  * read_raw_sheets           — "take that raw data from my sheet and visualise it"
  * find_feasible_pack_sheet  — "once that sheet exists in your file the app can
                                 just surface it directly"
  * ledger_declarations_from_ev_params — "Joule heat fs should be pulled plus key
                                 electric propulsion stats", flowing into the
                                 integration ledger the way the Accumulator tab does

The honesty contract is the thing under test as much as the parsing: these must
echo only what's in the workbook, never invent a column or a number, and produce
physically sane derived values (the heat-rejection amortisation had a unit bug
that this suite now guards against).
"""
import io

import numpy as np
import pytest

openpyxl = pytest.importorskip("openpyxl")

from suspension import ev_excel_roundtrip as rt


# --------------------------------------------------------------------------- #
#  Fixtures — a workbook shaped like the electrics lead's real file            #
# --------------------------------------------------------------------------- #
def _make_workbook(*, with_telemetry=True, with_feasible=True,
                   joule_kwh=0.85, endurance_km=22):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Battery Pack Calcs"
    pack_rows = [
        ("Fuse max (A)", 50), ("n_parallel", 4), ("n_series", 120),
        ("cell V", 3.6), ("cell Ah", 4.2), ("endurance km", endurance_km),
        ("max cells", 600), ("cell R ohm", 0.012), ("cell wt kg", 0.045),
        ("pack cells", 480), ("pack V", 432), ("cell I", 180),
        ("power kW", 78), ("pack Ah", 16.8), ("pack Wh", 7257),
        ("joule kWh", joule_kwh),
    ]
    for i, (lbl, val) in enumerate(pack_rows, start=1):
        ws.cell(row=i, column=1, value=lbl)
        ws.cell(row=i, column=2, value=val)

    ws2 = wb.create_sheet("ElecPropulsion")
    ep_rows = [
        ("peak torque", 140), ("peak power kW", 80), ("freq kHz", 10),
        ("poles", 10), ("max dc V", 600), ("eff", 0.95), ("I from pack", 175),
        ("pack V", 432), ("max rpm", 6500), ("wheel in", 18), ("pf", 0.95),
    ]
    for i, (lbl, val) in enumerate(ep_rows, start=1):
        ws2.cell(row=i, column=1, value=lbl)
        ws2.cell(row=i, column=2, value=val)
    for j, col in enumerate(range(8, 23)):
        ws2.cell(row=1, column=col, value=3.0 + j * 0.1)

    if with_telemetry:
        ws3 = wb.create_sheet("CellTelemetry")
        ws3.append(["time_s", "cell_V_min", "cell_V_max",
                    "pack_current_A", "cell_temp_C"])
        for t in range(60):
            ws3.append([t * 0.5, 3.55 - 0.002 * t, 3.62 - 0.001 * t,
                        120 + 40 * np.sin(t / 5), 28 + 0.3 * t])

    if with_feasible:
        ws4 = wb.create_sheet("Pack You'd Need")
        ws4.append(["Metric", "Current", "Needed"])
        ws4.append(["Energy (kWh)", 7.26, 8.10])
        ws4.append(["Cells", 480, 540])
        ws4.append(["Mass (kg)", 21.6, 24.3])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
#  read_raw_sheets                                                             #
# --------------------------------------------------------------------------- #
def test_read_raw_sheets_finds_telemetry_columns():
    raw = rt.read_raw_sheets(_make_workbook())
    assert "_error" not in raw
    tele = next((s for s in raw["sheets"] if s["name"] == "CellTelemetry"), None)
    assert tele is not None, "telemetry sheet not surfaced"
    headers = [c["header"] for c in tele["columns"]]
    for expected in ("time_s", "cell_V_min", "pack_current_A", "cell_temp_C"):
        assert expected in headers, f"missing column {expected}"


def test_read_raw_sheets_values_are_verbatim():
    raw = rt.read_raw_sheets(_make_workbook())
    tele = next(s for s in raw["sheets"] if s["name"] == "CellTelemetry")
    vmin = next(c for c in tele["columns"] if c["header"] == "cell_V_min")
    assert len(vmin["values"]) == 60
    # first value should be 3.55 exactly (3.55 - 0.002*0)
    assert abs(vmin["values"][0] - 3.55) < 1e-9


def test_read_raw_sheets_lists_all_sheet_names():
    raw = rt.read_raw_sheets(_make_workbook())
    for name in ("Battery Pack Calcs", "ElecPropulsion", "CellTelemetry",
                 "Pack You'd Need"):
        assert name in raw["sheet_names"]


def test_read_raw_sheets_skips_non_numeric_columns():
    """A column that is text-only must not appear as a numeric column."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["label", "value"])
    for i in range(10):
        ws.append([f"row{i}", i * 2.0])
    buf = io.BytesIO(); wb.save(buf)
    raw = rt.read_raw_sheets(buf.getvalue())
    sheet = raw["sheets"][0]
    headers = [c["header"] for c in sheet["columns"]]
    assert "value" in headers
    assert "label" not in headers


def test_read_raw_sheets_handles_missing_openpyxl(monkeypatch):
    # Simulate the import failing inside the function.
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "openpyxl":
            raise ImportError("no openpyxl")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    out = rt.read_raw_sheets(b"whatever")
    assert out["sheets"] == []
    assert "_error" in out


def test_read_raw_sheets_on_garbage_bytes_returns_error_not_crash():
    out = rt.read_raw_sheets(b"this is not an xlsx file")
    assert out["sheets"] == []
    assert "_error" in out


# --------------------------------------------------------------------------- #
#  find_feasible_pack_sheet                                                    #
# --------------------------------------------------------------------------- #
def test_find_feasible_pack_sheet_present():
    fp = rt.find_feasible_pack_sheet(_make_workbook(with_feasible=True))
    assert fp["found"] is True
    assert fp["sheet"] == "Pack You'd Need"
    assert ["Energy (kWh)", 7.26, 8.1] in fp["rows"]


def test_find_feasible_pack_sheet_absent():
    fp = rt.find_feasible_pack_sheet(_make_workbook(with_feasible=False))
    assert fp["found"] is False
    assert fp["sheet"] == ""
    assert fp["rows"] == []


# --------------------------------------------------------------------------- #
#  ledger_declarations_from_ev_params                                          #
# --------------------------------------------------------------------------- #
def test_ledger_declarations_pull_propulsion_stats():
    params = rt.extract_params_from_excel(_make_workbook())
    decl = rt.ledger_declarations_from_ev_params(params)
    assert decl["powertrain"]["peak_power_kw"] == 80.0
    assert decl["powertrain"]["peak_torque_nm"] == 140.0
    assert decl["powertrain"]["voltage_v"] == 432.0
    assert decl["electrics"]["peak_current_a"] == 180.0
    assert decl["_provenance"] == "FSAE_EV_Power_Draw.xlsx"


def test_ledger_heat_rejection_is_physically_sane():
    """The Joule-heat amortisation must produce a sane heat-rejection rate.
    0.85 kWh over a 22 km @ 50 km/h stint (~26 min) is ~1.9 kW, NOT megawatts.
    This guards the unit bug found in development."""
    params = rt.extract_params_from_excel(
        _make_workbook(joule_kwh=0.85, endurance_km=22))
    decl = rt.ledger_declarations_from_ev_params(params)
    heat_w = decl["electrics"]["heat_reject_w"]
    assert 1000.0 < heat_w < 5000.0, f"heat_reject_w={heat_w} W is not sane"
    assert decl["_notes"], "should explain the stint-average derivation"


def test_ledger_declarations_omit_missing_fields_no_invention():
    """A workbook missing a value must NOT get a fabricated one."""
    # Only a peak power, nothing else.
    decl = rt.ledger_declarations_from_ev_params(
        {"pack": {}, "motor": {"motor_peak_power_kw": 75.0}})
    assert decl["powertrain"] == {"peak_power_kw": 75.0}
    # no electrics block at all, since nothing electrical was provided
    assert "electrics" not in decl


def test_ledger_declarations_empty_input():
    decl = rt.ledger_declarations_from_ev_params({})
    # only provenance, no fabricated subsystem numbers
    assert "powertrain" not in decl
    assert "electrics" not in decl
    assert decl["_provenance"] == "FSAE_EV_Power_Draw.xlsx"
