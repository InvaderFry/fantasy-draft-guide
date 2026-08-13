"""Immutable raw-data snapshots (S65, S84).

Raw data is immutable. A snapshot directory is a dated, hashed capture that a
research rerun within an edition reads from, and that a later edition can
verify byte-for-byte.

The hard rule is S84's ``never: overwrite a prior capture``. Writing over an
existing snapshot file raises; it does not warn and it does not silently
replace. A capture of intra-summer ADP movement cannot be taken again later.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

from pipeline.config import SNAPSHOT_DIR


class SnapshotExistsError(FileExistsError):
    """Raised on any attempt to overwrite an existing capture (S84)."""


def snapshot_dir(date: dt.date | None = None) -> Path:
    date = date or dt.date.today()
    return SNAPSHOT_DIR / date.isoformat()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Snapshot:
    """A single dated capture directory with a manifest.

    The manifest records, per file: source, url, retrieval time, sha256, size,
    license and free-text notes -- everything needed to reproduce a number from
    an edition manifest plus a source snapshot (S48).
    """

    def __init__(self, date: dt.date | None = None) -> None:
        self.date = date or dt.date.today()
        self.dir = snapshot_dir(self.date)
        self.manifest_path = self.dir / "manifest.json"

    # -- manifest ------------------------------------------------------
    def read_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {"snapshot_date": self.date.isoformat(), "files": {}}
        with self.manifest_path.open() as fh:
            return json.load(fh)

    def _write_manifest(self, manifest: dict[str, Any]) -> None:
        tmp = self.manifest_path.with_suffix(".json.tmp")
        with tmp.open("w") as fh:
            json.dump(manifest, fh, indent=2, sort_keys=True)
            fh.write("\n")
        tmp.replace(self.manifest_path)

    # -- writing -------------------------------------------------------
    def write(
        self,
        filename: str,
        data: bytes,
        *,
        source: str,
        url: str | None = None,
        license: str | None = None,
        notes: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Path:
        """Write one payload into the snapshot. Never overwrites (S84)."""
        path = self.dir / filename
        if path.exists():
            raise SnapshotExistsError(
                f"{path} already exists. S84 forbids overwriting a prior capture; "
                "a snapshot is immutable once written. Use a different date or filename."
            )
        if not data:
            raise ValueError(
                f"refusing to write an empty payload for {source}/{filename}: "
                "an empty capture is a lost day, not a successful run (S84)."
            )

        self.dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

        manifest = self.read_manifest()
        manifest["snapshot_date"] = self.date.isoformat()
        manifest.setdefault("files", {})[filename] = {
            "source": source,
            "url": url,
            "retrieved_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "sha256": sha256(data),
            "bytes": len(data),
            "license": license,
            "notes": notes,
            **(extra or {}),
        }
        self._write_manifest(manifest)
        return path

    # -- verification --------------------------------------------------
    def verify(self) -> list[str]:
        """Re-hash every manifest entry. Returns a list of problems (empty = clean)."""
        problems: list[str] = []
        manifest = self.read_manifest()
        files = manifest.get("files", {})
        if not files:
            return [f"{self.dir}: manifest lists no files"]
        for filename, entry in sorted(files.items()):
            path = self.dir / filename
            if not path.exists():
                problems.append(f"{filename}: listed in manifest but missing on disk")
                continue
            actual = sha256(path.read_bytes())
            if actual != entry.get("sha256"):
                problems.append(
                    f"{filename}: sha256 mismatch (manifest {entry.get('sha256')!r}, "
                    f"disk {actual!r}) -- raw data was modified after capture"
                )
        for path in sorted(self.dir.glob("*")):
            if path.name not in files and path.name != "manifest.json":
                problems.append(f"{path.name}: present on disk but absent from the manifest")
        return problems


def verify_all() -> dict[str, list[str]]:
    """Verify every snapshot directory. Returns {date: problems}."""
    results: dict[str, list[str]] = {}
    if not SNAPSHOT_DIR.exists():
        return results
    for d in sorted(SNAPSHOT_DIR.iterdir()):
        if not d.is_dir():
            continue
        try:
            date = dt.date.fromisoformat(d.name)
        except ValueError:
            continue
        problems = Snapshot(date).verify()
        if problems:
            results[d.name] = problems
    return results
