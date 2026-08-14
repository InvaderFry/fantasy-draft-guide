"""draft_pick: every pick of every league draft recorded (S13, S76, S10B).

Stacks the draft logs in ``data/snapshots/`` the way adp_history stacks the ADP
captures, and for the same reason: the table is a growing corpus, and its value
is in the accumulation rather than in any one row.

Two things it makes possible that nothing else in the repository can:

**S76's audit trail.** What the sheet recommended is in the artifacts; what
happened is here. Neither is a recommendation review on its own.

**An answer to S31.1, eventually.** Fantasy Football Calculator publishes a mean
and a spread and no percentiles, so S31.2's survival number is a labelled normal
approximation and says so in every artifact it writes. A full board -- every seat,
not just the drafter's -- is a real pick distribution. One draft settles nothing;
the point is that the corpus cannot start until something writes the first one,
and S10B names it as the one source needing no external access at all.

``value_type`` is ``observed``: a pick is a thing that happened.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import polars as pl

from pipeline.config import SNAPSHOT_DIR
from pipeline.features.assertions import assert_as_of_present
from pipeline.ingest import draft_log
from pipeline.normalize.player_ids import load_player_ids, match_external


def snapshot_files(root: Path = SNAPSHOT_DIR) -> list[tuple[dt.date, Path]]:
    """Every recorded draft across every snapshot date."""
    found: list[tuple[dt.date, Path]] = []
    if not root.exists():
        return found
    for day in sorted(root.iterdir()):
        if not day.is_dir():
            continue
        try:
            date = dt.date.fromisoformat(day.name)
        except ValueError:
            continue
        found.extend((date, path) for path in sorted(day.glob("draft_*.json")))
    return found


def parse_payload(payload: dict, *, snapshot_date: dt.date) -> list[dict]:
    """One row per pick, re-parsed from the stored text.

    Re-parsed rather than read from a stored pick list, deliberately. The paste
    is kept verbatim so that a parser which learns a new result shape improves
    every draft ever recorded, instead of only the ones taken after the fix.
    """
    teams = int(payload["teams"])
    drafter_slot = int(payload["draft_slot"])
    draft_date = dt.date.fromisoformat(payload["draft_date"])
    picks = draft_log.parse(
        payload["raw_text"], teams=teams, partial=bool(payload.get("partial"))
    )
    return [
        {
            "season": int(payload["season"]),
            "draft_date": draft_date,
            "snapshot_date": snapshot_date,
            # A pick is knowable the moment it is made, and not before. S6.1's
            # trio is what stops a draft-day fact being joined into a feature
            # frame for the season it belongs to.
            "as_of": draft_date,
            "source_as_of": draft_date,
            "value_type": "observed",
            "source": draft_log.SOURCE_NAME,
            "profile_id": str(payload["profile_id"]),
            "teams": teams,
            "overall_pick": pick["overall_pick"],
            "round": pick["round"],
            "slot": pick["slot"],
            # The whole reason the drawn seat is recorded: without it the log is
            # 180 picks by nobody in particular.
            "is_drafter": pick["slot"] == drafter_slot,
            "source_player_name": pick["source_player_name"],
            "position": pick["position"],
            "team": pick["team"],
            "parsed_as": pick["parsed_as"],
        }
        for pick in picks
    ]


def build(root: Path = SNAPSHOT_DIR, *, crosswalk: pl.DataFrame | None = None) -> pl.DataFrame:
    rows: list[dict] = []
    for date, path in snapshot_files(root):
        rows.extend(parse_payload(json.loads(path.read_bytes()), snapshot_date=date))

    if not rows:
        frame = pl.DataFrame(schema=_empty_schema())
        assert_as_of_present(frame, "draft_pick")
        return frame

    frame = pl.DataFrame(rows)
    xwalk = crosswalk if crosswalk is not None else load_player_ids()
    frame = match_external(frame, crosswalk=xwalk).rename({"gsis_id": "player_id"})
    assert_as_of_present(frame, "draft_pick")
    return frame.sort(["season", "profile_id", "overall_pick"])


def _empty_schema() -> dict[str, pl.DataType]:
    return {
        "season": pl.Int64, "draft_date": pl.Date, "snapshot_date": pl.Date,
        "as_of": pl.Date, "source_as_of": pl.Date, "value_type": pl.String,
        "source": pl.String, "profile_id": pl.String, "teams": pl.Int64,
        "overall_pick": pl.Int64, "round": pl.Int64, "slot": pl.Int64,
        "is_drafter": pl.Boolean, "player_id": pl.String,
        "source_player_name": pl.String, "position": pl.String, "team": pl.String,
        "parsed_as": pl.String, "match_method": pl.String, "match_confidence": pl.Float64,
    }
