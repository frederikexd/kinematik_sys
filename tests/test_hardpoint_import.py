# ============================================================================
#  KinematiK — hardpoint importer tests
# ============================================================================
"""The importer's contract is the product's brand: never guess silently.
Ambiguous names are refused (both directions), unit and frame inferences are
reported with their basis, mirroring and re-origining are itemised, and a
correct OptimumK-style export round-trips to exact editor coordinates."""
import io

import pytest

from suspension.hardpoint_import import (
    RawPoint, build_result, group_corners, infer_units, map_names,
    parse_tabular, points_for_corner)


# --------------------------------------------------------------------------- #
#  Fixtures: the KinematiK default corner expressed as an ISO 8855 / metres /
#  OptimumK-vocabulary export (x fwd, y left → our right-side corner has -y)
# --------------------------------------------------------------------------- #
OPTIMUMK_CSV = """Point Name,X (m),Y (m),Z (m)
Upper Wishbone Front Pivot FL,0.100,-0.240,0.2808
Upper Wishbone Rear Pivot FL,-0.130,-0.240,0.2992
Lower Wishbone Front Pivot FL,0.110,-0.200,0.1225
Lower Wishbone Rear Pivot FL,-0.140,-0.200,0.1175
Upper Ball Joint FL,-0.012,-0.540,0.300
Lower Ball Joint FL,0.005,-0.575,0.110
Tie Rod Inboard FL,-0.100,-0.230,0.160
Tie Rod Outboard FL,-0.090,-0.560,0.150
Wheel Center FL,0.0,-0.600,0.228
Contact Patch FL,0.0,-0.605,0.0
Pushrod Outboard FL,0.005,-0.408,0.120
Mystery Bracket FL,0.1,0.2,0.3
""".encode()

EXPECTED = {
    "upper_front_inner": (-100.0, 240.0, 280.8),
    "upper_rear_inner": (130.0, 240.0, 299.2),
    "lower_front_inner": (-110.0, 200.0, 122.5),
    "lower_rear_inner": (140.0, 200.0, 117.5),
    "upper_outer": (12.0, 540.0, 300.0),
    "lower_outer": (-5.0, 575.0, 110.0),
    "tie_rod_inner": (100.0, 230.0, 160.0),
    "tie_rod_outer": (90.0, 560.0, 150.0),
    "wheel_center": (0.0, 600.0, 228.0),
    "contact_patch": (0.0, 605.0, 0.0),
    "pushrod_outer": (-5.0, 408.0, 120.0),
}


def _close(a, b, tol=0.05):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


# --------------------------------------------------------------------------- #
#  Parsing
# --------------------------------------------------------------------------- #
def test_parse_csv_with_headers_and_unit_hint():
    pts, hint = parse_tabular(OPTIMUMK_CSV, "export.csv")
    assert len(pts) == 12 and hint == "m"


def test_parse_headerless_rows_and_semicolon_delimiter():
    raw = ("Upper wishbone front inboard;-100;240;280.8\n"
           "junk text row with no numbers\n"
           "Lower wishbone front inboard;-110;200;122.5\n").encode()
    pts, hint = parse_tabular(raw, "points.csv")
    assert [p.name for p in pts] == ["Upper wishbone front inboard",
                                     "Lower wishbone front inboard"]
    assert hint is None


def test_parse_xlsx_multisheet(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Front"
    ws.append(["Point", "X [mm]", "Y [mm]", "Z [mm]"])
    ws.append(["Wheel center", 0, 600, 228])
    ws2 = wb.create_sheet("Notes")
    ws2.append(["nothing to see"])
    buf = io.BytesIO()
    wb.save(buf)
    pts, hint = parse_tabular(buf.getvalue(), "book.xlsx")
    assert len(pts) == 1 and pts[0].name == "Wheel center" and hint == "mm"


def test_parse_columns_in_any_order():
    raw = ("Z,Point Name,X,Y\n"
           "228,Wheel centre,0,600\n").encode()
    pts, _ = parse_tabular(raw, "odd.csv")
    assert pts[0].coords == (0.0, 600.0, 228.0)


# --------------------------------------------------------------------------- #
#  Name mapping — the honesty guarantees
# --------------------------------------------------------------------------- #
def test_vocabulary_variants_map():
    cases = {
        "UCA fore chassis pickup": "upper_front_inner",
        "LCA aft inboard": "lower_rear_inner",
        "upper balljoint": "upper_outer",
        "toe link outboard": "tie_rod_outer",
        "track rod inner": "tie_rod_inner",
        "wheel centre": "wheel_center",
        "tyre contact patch": "contact_patch",
        "push rod outboard": "pushrod_outer",
        "bellcrank pivot": "rocker_pivot",
        "bell crank to pushrod": "rocker_pushrod",
        "rocker spring mount": "rocker_spring",
        "damper inboard": "spring_inner",
    }
    pts = [RawPoint(n, 1, 2, 3) for n in cases]
    mapped, unmapped, ambiguous = map_names(pts)
    inv = {p.name: k for k, p in mapped.items()}
    for name, want in cases.items():
        if want:
            assert inv.get(name) == want, f"{name!r} -> {inv.get(name)}"


def test_bare_wc_shorthand_maps_alone_but_duplicates_are_refused():
    # "WC" alone is standard FSAE shorthand for wheel center …
    m, u, a = map_names([RawPoint("WC", 0, 600, 228)])
    assert "wheel_center" in m
    # … but two names claiming wheel_center are BOTH refused, not raced
    m2, u2, a2 = map_names([RawPoint("WC", 0, 600, 228),
                            RawPoint("wheel centre", 0, 600, 228)])
    assert "wheel_center" not in m2 and len(a2) == 2


def test_ambiguous_name_is_refused_not_guessed():
    pts = [RawPoint("upper front inner outer thing", 0, 0, 0)]
    mapped, unmapped, ambiguous = map_names(pts)
    assert not mapped
    # either bucket is honest; silence is not
    assert unmapped or ambiguous


def test_duplicate_claims_on_one_key_both_reported():
    pts = [RawPoint("upper wishbone front pivot", 0, 0, 0),
           RawPoint("UCA fore inboard", 9, 9, 9)]
    mapped, unmapped, ambiguous = map_names(pts)
    assert "upper_front_inner" not in mapped
    assert len(ambiguous) == 2


# --------------------------------------------------------------------------- #
#  Units
# --------------------------------------------------------------------------- #
def test_header_hint_wins():
    pts = [RawPoint("wheel center", 0.0, 0.6, 0.228)]
    unit, basis = infer_units(pts, header_hint="m")
    assert unit == "m" and "header" in basis


@pytest.mark.parametrize("scale,expected", [(1.0, "mm"), (1 / 25.4, "in"),
                                            (1 / 1000.0, "m")])
def test_magnitude_heuristic(scale, expected):
    pts = [RawPoint("wheel center", 0 * scale, 600 * scale, 228 * scale),
           RawPoint("lower balljoint", -5 * scale, 575 * scale, 110 * scale)]
    unit, basis = infer_units(pts, header_hint=None)
    assert unit == expected and basis


# --------------------------------------------------------------------------- #
#  Full pipeline: frames, mirror, re-origin, round-trip
# --------------------------------------------------------------------------- #
def test_optimumk_iso8855_metres_roundtrips_to_editor_coords():
    pts, hint = parse_tabular(OPTIMUMK_CSV, "export.csv")
    work = points_for_corner(group_corners(pts), "FL")
    res = build_result(work, frame_key="iso8855", header_hint=hint)
    assert res.unit == "m" and "header" in res.unit_basis
    assert len(res.mapped) == len(EXPECTED)
    for k, want in EXPECTED.items():
        assert _close(res.mapped[k], want), (k, res.mapped[k], want)
    assert [p.name for p in res.unmapped] == ["Mystery Bracket FL"]
    assert not res.ambiguous


def test_left_side_in_y_right_frame_is_auto_mirrored_and_reported():
    # same corner expressed directly in a y-right frame but on the LEFT
    # (negative y) — e.g. a CAD table of the left corner
    pts = [RawPoint(n, x, -y, z) for n, (x, y, z) in [
        ("wheel center", EXPECTED["wheel_center"]),
        ("contact patch", EXPECTED["contact_patch"]),
        ("upper balljoint", EXPECTED["upper_outer"]),
        ("lower balljoint", EXPECTED["lower_outer"]),
    ]]
    res = build_result(pts, frame_key="kinematik", unit="mm")
    assert res.mirrored
    assert any("mirrored" in w.lower() for w in res.warnings)
    assert _close(res.mapped["wheel_center"], EXPECTED["wheel_center"])


def test_explicit_mirror_off_is_respected():
    pts = [RawPoint("wheel center", 0, -600, 228),
           RawPoint("contact patch", 0, -605, 0)]
    res = build_result(pts, frame_key="kinematik", unit="mm", mirror=False)
    assert not res.mirrored and res.mapped["wheel_center"][1] == -600.0


def test_reorigin_shifts_to_editor_convention_and_reports():
    # whole-car coordinates: wheel centre at x=1530 (front axle), ground z=0
    # already, but z offset +50 to test both shifts
    pts = [RawPoint(n, x + 1530.0, y, z + 50.0) for n, (x, y, z) in
           EXPECTED.items()]
    res = build_result(pts, frame_key="kinematik", unit="mm")
    assert res.reorigined
    dx, _, dz = res.reorigin_shift
    assert abs(dx - 1530.0) < 0.1 and abs(dz - 50.0) < 0.1
    for k, want in EXPECTED.items():
        assert _close(res.mapped[k], want), k


def test_missing_core_points_are_named_in_warnings():
    res = build_result([RawPoint("wheel center", 0, 600, 228)],
                       frame_key="kinematik", unit="mm", reorigin=False)
    assert any("contact_patch" in w for w in res.warnings)


def test_empty_or_garbage_never_raises():
    res = build_result([], frame_key="iso8855")
    assert not res.ok and res.warnings
    pts, _ = parse_tabular(b"not,really,a,point,table\n1,2\n", "x.csv")
    assert isinstance(pts, list)
