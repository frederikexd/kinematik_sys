"""Tests for the Component Registry (Source of Truth) and CAD ingest parser.

These run fully headless with no large fixtures: a minimal synthetic IGES and a
synthetic Simulation .LOG exercise the real parsers, and an in-memory zip
exercises bundle ingest + the full registry lifecycle and persistence.
"""
import os
import io
import sys
import zipfile
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from suspension import registry as R
from suspension import cad_ingest as I


# --- a minimal valid IGES with a real Global section ---------------------- #
# Columns 1..72 hold data, column 73 is the section letter, 74..80 a sequence #.
def _iges_line(body, sec, seq):
    return f"{body:<72}{sec}{seq:>7}\n"


def _make_iges():
    start = _iges_line("Synthetic test IGES", "S", 1)
    g = ("1H,,1H;,17HDiffMount.SLDASM,21HC:\\cars\\DiffMount.IGS,"
         "15HSolidWorks 2024,15HSolidWorks 2024,32,308,15,308,15,"
         "17HDiffMount.SLDASM,1.,2,2HMM,50,0.125,13H250628.143000,1E-08,"
         "499990.,5Hadwks,,11,0,13H250628.150000;")
    glines = ""
    seq = 1
    for i in range(0, len(g), 72):
        glines += _iges_line(g[i:i + 72], "G", seq)
        seq += 1
    d = _iges_line("     314       1       0", "D", 1)
    return (start + glines + d).encode("latin-1")


def _make_sim_log():
    return ("No. of nodes        = 34,881\r\n"
            "No. of elements     = 20,120\r\n"
            "No. of DOF          = 103,797\r\n"
            "Total solution time = 00:00:02\r\n").encode("latin-1")


def _make_sim_mfc():
    return ("[StudyName]\nx.MFC=Static 1\n[SolverType]\nx.MFC=Linear Analysis\n"
            "[Solver]\nx.MFC=Sparse\n").encode("latin-1")


def _make_bundle_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("DiffMount/model.IGS", _make_iges())
        zf.writestr("DiffMount/Static 1.LOG", _make_sim_log())
        zf.writestr("DiffMount/Static 1.MFC", _make_sim_mfc())
        zf.writestr("DiffMount/bracket.SLDPRT", b"\x2c\x80\x7e\xc5binarypart")
        zf.writestr("DiffMount/asm.SLDASM", b"\xcf\x5e\x91\x84binaryasm")
    return buf.getvalue()


def test_iges_parser():
    p = I.parse_iges(_make_iges())
    assert p["cad_system"] == "SolidWorks 2024"
    assert p["units"] == "mm"
    assert p["author"] == "adwks"
    assert "DiffMount" in p["model_name"]
    assert p["exported"].startswith("2025-06-28")


def test_sim_log_parser():
    p = I.parse_sim_log(_make_sim_log())
    assert p["nodes"] == "34881"
    assert p["elements"] == "20120"
    assert p["solve_time"] == "00:00:02"


def test_sim_mfc_parser():
    p = I.parse_sim_mfc(_make_sim_mfc())
    assert p["study"] == "Static 1"
    assert p["solver_mode"] == "Linear Analysis"


def test_bundle_ingest():
    m = I.ingest_bundle("DiffMount.zip", _make_bundle_zip())
    assert m["file_count"] == 5
    # provenance promoted from the IGES
    assert m["provenance"]["cad_system"] == "SolidWorks 2024"
    # assembly chosen as primary
    assert m["primary"].endswith(".SLDASM")
    # FEA mesh surfaced
    fea = [f for f in m["files"] if f.get("parsed", {}).get("nodes")]
    assert fea and fea[0]["parsed"]["nodes"] == "34881"


def test_bad_file_never_raises():
    m = I.ingest_bundle("junk.sldprt", b"\x00\x01not real\xff")
    assert m["file_count"] == 1
    assert m["files"][0]["sha256"]


def test_lifecycle_and_persistence():
    d = tempfile.mkdtemp()
    reg = R.Registry(os.path.join(d, "registry.json"))
    data = _make_bundle_zip()
    m = I.ingest_bundle("diff.zip", data)
    reg.store_blob(data, m["bundle_sha"], "diff.zip")
    c = reg.add_component("Differential Mount", "powertrain", "Dustin")
    v1 = reg.add_version(c.id, "Rev B", blob_sha=m["bundle_sha"],
                         blob_name="diff.zip", provenance=m["provenance"],
                         manifest=m)
    reg.set_spec(c.id, v1.id, "Offset", "40.0 mm")
    reg.verify_version(c.id, v1.id, "Aidan Turner")
    v2 = reg.add_version(c.id, "Rev C", link="https://drive.google.com/x")
    assert c.current().id == v2.id
    assert len(c.history()) == 1
    assert reg.get(c.id).versions[0].status == "superseded"
    reg.save()

    reg2 = R.Registry(os.path.join(d, "registry.json"))
    rows = reg2.summary_rows()
    assert len(rows) == 1 and rows[0]["version"] == "Rev C"
    old_id = reg2.get(c.id).history()[0].id
    assert reg2.set_current(c.id, old_id)
    assert reg2.get(c.id).current().specs["Offset"] == "40.0 mm"
    assert reg2.blob_path(reg2.get(c.id).current()) is not None


def test_dedup_blob():
    import hashlib
    d = tempfile.mkdtemp()
    reg = R.Registry(os.path.join(d, "registry.json"))
    data = b"hello world bytes"
    sha = hashlib.sha256(data).hexdigest()
    assert reg.store_blob(data, sha, "a.stl") == reg.store_blob(data, sha, "a.stl")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("✓", name)
    print("\nALL TESTS PASSED")
