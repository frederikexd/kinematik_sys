# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================

"""
CAD ingest — parse metadata out of CAD / FEA deliverables instead of just
storing the bytes.

This is the "ingest, don't just store" half of the Registry. A subteam drops a
zip (or a single file) that came off SolidWorks, and instead of becoming one
more opaque blob in a shared drive, KinematiK reads what it can *verify from the
file itself*:

  • IGES (.igs/.iges) — a clean ASCII neutral file. Its Global section is fully
    parseable and carries the authoritative provenance: the original model name,
    the absolute path it was exported from, the CAD system + version, the model
    units, the design resolution, the author initials, and the export timestamp.
    This is the single most trustworthy metadata source in a SolidWorks export
    and the one we lean on.

  • SolidWorks (.sldprt/.sldasm/.slddrw) — modern SW files are not OLE2 compound
    documents, so the old `olefile` summary-stream trick does not apply. We still
    record type (part / assembly / drawing), size, and content hash so the row is
    a fingerprinted, deduplicated record even when the internal properties are
    not machine-readable.

  • SOLIDWORKS Simulation (.MFC / .LOG) — small text sidecars from a Simulation
    study. The .MFC holds the study name / solver / type; the .LOG holds the mesh
    statistics (nodes, elements, DOF, solve time). We surface these so an FEA
    result advertises *what it actually is* on the dashboard.

Everything here is best-effort and never raises on a file it can't read — an
unknown file still yields a row with name / size / sha256, which is enough to be
a registry entry. Nothing in this module imports streamlit or touches the
network; it is pure parsing so it can be unit-tested headless.
"""

from __future__ import annotations

import os
import re
import io
import json
import hashlib
import zipfile
import datetime as _dt
from dataclasses import dataclass, field, asdict

# --------------------------------------------------------------------------- #
#  File-type classification
# --------------------------------------------------------------------------- #
# Map of lower-case extension -> (kind, human label). "kind" is a coarse bucket
# the dashboard groups / filters on.
_EXT_KIND = {
    ".sldprt": ("part",      "SolidWorks Part"),
    ".sldasm": ("assembly",  "SolidWorks Assembly"),
    ".slddrw": ("drawing",   "SolidWorks Drawing"),
    ".igs":    ("neutral",   "IGES neutral"),
    ".iges":   ("neutral",   "IGES neutral"),
    ".step":   ("neutral",   "STEP neutral"),
    ".stp":    ("neutral",   "STEP neutral"),
    ".stl":    ("mesh",      "STL mesh"),
    ".obj":    ("mesh",      "OBJ mesh"),
    ".glb":    ("mesh",      "GLB mesh"),
    ".x_t":    ("neutral",   "Parasolid text"),
    ".x_b":    ("neutral",   "Parasolid binary"),
    ".mfc":    ("fea",       "Simulation study config"),
    ".log":    ("fea",       "Simulation solve log"),
    ".cwr":    ("fea",       "Simulation results"),
    ".bdf":    ("fea",       "Nastran bulk data"),
    ".sl4":    ("fea",       "Simulation setup"),
    ".txt":    ("doc",       "Text note"),
    ".md":     ("doc",       "Markdown note"),
    ".pdf":    ("doc",       "PDF document"),
    ".csv":    ("data",      "CSV data"),
}

# Extensions we consider "the deliverable you'd actually want to open" — used to
# pick a sensible primary file when a whole folder is ingested.
_PRIMARY_PREFERENCE = [".sldasm", ".step", ".stp", ".igs", ".iges",
                       ".sldprt", ".x_t", ".stl"]


def classify(name: str):
    """Return (kind, label, ext) for a filename."""
    ext = os.path.splitext(name)[1].lower()
    kind, label = _EXT_KIND.get(ext, ("other", "File"))
    return kind, label, ext


def _sha256(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
#  IGES Global-section parser
# --------------------------------------------------------------------------- #
# The IGES Global section is parameter records terminated by ';' and delimited by
# ',' with Hollerith strings encoded as "<count>H<chars>". The field ORDER is
# fixed by the IGES 5.3 spec; the indices we care about:
#   2  product id (sender)            14  units name (e.g. "MM")
#   3  file name / original path      15  max line weight gradations
#   4  native system id               16  width of max line weight
#   5  preprocessor version           17  date/time of file generation
#   11 product id (receiver)          18  min user-intended resolution
#   12 model space scale              19  approx max coordinate value
#   13 unit flag                      20  author name
#                                     21 author organisation
#                                     23 date/time model last modified
_HOLLERITH = re.compile(r'(\d+)H')


def _decode_iges_global(global_text: str):
    """Split a concatenated IGES Global section into its ordered fields,
    honouring Hollerith string lengths."""
    fields = []
    i = 0
    n = len(global_text)
    cur = ""
    while i < n:
        m = _HOLLERITH.match(global_text, i)
        if m:
            count = int(m.group(1))
            start = m.end()
            fields.append(global_text[start:start + count])
            i = start + count
            # consume one trailing delimiter if present
            if i < n and global_text[i] in ",;":
                i += 1
        elif global_text[i] in ",;":
            # empty / numeric field already flushed; a bare delimiter ends a field
            if cur != "":
                fields.append(cur)
                cur = ""
            else:
                fields.append("")
            i += 1
        else:
            cur += global_text[i]
            i += 1
    if cur != "":
        fields.append(cur)
    return fields


def _iges_units(unit_flag: str, unit_name: str) -> str:
    name = (unit_name or "").strip().upper()
    if name:
        return {"MM": "mm", "M": "m", "IN": "in", "INCH": "in",
                "FT": "ft", "CM": "cm"}.get(name, name.lower())
    # fall back on the numeric unit flag (2 == mm, 1 == inch, ...)
    return {"1": "in", "2": "mm", "3": "ft", "4": "mi",
            "5": "m", "6": "km", "8": "cm"}.get(str(unit_flag).strip(), "?")


def _iges_timestamp(raw: str):
    """IGES timestamps are YYMMDD.HHMMSS or 15H + same. Return ISO-ish string."""
    raw = (raw or "").strip()
    m = re.match(r'(\d{2})(\d{2})(\d{2})\.(\d{2})(\d{2})(\d{2})', raw)
    if m:
        yy, mo, dd, hh, mm, ss = m.groups()
        year = 2000 + int(yy)
        try:
            return _dt.datetime(year, int(mo), int(dd),
                                int(hh), int(mm), int(ss)).isoformat(sep=" ")
        except ValueError:
            return raw
    return raw or None


def parse_iges(data: bytes) -> dict:
    """Parse provenance out of an IGES file's Start + Global sections.

    Returns a dict of best-effort fields; missing values are simply absent.
    """
    try:
        text = data.decode("latin-1", errors="replace")
    except Exception:
        return {}
    start_lines, global_lines = [], []
    for line in text.splitlines():
        if len(line) < 73:
            continue
        sec = line[72]
        if sec == "S":
            start_lines.append(line[:72].rstrip())
        elif sec == "G":
            global_lines.append(line[:72])
        elif sec in ("D", "P", "T"):
            # Global section is contiguous and comes before D; once we hit D we
            # can stop scanning for metadata.
            if global_lines:
                break
    if not global_lines:
        return {}
    fields = _decode_iges_global("".join(global_lines))

    def g(idx):
        return fields[idx].strip() if idx < len(fields) and fields[idx] else None

    out = {
        "format": "IGES",
        "model_name": g(2) or g(11),
        "source_path": g(3),
        "cad_system": g(4),
        "cad_version": g(5),
        "units": _iges_units(g(13), g(14)),
        "model_scale": g(12),
        "min_resolution": g(18),
        "max_coord": g(19),
        "author": g(20),
        "organisation": g(21),
        "exported": _iges_timestamp(g(17)),
        "modified": _iges_timestamp(g(23)),
        "description": " ".join(l.strip() for l in start_lines).strip() or None,
    }
    # strip the file name off the original absolute path for a clean folder hint
    if out.get("source_path"):
        out["source_dir"] = os.path.dirname(out["source_path"].replace("\\", "/"))
    return {k: v for k, v in out.items() if v}


# --------------------------------------------------------------------------- #
#  SOLIDWORKS Simulation sidecars (.MFC / .LOG)
# --------------------------------------------------------------------------- #
def parse_sim_mfc(data: bytes) -> dict:
    """Pull study name / solver / type out of a Simulation .MFC ini-style file."""
    try:
        text = data.decode("utf-16", errors="ignore")
        if "[" not in text:
            text = data.decode("latin-1", errors="replace")
    except Exception:
        text = data.decode("latin-1", errors="replace")
    out = {"format": "SW Simulation"}
    # the .MFC is grouped [Key] then "<file>=<value>" lines
    cur = None
    wanted = {"studyname": "study", "studytype": "study_type",
              "solvertype": "solver_mode", "solver": "solver"}
    for line in text.splitlines():
        line = line.strip().strip("\x00")
        m = re.match(r'\[(.+?)\]$', line)
        if m:
            cur = m.group(1).lower()
            continue
        if cur in wanted and "=" in line:
            val = line.split("=", 1)[1].strip()
            if val:
                out[wanted[cur]] = val
    return out if len(out) > 1 else {}


_LOG_FIELDS = [
    (re.compile(r'nodes\s*=\s*([\d,]+)', re.I),     "nodes"),
    (re.compile(r'elements\s*=\s*([\d,]+)', re.I),  "elements"),
    (re.compile(r'DOF\s*=\s*([\d,]+)', re.I),       "dof"),
    (re.compile(r'solution time\s*=\s*([\d:]+)', re.I), "solve_time"),
]


def parse_sim_log(data: bytes) -> dict:
    """Pull mesh statistics out of a Simulation solve .LOG."""
    text = data.decode("utf-16", errors="ignore")
    if "node" not in text.lower():
        text = data.decode("latin-1", errors="replace")
    out = {"format": "SW Simulation solve"}
    for rx, key in _LOG_FIELDS:
        m = rx.search(text)
        if m:
            out[key] = m.group(1).replace(",", "")
    return out if len(out) > 1 else {}


# --------------------------------------------------------------------------- #
#  Per-file metadata extraction
# --------------------------------------------------------------------------- #
def extract_file_meta(name: str, data: bytes) -> dict:
    """Best-effort metadata for one file. Always returns at least
    name / size / sha256 / kind."""
    kind, label, ext = classify(name)
    meta = {
        "name": os.path.basename(name),
        "ext": ext,
        "kind": kind,
        "label": label,
        "size_bytes": len(data),
        "sha256": _sha256(data),
    }
    try:
        if ext in (".igs", ".iges"):
            meta["parsed"] = parse_iges(data)
        elif ext == ".mfc":
            meta["parsed"] = parse_sim_mfc(data)
        elif ext == ".log" and kind == "fea":
            meta["parsed"] = parse_sim_log(data)
    except Exception as exc:  # never let a bad file break ingest
        meta["parse_error"] = str(exc)
    if not meta.get("parsed"):
        meta.pop("parsed", None)
    return meta


# --------------------------------------------------------------------------- #
#  Bundle ingest (single file OR a zip of a whole folder)
# --------------------------------------------------------------------------- #
def _pick_primary(files):
    """Choose the most 'openable' file as the bundle's representative."""
    by_ext = {f["ext"]: f for f in files}
    for ext in _PRIMARY_PREFERENCE:
        if ext in by_ext:
            return by_ext[ext]["name"]
    return files[0]["name"] if files else None


def _roll_up_provenance(files):
    """Promote the richest IGES/neutral provenance to the bundle level so the
    dashboard can show a 'Golden Record' line without the user digging."""
    best = {}
    for f in files:
        p = f.get("parsed") or {}
        if p.get("format") == "IGES":
            # IGES wins; merge, preferring the first IGES seen
            for k, v in p.items():
                best.setdefault(k, v)
    return best


def ingest_bundle(filename: str, data: bytes, max_files: int = 500) -> dict:
    """Ingest a single uploaded artefact.

    If it's a zip, walk every member and parse each; otherwise treat the bytes
    as one file. Returns a manifest dict ready to attach to a registry row:

        {
          "primary":   "<representative filename>",
          "bundle_sha": "<sha256 of the raw upload>",
          "file_count": N,
          "total_bytes": ...,
          "kinds": {"part": 7, "fea": 6, ...},
          "provenance": {... rolled-up IGES metadata ...},
          "files": [ {per-file meta}, ... ],
        }
    """
    bundle_sha = _sha256(data)
    files = []
    is_zip = filename.lower().endswith(".zip") or zipfile.is_zipfile(io.BytesIO(data))
    if is_zip:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    if len(files) >= max_files:
                        break
                    base = os.path.basename(info.filename)
                    if not base or base.startswith("."):
                        continue
                    try:
                        member = zf.read(info)
                    except Exception:
                        continue
                    files.append(extract_file_meta(info.filename, member))
        except zipfile.BadZipFile:
            files.append(extract_file_meta(filename, data))
    else:
        files.append(extract_file_meta(filename, data))

    kinds = {}
    for f in files:
        kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1

    return {
        "primary": _pick_primary(files),
        "bundle_sha": bundle_sha,
        "file_count": len(files),
        "total_bytes": sum(f["size_bytes"] for f in files),
        "kinds": kinds,
        "provenance": _roll_up_provenance(files),
        "files": files,
    }


# --------------------------------------------------------------------------- #
#  Small helpers the UI layer reuses
# --------------------------------------------------------------------------- #
def human_size(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{f:.0f} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} GB"


def short_sha(sha: str, n: int = 10) -> str:
    return (sha or "")[:n]
