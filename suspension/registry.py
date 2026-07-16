# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================

"""
Component Registry — the "Source of Truth" for released CAD / assembly files.

The problem this kills: a subteam ships a part as a Google-Drive link buried in a
Discord message ("here is the most recently updated diff mount … sorry the folder
is disorganized"). Six weeks later nobody knows which link is current, what the
agreed offset was, or whether anyone verified it. The drive folder is a pile; the
truth lives in one person's memory.

The Registry replaces that pile with a table that always shows the *current*
version of each component, the numbers that matter (e.g. the diff-mount offset),
who verified it and when, and a one-click pull of the exact released file. Each
component has a running history of versions; exactly one is marked CURRENT — the
golden record. Promoting a new version is one action and it timestamps the change
and pushes the old one into history (never deletes it — handover memory).

Design choices match the rest of KinematiK:
  • Plain dataclasses, JSON on disk (registry.json), so it survives sessions and
    can be committed to the repo. The tool becomes the record.
  • No streamlit / network imports here — pure model, unit-testable headless.
  • Stored CAD bytes live next to the json in a content-addressed blob dir keyed
    by sha256, so the same upload is never stored twice and a row can always pull
    back the exact verified bytes. Storing blobs is optional: a row can instead
    just hold a link (the Drive URL) for teams that don't want to commit binaries.
"""

from __future__ import annotations

import os
import json
import uuid
import shutil
import datetime as _dt
from dataclasses import dataclass, field, asdict, fields as _dc_fields

DEFAULT_REGISTRY = "registry.json"
BLOB_DIRNAME = "registry_blobs"


def _now() -> str:
    return _dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------------------- #
#  Records
# --------------------------------------------------------------------------- #
@dataclass
class Version:
    """One released revision of a component."""
    id: str
    label: str                      # e.g. "Rev C — June 28 2026"
    created: str                    # ISO timestamp the row was added
    # where the file lives — exactly one of these is the source of truth:
    link: str = ""                  # external URL (Drive, etc.)
    blob_sha: str = ""              # content hash if bytes are stored locally
    blob_name: str = ""             # original filename for the stored blob
    # parsed / declared engineering numbers, free-form key->value:
    specs: dict = field(default_factory=dict)     # {"Offset": "42.0 mm", ...}
    provenance: dict = field(default_factory=dict)  # rolled-up CAD metadata
    manifest: dict = field(default_factory=dict)    # full ingest manifest
    # human sign-off:
    status: str = "unverified"      # unverified | verified | superseded
    verified_by: str = ""
    verified_on: str = ""
    notes: str = ""

    @staticmethod
    def from_dict(d: dict) -> "Version":
        known = {f.name for f in _dc_fields(Version)}
        return Version(**{k: v for k, v in d.items() if k in known})


@dataclass
class Component:
    """A tracked part/assembly with a history of versions and one current."""
    id: str
    name: str                       # "Differential Mount"
    subteam: str = "suspension"
    owner: str = ""                 # person responsible
    current_version_id: str = ""    # which Version is the golden record
    versions: list = field(default_factory=list)   # list[Version], newest last
    # metadata-validation rules for the status dashboard. Each is a small dict
    # like {"param":"Weight","op":"<=","value":2.5,"unit":"kg","label":"..."} —
    # the user declares the numbers, these declare what "OK" means. No CAD parse.
    rules: list = field(default_factory=list)
    created: str = ""
    updated: str = ""

    # -- convenience ------------------------------------------------------- #
    def current(self):
        for v in self.versions:
            if v.id == self.current_version_id:
                return v
        return self.versions[-1] if self.versions else None

    def history(self):
        """Versions other than current, newest first."""
        cur = self.current_version_id
        return [v for v in reversed(self.versions) if v.id != cur]

    @staticmethod
    def from_dict(d: dict) -> "Component":
        vers = [Version.from_dict(v) for v in d.get("versions", [])]
        known = {f.name for f in _dc_fields(Component)}
        base = {k: v for k, v in d.items() if k in known and k != "versions"}
        c = Component(**base)
        c.versions = vers
        return c

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# --------------------------------------------------------------------------- #
#  Registry
# --------------------------------------------------------------------------- #
class Registry:
    """Collection of components persisted to a single JSON file (+ blob dir)."""

    def __init__(self, path: str = DEFAULT_REGISTRY):
        self.path = path
        self.components: list[Component] = []
        self.load()

    # -- persistence ------------------------------------------------------- #
    @property
    def blob_dir(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(self.path)),
                            BLOB_DIRNAME)

    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r") as f:
                    raw = json.load(f)
                self.components = [Component.from_dict(c)
                                   for c in raw.get("components", [])]
            except (json.JSONDecodeError, OSError):
                self.components = []
        else:
            self.components = []

    def save(self):
        payload = {
            "schema": "kinematik.registry/1",
            "saved": _now(),
            "components": [c.to_dict() for c in self.components],
        }
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, self.path)

    # -- blob storage (content-addressed) ---------------------------------- #
    def store_blob(self, data: bytes, sha: str, name: str) -> str:
        """Persist raw bytes under sha256; return the stored path. Idempotent."""
        os.makedirs(self.blob_dir, exist_ok=True)
        ext = os.path.splitext(name)[1]
        dest = os.path.join(self.blob_dir, sha + ext)
        if not os.path.exists(dest):
            with open(dest, "wb") as f:
                f.write(data)
        return dest

    def blob_path(self, version: "Version") -> str | None:
        if not version.blob_sha:
            return None
        ext = os.path.splitext(version.blob_name or "")[1]
        p = os.path.join(self.blob_dir, version.blob_sha + ext)
        return p if os.path.exists(p) else None

    # -- component CRUD ---------------------------------------------------- #
    def get(self, comp_id: str):
        for c in self.components:
            if c.id == comp_id:
                return c
        return None

    def get_by_name(self, name: str):
        for c in self.components:
            if c.name.strip().lower() == name.strip().lower():
                return c
        return None

    def add_component(self, name: str, subteam: str = "suspension",
                      owner: str = "") -> Component:
        c = Component(id=_new_id("cmp"), name=name.strip(),
                      subteam=subteam, owner=owner,
                      created=_now(), updated=_now())
        self.components.append(c)
        return c

    def remove_component(self, comp_id: str) -> bool:
        before = len(self.components)
        self.components = [c for c in self.components if c.id != comp_id]
        return len(self.components) != before

    # -- version lifecycle ------------------------------------------------- #
    def add_version(self, comp_id: str, label: str, *, link: str = "",
                    blob_sha: str = "", blob_name: str = "",
                    specs: dict | None = None, provenance: dict | None = None,
                    manifest: dict | None = None, notes: str = "",
                    make_current: bool = True) -> Version | None:
        c = self.get(comp_id)
        if not c:
            return None
        v = Version(
            id=_new_id("ver"), label=label.strip() or _now(),
            created=_now(), link=link.strip(),
            blob_sha=blob_sha, blob_name=blob_name,
            specs=dict(specs or {}), provenance=dict(provenance or {}),
            manifest=dict(manifest or {}), notes=notes,
        )
        c.versions.append(v)
        if make_current or not c.current_version_id:
            self.set_current(comp_id, v.id)
        c.updated = _now()
        return v

    def set_current(self, comp_id: str, version_id: str) -> bool:
        """Promote a version to the golden record; demote the previous one."""
        c = self.get(comp_id)
        if not c:
            return False
        ids = {v.id for v in c.versions}
        if version_id not in ids:
            return False
        prev = c.current_version_id
        for v in c.versions:
            if v.id == prev and v.id != version_id and v.status == "verified":
                v.status = "superseded"
        c.current_version_id = version_id
        c.updated = _now()
        return True

    def verify_version(self, comp_id: str, version_id: str,
                       who: str) -> bool:
        c = self.get(comp_id)
        if not c:
            return False
        for v in c.versions:
            if v.id == version_id:
                v.status = "verified"
                v.verified_by = who.strip()
                v.verified_on = _now()
                c.updated = _now()
                return True
        return False

    def set_spec(self, comp_id: str, version_id: str,
                 key: str, value: str) -> bool:
        c = self.get(comp_id)
        if not c:
            return False
        for v in c.versions:
            if v.id == version_id:
                if value == "" and key in v.specs:
                    del v.specs[key]
                else:
                    v.specs[key] = value
                c.updated = _now()
                return True
        return False

    # -- dashboard roll-up ------------------------------------------------- #
    def summary_rows(self):
        """One row per component for the dashboard table."""
        rows = []
        for c in self.components:
            cur = c.current()
            rows.append({
                "comp_id": c.id,
                "name": c.name,
                "subteam": c.subteam,
                "owner": c.owner,
                "version": cur.label if cur else "—",
                "version_id": cur.id if cur else "",
                "status": cur.status if cur else "empty",
                "verified_by": cur.verified_by if cur else "",
                "verified_on": cur.verified_on if cur else "",
                "specs": cur.specs if cur else {},
                "has_file": bool(cur and (cur.blob_sha or cur.link)) if cur else False,
                "link": cur.link if cur else "",
                "blob_sha": cur.blob_sha if cur else "",
                "n_versions": len(c.versions),
                "rules": list(c.rules),
                "updated": c.updated,
            })
        return rows

    def set_rules(self, comp_id: str, rules: list) -> bool:
        """Replace a component's metadata-validation rules (status dashboard)."""
        c = self.get(comp_id)
        if not c:
            return False
        c.rules = list(rules or [])
        c.updated = _now()
        return True
