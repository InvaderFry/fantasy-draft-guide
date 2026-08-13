"""adp_history: stacked ADP snapshots (S13, S31.1).

Every capture in ``data/snapshots/`` is parsed and stacked, so the table grows
into the intra-summer price-movement series that S31.3 needs and that cannot be
reconstructed after the fact.

Distribution columns (``pick_p10`` ... ``pick_p90``) are left null when the
source does not publish them. Approximating a distribution from a mean happens
at analysis time and must be labelled there (S31.2) -- never silently in the
table.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import polars as pl

from pipeline.config import SNAPSHOT_DIR
from pipeline.features.assertions import assert_as_of_present
from pipeline.ingest import ffc_adp
from pipeline.normalize.player_ids import load_player_ids, match_external

FFC_FILENAME = re.compile(
    r"^ffc_adp_(?P<format>[a-z0-9\-]+)_(?P<teams>\d+)team_(?P<year>\d{4})\.json$"
)


def snapshot_files(root: Path = SNAPSHOT_DIR) -> list[tuple[dt.date, Path, dict[str, str]]]:
    """Every FFC payload across every snapshot date."""
    found: list[tuple[dt.date, Path, dict[str, str]]] = []
    if not root.exists():
        return found
    for day in sorted(root.iterdir()):
        if not day.is_dir():
            continue
        try:
            date = dt.date.fromisoformat(day.name)
        except ValueError:
            continue
        for path in sorted(day.glob("ffc_adp_*.json")):
            m = FFC_FILENAME.match(path.name)
            if m:
                found.append((date, path, m.groupdict()))
    return found


def build(root: Path = SNAPSHOT_DIR, *, crosswalk: pl.DataFrame | None = None) -> pl.DataFrame:
    rows: list[dict] = []
    for date, path, meta in snapshot_files(root):
        rows.extend(
            ffc_adp.parse(
                path.read_bytes(),
                snapshot_date=date,
                fmt=meta["format"],
                teams=int(meta["teams"]),
                year=int(meta["year"]),
            )
        )
    # The empty frame is asserted too. Returning it unchecked is what let the
    # schema drift away from AS_OF_COLUMNS while the archive was still empty:
    # the first real capture would have been the first failing build.
    if not rows:
        frame = pl.DataFrame(schema=_empty_schema())
        assert_as_of_present(frame, "adp_history")
        return frame

    frame = pl.DataFrame(rows)
    xwalk = crosswalk if crosswalk is not None else load_player_ids()
    frame = match_external(frame, crosswalk=xwalk).rename({"gsis_id": "player_id"})
    frame = frame.with_columns(
        # `season` belongs in the partition: a backfill captures many seasons on
        # one snapshot_date, and without it 2015 and 2025 interleave into a
        # single positional ranking.
        pl.col("adp")
        .rank("ordinal")
        .over(["season", "snapshot_date", "format", "teams", "position"])
        .cast(pl.Int64)
        .alias("position_adp")
    )
    assert_as_of_present(frame, "adp_history")
    return frame


def distribution_availability(root: Path = SNAPSHOT_DIR) -> dict[str, list[str]]:
    """Answer S31.1 from the archive: which distribution fields each capture carried."""
    out: dict[str, list[str]] = {}
    for date, path, _meta in snapshot_files(root):
        payload = json.loads(path.read_bytes())
        out[f"{date.isoformat()}/{path.name}"] = sorted(
            ffc_adp.distribution_fields_present(payload)
        )
    return out


def _empty_schema() -> dict[str, pl.DataType]:
    return {
        "season": pl.Int64, "snapshot_date": pl.Date, "as_of": pl.Date,
        "source_as_of": pl.Date, "window_start": pl.Date, "window_end": pl.Date,
        "total_drafts": pl.Int64, "source": pl.String,
        "format": pl.String, "teams": pl.Int64, "player_id": pl.String,
        "source_player_id": pl.String, "source_player_name": pl.String,
        "position": pl.String, "team": pl.String, "bye": pl.Int64, "adp": pl.Float64,
        "position_adp": pl.Int64, "sample_size_if_available": pl.Int64,
        "adp_stdev": pl.Float64, "pick_high": pl.Float64, "pick_low": pl.Float64,
        "pick_p10": pl.Float64, "pick_p25": pl.Float64,
        "pick_p50": pl.Float64, "pick_p75": pl.Float64, "pick_p90": pl.Float64,
        "n_drafts": pl.Int64, "match_method": pl.String, "match_confidence": pl.Float64,
        "value_type": pl.String,
    }
