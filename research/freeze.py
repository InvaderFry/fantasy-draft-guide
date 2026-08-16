"""Preserve the board a draft was actually made from (S7, S9, S76, S83).

S76's audit trail pairs what the sheet said with what happened, and it can only
do that while the sheet's artifacts still exist. The live board is the fixed
edition `2026-draft`, and the daily refresh regenerates it **in place**: the
survival artifact that priced a draft at 8pm is overwritten at 11:00 UTC the next
morning by one built from a later ADP capture.

That is not recoverable afterwards. `survival.py` prices off
``snapshot_date.max()`` and there is no as-of pin anywhere in the research layer,
so re-running the board the morning after produces different quotes and no
warning that they are different. The pairing still succeeds, the calibration
table still fills, and it is measuring a board nobody drafted from -- the same
"fails open, looks fine" shape `draft_record` already guards against at the level
of names, one level up.

So the board is copied, not rebuilt. S7 is explicit that past editions are not
overwritten, and S9 makes dated editions the archival scheme; this is that scheme
applied to the one edition that had been exempt from it.

**Copied, never regenerated.** Nothing here recomputes a board; the only thing
borrowed from the research layer is the method id that names the file, so the
freeze and the audit cannot drift into disagreeing about what a preserved board
consists of (the same rule `refresh.expected_sheets` follows against the
renderer).
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path
from typing import Any

from research.method import ARTIFACT_DIR
from research.refresh import STATE_FILENAME

# What a frozen edition holds. `methods` is the half S76 reads; `sheets` is the
# half a human reads, and it is copied too because an audit that cannot show the
# page the number came off is an audit nobody can check.
SUBDIRS = ("methods", "sheets")

PROVENANCE_FILENAME = "frozen_from.json"


class FrozenEditionExistsError(FileExistsError):
    """Raised on any attempt to rewrite a frozen edition (S7, S84).

    Mirrors `snapshot.SnapshotExistsError` deliberately and for its reason: an
    edition that can be rewritten is not a record of anything.
    """


class NothingToFreezeError(FileNotFoundError):
    """The live edition holds no board to preserve, or not the part that matters."""


def required_artifacts(profiles: list[dict[str, Any]] | None = None) -> list[str]:
    """What a frozen board must carry for S76 to be able to read it.

    Named from the survival method itself rather than restated here. The two
    carried-forward artifacts (S25, S21.1) describe completed seasons and are
    committed already; the survival artifact is the one that is regenerated daily,
    is what `draft_record._survival_quotes` opens, and is therefore the one whose
    absence makes a frozen edition an empty gesture.
    """
    from pipeline.config import real_profiles
    from research.foundations import survival as survival_mod

    chosen = real_profiles() if profiles is None else profiles
    return [f"{survival_mod.METHOD_ID}__{p['id']}.json" for p in chosen]


def frozen_name(source: str, profile_id: str, day: dt.date) -> str:
    """The name a draft's frozen board gets.

    Profile *and* date, not either alone. The two leagues draft on different
    nights against boards a day or more apart, so a name keyed only on the date
    would have the second draft refuse to freeze (the name is taken) or clobber
    the first (if it did not).
    """
    return f"{source}-{profile_id}-{day.isoformat()}"


def board_captures(source: str, root: Path | None = None) -> dict[str, str]:
    """The ADP capture date the live board is priced off, per profile.

    Read from `refresh_state.json`, which the refresh gate writes only on a run
    that passed -- so it describes the board that is actually on disk rather than
    the last one attempted.
    """
    path = (root or ARTIFACT_DIR) / source / STATE_FILENAME
    if not path.exists():
        return {}
    try:
        state = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return {
        pid: entry.get("adp_snapshot_date")
        for pid, entry in (state.get("profiles") or {}).items()
        if entry.get("adp_snapshot_date")
    }


def target(name: str, root: Path | None = None) -> Path:
    return (root or ARTIFACT_DIR) / name


def freeze(
    source: str,
    name: str,
    *,
    root: Path | None = None,
    dry_run: bool = False,
    today: dt.date | None = None,
    require: list[str] | None = None,
) -> Path:
    """Copy the live board into `name`, once and never again.

    Raises rather than returning a status, so a caller that ignores the result
    cannot proceed as though the board were preserved.
    """
    root = root or ARTIFACT_DIR
    src = root / source
    dst = target(name, root)

    present = [d for d in SUBDIRS if (src / d).is_dir() and any((src / d).iterdir())]
    wanted = required_artifacts() if require is None else require
    absent = [n for n in wanted if not (src / "methods" / n).exists()]
    if absent:
        raise NothingToFreezeError(
            f"{src}/methods is missing {', '.join(absent)}, so freezing it would "
            "preserve a board S76 cannot read. A directory that exists is not a board: "
            "the two carried-forward artifacts are committed year-round and the "
            "market-dependent ones are written by the daily refresh (S16, S83). Run "
            f"`research run-research --edition {source}` on a machine with the tables "
            "built, or check out a commit in which the refresh landed."
        )
    if dst.exists():
        raise FrozenEditionExistsError(
            f"{dst} already exists and a frozen edition is never rewritten (S7, S84). "
            "If this is the second league drafting on the same night, its board freezes "
            "under its own profile in the name; if it is a re-run of the same draft, the "
            "board is already preserved and there is nothing to do."
        )

    if dry_run:
        return dst

    dst.mkdir(parents=True)
    for sub in present:
        shutil.copytree(src / sub, dst / sub)
    (dst / PROVENANCE_FILENAME).write_text(
        json.dumps(
            {
                "source_edition": source,
                "frozen_on": (today or dt.datetime.now(dt.UTC).date()).isoformat(),
                "copied": list(present),
                # The number that decides whether this audit is worth anything.
                # Carried here as well as inside the artifacts so it can be read
                # without parsing 670 KB of survival output.
                "adp_snapshot_date": board_captures(source, root),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return dst


def provenance(name: str, root: Path | None = None) -> dict[str, Any] | None:
    path = target(name, root) / PROVENANCE_FILENAME
    if not path.exists():
        return None
    return json.loads(path.read_text())
