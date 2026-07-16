# Tests for the CHASSIS DOCS & SHARED CAD feature:
#   - CADFile persistence round-trip on ProjectStore
#   - hardpoints CSV, SES location pack, and APDL BEAM188 deck generation
import base64
import os
import tempfile

from suspension.project import ProjectStore, CADFile
from suspension.kinematics import Hardpoints
from suspension.flex import MATERIALS
from suspension.tubeframe import demo_frame
from suspension import cad_share as cs


def _fresh_store():
    d = tempfile.mkdtemp()
    return ProjectStore(os.path.join(d, "project.json"))


def test_cadfile_roundtrip_and_filters():
    s = _fresh_store()
    blob = b"SLDPRT-BINARY" * 50
    s.add_cad_file(CADFile(name="upright.SLDPRT", subsystem="suspension",
                           uploader="fred", kind="file",
                           data_b64=base64.b64encode(blob).decode(),
                           size_bytes=len(blob)))
    s.add_cad_file(CADFile(name="big.SLDASM", subsystem="chassis",
                           kind="link", link="https://example.com/x"))
    assert s.save()

    s2 = ProjectStore(s.path)
    assert len(s2.cad_files) == 2
    assert s2.cad_subsystems() == ["chassis", "suspension"]
    susp = s2.cad_files_for("suspension")
    assert [c.name for c in susp] == ["upright.SLDPRT"]
    assert base64.b64decode(susp[0].data_b64) == blob
    assert susp[0].ext == "sldprt"


def test_cadfile_remove():
    s = _fresh_store()
    s.add_cad_file(CADFile(name="a.stl", subsystem="aero"))
    fid = s.cad_files[0].id
    assert s.remove_cad_file(fid) is True
    assert s.remove_cad_file(fid) is False
    assert s.cad_files == []


def test_embed_limit_guard():
    assert cs.within_embed_limit(5 * 1024 * 1024) is True
    assert cs.within_embed_limit(cs.CAD_EMBED_LIMIT_BYTES) is True
    assert cs.within_embed_limit(cs.CAD_EMBED_LIMIT_BYTES + 1) is False


def test_hardpoints_csv():
    hp = Hardpoints.default().as_dict()
    txt = cs.hardpoints_csv(hp)
    lines = txt.strip().splitlines()
    assert lines[0] == "point,x_mm,y_mm,z_mm"
    # default corner has a full rocker/pushrod -> all 15 points present
    assert len(lines) - 1 == 15
    assert any(row.startswith("wheel_center,") for row in lines)


def test_ses_location_pack():
    rows = cs.ses_location_rows(hv=(-150, 0, 180), lv=(200, 150, 120),
                                placed={"Motor": (600, 0, 200)})
    cats = {r.category for r in rows}
    assert "HV" in cats and "LV" in cats
    csv_txt = cs.ses_location_csv(rows)
    assert csv_txt.startswith("component,category,x_mm,y_mm,z_mm")
    assert "HV accumulator" in csv_txt and "LV battery" in csv_txt


def test_ses_pack_empty_without_hv_lv():
    assert cs.ses_location_rows() == []


def test_apdl_deck_with_demo_frame():
    hp = Hardpoints.default().as_dict()
    mat = MATERIALS["Steel 4130"]
    section = cs.BeamSection(od_m=19.05e-3, wall_m=0.9e-3)
    material = cs.BeamMaterial(name="Steel 4130", E_pa=mat.E * 1e6,
                               rho_kg_m3=mat.rho)
    frame = demo_frame()
    deck = cs.build_apdl_deck(hp, section, material, frame=frame)

    assert "ET,1,BEAM188" in deck
    assert "SECTYPE,1,BEAM,CTUBE" in deck
    assert "TORSIONAL STIFFNESS RECIPE" in deck
    assert "K_torsion = T / theta" in deck

    # 15 frame tubes generated from the demo frame's structural + defect tubes
    frame_tube_lines = [l for l in deck.splitlines()
                        if l.strip().startswith("L,") and "frame tube" in l]
    assert len(frame_tube_lines) == 15

    counts = cs.apdl_counts(hp, frame=frame)
    assert counts["frame_tubes"] == 15
    assert counts["links"] >= 10   # full rocker corner -> 11; direct-acting -> 10


def test_apdl_deck_without_frame():
    hp = Hardpoints.default().as_dict()
    section = cs.BeamSection(od_m=19.05e-3, wall_m=0.9e-3)
    material = cs.BeamMaterial(name="Steel 4130", E_pa=205e9)
    deck = cs.build_apdl_deck(hp, section, material, frame=None)
    assert "BEAM188" in deck
    assert "frame tube" not in deck
    counts = cs.apdl_counts(hp, frame=None)
    assert counts["frame_tubes"] == 0


def test_material_units_mpa_to_pa():
    # Guard the MPa->Pa conversion the UI relies on.
    mat = MATERIALS["Steel 4130"]
    assert abs(mat.E * 1e6 - 205e9) < 1e6
