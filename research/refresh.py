"""The daily refresh must not publish a board worse than the one it replaces (S83, S84).

Every piece of the refresh path works as designed, and together they can destroy
the deliverable without anything going red:

  * `run_research` treats a blocked module as a finding and exits 0 -- correct,
    because S19.3 is blocked by design until its gates open;
  * `run_research` writes nothing for a blocked module, so whatever artifacts are
    already on disk are what `sheet.py` then renders;
  * `sheet.py` renders a section with no artifact behind it as BLOCKED --
    deliberately, because a blank space on a draft sheet is worse;
  * the workflow commits whatever changed under `artifacts/2026-draft`.

So the morning a projection key rotates, the job renders 26 pages whose TIERS and
SURVIVAL say BLOCKED and commits them over the good ones, at 11:00 UTC, with
nobody looking until the draft. The index banner cannot see it either: it
compares the ADP capture date, and ADP was fine.

**That morning now fails a second way, and the second is quieter than the first.**
The live board's method artifacts used to be gitignored, so a fresh runner started
with nothing and a blocked module produced BLOCKED pages -- loud, and caught by the
scan below. They are committed now (S76 cannot audit a board that survived
nowhere), so the runner checks out YESTERDAY's artifacts, a blocked module leaves
them untouched, and `sheet.py` renders 26 complete pages from them. Nothing is
blocked, no count has fallen, and the gate as originally written passes a board
that is silently a day old. `stale_boards` is the check that closes it: the
rendered board's ADP capture date against the newest capture in the S84 archive.
Equal is correct and common -- FFC publishes once a day and a day already in hand
is not a failure. Behind is the refresh having failed to pick up a capture that is
sitting in the repository, which is the signature of exactly this.

There is a third way to lose the board, and it is the quiet one: not rendering
it. A blocked-section scan reads the pages that exist and the counts come from
the artifacts rather than from the pages, so an edition with no pages at all
passes both -- nothing rendered means nothing blocked. `missing_sheets` counts
the filenames S83's own renderer would have written.

This module is the measurement that sits between the render and the commit. When
it refuses, the job reds and yesterday's complete sheets stay where they are,
already carrying the banner that says they are not today's. **Stale-but-complete
beats fresh-but-blocked** -- a sheet that admits it is two days old is still a
sheet somebody can draft from, and BLOCKED is not.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pipeline import archive
from pipeline.config import UnknownFormatError, draft_season, profile_adp_format
from research.method import ARTIFACT_DIR

# Sections that must carry content on the live board. TARGETS, DARTS and FALSE
# FRIENDS are honestly NOT BUILT (S79) and say so on the page; only BLOCKED means
# "this should have content and does not". AVOIDS is absent from the list because
# it degrades to NOT BUILT rather than to BLOCKED and so can never trip this.
SECTIONS_REQUIRING_CONTENT = ("TIERS", "REGRESSION", "SURVIVAL")

# How far a tracked count may fall before the refresh is treated as a downgrade.
#
# Twenty percent, relative, committed here with its reasoning rather than fitted
# to a run that has already happened (S80): a board that lost a fifth of its
# priced players lost something structural, not a couple of retirements. This is
# the half the floors cannot see -- 493 projected players arriving as 40, or 183
# priced arriving as 60, still renders a full-looking page.
MAX_DROP = 0.20

STATE_FILENAME = "refresh_state.json"

_SECTION = re.compile(r"<section><h3>(?P<name>[A-Z ]+?)\s*<span", re.S)
_BLOCKED = "<strong>BLOCKED.</strong>"


def blocked_sections(page: str) -> list[str]:
    """Sections of a rendered sheet that say BLOCKED where content is expected."""
    out = []
    for chunk in page.split("<section>")[1:]:
        m = _SECTION.match("<section>" + chunk)
        if m is None:
            continue
        name = m.group("name").strip()
        if name in SECTIONS_REQUIRING_CONTENT and _BLOCKED in chunk:
            out.append(name)
    return out


def sheets_dir(edition: str, root: Path | None = None) -> Path:
    return (root or ARTIFACT_DIR) / edition / "sheets"


def expected_sheets(profiles: list[dict[str, Any]]) -> list[str]:
    """Every filename a rendered edition must carry, from the same rule that
    writes them.

    Read from `sheet`'s own helpers rather than restated here, so the gate and
    the renderer cannot drift into disagreeing about what a complete edition is.
    """
    from research.sheet import sheet_targets

    return sorted(["index.html", *(name for name, _, _ in sheet_targets(profiles))])


def missing_sheets(
    edition: str, profiles: list[dict[str, Any]], root: Path | None = None
) -> list[str]:
    """Pages the edition should carry and does not.

    The blocked-section check reads the files that are there, which makes an
    edition with no files at all its healthiest possible state: nothing rendered
    means nothing blocked, and the gate would wave through a morning that
    produced no board. The counts it compares come from the S16 artifacts, not
    from the pages, so they cannot see it either -- a run whose artifacts are
    fine and whose render never happened passes on both.
    """
    if not profiles:
        return ["no real league profile to render a sheet for (S14)"]
    directory = sheets_dir(edition, root)
    absent = [name for name in expected_sheets(profiles) if not (directory / name).exists()]
    if not absent:
        return []
    return [
        f"{len(absent)} expected sheet(s) missing from {directory}: "
        + ", ".join(absent[:5])
        + (f" and {len(absent) - 5} more" if len(absent) > 5 else "")
    ]


def blocked_pages(edition: str, root: Path | None = None) -> dict[str, list[str]]:
    """Every rendered sheet in the edition that carries a blocked section."""
    directory = sheets_dir(edition, root)
    out: dict[str, list[str]] = {}
    if not directory.exists():
        return out
    for path in sorted(directory.glob("*.html")):
        if path.name == "index.html":  # a chooser, it carries no sections
            continue
        blocked = blocked_sections(path.read_text())
        if blocked:
            out[path.name] = blocked
    return out


def edition_metrics(
    artifacts: dict[str, dict[str, Any]], profiles: list[dict[str, Any]]
) -> dict[str, Any]:
    """What this refresh produced, per league. Read, never recomputed.

    Every number here already exists in the S16 artifacts the sheet was rendered
    from; this only lifts the ones whose collapse would not be visible on the
    page. A tier board with a tenth of its players prints twelve names a position
    exactly like a healthy one -- the tier list is capped at twelve.
    """
    out: dict[str, Any] = {}
    for profile in profiles:
        pid = profile["id"]
        entry: dict[str, Any] = {}
        tiers = (artifacts.get(f"tiers_and_replacement_level__{pid}") or {}).get(
            "primary_results"
        )
        if tiers:
            coverage = tiers.get("adp_coverage") or {}
            entry["board_rows"] = coverage.get("board_rows")
            entry["priced"] = coverage.get("priced")
            entry["priced_share"] = coverage.get("priced_share")
            entry["adp_snapshot_date"] = coverage.get("adp_snapshot_date")
            entry["tier_players"] = {
                pos: len(block.get("players") or [])
                for pos, block in (tiers.get("positions") or {}).items()
            }
        survival = (artifacts.get(f"survival_probability__{pid}") or {}).get("primary_results")
        if survival:
            entry["survival_slots"] = len(survival.get("by_slot") or [])
            entry["players_priced"] = survival.get("players_priced")
            entry["players_with_spread"] = survival.get("players_with_spread")
        out[pid] = entry
    return out


# Counts whose fall means the board thinned. `priced_share` is deliberately not
# here: it moves when the provider adds players the market has not quoted, which
# is a wider board rather than a worse one.
TRACKED = ("board_rows", "priced", "survival_slots", "players_priced", "players_with_spread")


def regressions(metrics: dict[str, Any], baseline: dict[str, Any] | None) -> list[str]:
    """What fell by more than MAX_DROP since the last refresh that passed.

    A profile the baseline does not know is skipped rather than failed: adding a
    league is not a regression, and neither is dropping one.
    """
    if not baseline:
        return []
    out = []
    for pid, now in metrics.items():
        before = (baseline.get("profiles") or {}).get(pid)
        if not before:
            continue
        for key in TRACKED:
            out.extend(_drop(pid, key, before.get(key), now.get(key)))
        was = before.get("tier_players") or {}
        for pos, count in (now.get("tier_players") or {}).items():
            out.extend(_drop(pid, f"tier_players.{pos}", was.get(pos), count))
    return out


def _drop(pid: str, key: str, before: Any, now: Any) -> list[str]:
    if not isinstance(before, (int, float)) or not before:
        return []
    if not isinstance(now, (int, float)):
        return [f"{pid}: {key} was {before} and this refresh produced nothing"]
    if now >= before * (1 - MAX_DROP):
        return []
    return [f"{pid}: {key} fell {before} -> {now} ({1 - now / before:.0%} down)"]


def stale_boards(
    metrics: dict[str, Any],
    profiles: list[dict[str, Any]],
    *,
    snapshot_root: Path | None = None,
) -> list[str]:
    """Leagues whose rendered board is older than a capture already in the archive.

    The one failure the count floors and the blocked-section scan both miss. A
    board re-rendered from yesterday's artifacts is complete, is the right shape,
    and carries every number the baseline expects -- it is only wrong about which
    day it is.

    Read from the archive rather than from the clock. "Older than today" would red
    every morning FFC has not published yet, which is a day in hand and not a day
    lost (the capture job draws the same distinction). Older than the newest
    capture the repository actually holds means the price was there to be read and
    this refresh did not read it.

    A profile whose format has no captures at all is skipped: that is the archive
    failing, which `archive-status` reports and this gate would only double. So is
    one whose format cannot be derived -- S14 owns that, `config.adp_capture_formats`
    asserts it over every profile, and a gate that red on it would report the same
    defect in a place nobody would think to look for it.
    """
    out = []
    for profile in profiles:
        pid = profile["id"]
        board = (metrics.get(pid) or {}).get("adp_snapshot_date")
        if not board:
            continue  # no tier artifact at all; the blocked scan owns that case
        try:
            key = (profile_adp_format(profile), int(profile["teams"]))
        except UnknownFormatError:
            continue
        dates = archive.series(draft_season(profile), snapshot_root).get(key)
        if not dates:
            continue
        newest = dates[-1]
        if str(board) < newest.isoformat():
            out.append(
                f"{pid}: board priced off {board} while the archive holds "
                f"{newest.isoformat()} -- this refresh rendered a board older than "
                "the capture it was given"
            )
    return out


def state_path(edition: str, root: Path | None = None) -> Path:
    return (root or ARTIFACT_DIR) / edition / STATE_FILENAME


def read_state(edition: str, root: Path | None = None) -> dict[str, Any] | None:
    path = state_path(edition, root)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        # A corrupt baseline must not red the refresh forever, and it must not be
        # silently trusted either. Treated as no baseline; the floors still hold.
        return None


def write_state(
    edition: str, metrics: dict[str, Any], *, generated: str, root: Path | None = None
) -> Path:
    """Record this refresh as the one to be no worse than.

    Written only on a run that passed, which is what makes the file a record of
    the last GOOD board rather than of the last board -- otherwise one bad
    morning becomes the new baseline and the degradation is permanent.
    """
    path = state_path(edition, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"edition": edition, "checked": generated, "profiles": metrics},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return path
