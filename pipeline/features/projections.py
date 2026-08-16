"""projection_snapshot: stacked provider projections (S13, S11, S38.1).

Every projection payload in ``data/snapshots/`` is parsed and stacked, the same
way ``adp_history.py`` stacks ADP captures. Two rules from the spec are enforced
here rather than left to the caller:

**One row per provider per player, never an average.** S38.1: cross-provider
dispersion is the only available proxy for projection uncertainty, and averaging
providers into a consensus row destroys the only signal we have about how sure
anyone is. Consumers that want a single number take one deliberately.

**A projection is `derived`, not `observed`.** It is a model output that happens
to be knowable today. Its ``as_of`` is the snapshot date -- unlike an ADP window,
a projection carries no separate publication window to date it by -- so it is a
legal feature for the season it describes and needs no leakage exemption.

Projected points columns are deliberately prefixed (``projected_fantasy_points``,
``projected_games``) rather than carrying S11's bare canonical names, which are
already taken by ``schema.OUTCOME_COLUMNS``.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

import polars as pl

from pipeline.config import SNAPSHOT_DIR, projection_providers
from pipeline.features.assertions import assert_as_of_present
from pipeline.features.schema import PROJECTION_SNAPSHOT_COLUMNS
from pipeline.ingest import fantasypros, projections_csv
from pipeline.normalize.player_ids import load_player_ids, match_external

API_FILENAME = re.compile(
    r"^fantasypros_projections_(?P<position>[a-z]+)_(?P<season>\d{4})\.json$"
)
CSV_FILENAME = re.compile(r"^projections_(?P<provider>.+)\.csv$")


def snapshot_files(root: Path = SNAPSHOT_DIR) -> list[tuple[dt.date, Path]]:
    """Every projection payload across every snapshot date, oldest first."""
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
        for path in sorted(day.iterdir()):
            if API_FILENAME.match(path.name) or CSV_FILENAME.match(path.name):
                found.append((date, path))
    return found


def parse_file(date: dt.date, path: Path, *, season: int | None = None) -> list[dict[str, Any]]:
    """Route one payload to the adapter that wrote it."""
    api = API_FILENAME.match(path.name)
    if api:
        return fantasypros.parse(
            path.read_bytes(),
            snapshot_date=date,
            season=season or int(api.group("season")),
            position=api.group("position").upper(),
        )
    csv_match = CSV_FILENAME.match(path.name)
    if not csv_match:
        return []
    key = csv_match.group("provider")
    spec = projection_providers().get(key)
    if spec is None:
        # The export is in the archive but its column mapping has been removed
        # from config. Parsing it against a guessed mapping would produce a frame
        # of nulls that looks like a provider with no projections.
        raise KeyError(
            f"{path.name} is in the snapshot archive but provider {key!r} is no longer "
            "declared under `projection_providers` in config/sources.yaml, so its column "
            "mapping is unknown (S11)."
        )
    return projections_csv.parse(
        path.read_bytes(), spec, snapshot_date=date, season=season or date.year
    )


def build(root: Path = SNAPSHOT_DIR, *, crosswalk: pl.DataFrame | None = None) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for date, path in snapshot_files(root):
        rows.extend(parse_file(date, path))

    # The empty frame is asserted too, for the reason adp_history gives: an
    # unchecked empty return is how a schema drifts away from AS_OF_COLUMNS while
    # the archive is empty, making the first real capture the first failing build.
    if not rows:
        frame = pl.DataFrame(schema=_empty_schema()).select(PROJECTION_SNAPSHOT_COLUMNS)
        assert_as_of_present(frame, "projection_snapshot")
        return frame

    frame = pl.DataFrame(rows, infer_schema_length=None)
    xwalk = crosswalk if crosswalk is not None else load_player_ids()
    frame = match_external(frame, crosswalk=xwalk).rename({"gsis_id": "player_id"})
    frame = _conform(frame)
    assert_as_of_present(frame, "projection_snapshot")
    return frame


def _conform(frame: pl.DataFrame) -> pl.DataFrame:
    """Give every build the same columns, whichever provider it came from.

    A provider that projects no rushing does not get a table without a rushing
    column: the shape of `projection_snapshot` is S13's, not the shape of
    whichever export happened to land. Without this the frame silently follows
    the stat_map, and the empty-frame schema -- the one thing standing between a
    quiet drift and a failing build -- describes a table nothing produces.
    """
    empty = _empty_schema()
    unexpected = [c for c in frame.columns if c not in PROJECTION_SNAPSHOT_COLUMNS]
    if unexpected:
        raise ValueError(
            f"projection_snapshot rows carry column(s) {unexpected} that are not in S13's "
            "schema. Map them onto a canonical column in config/sources.yaml, or add them "
            "to PROJECTION_SNAPSHOT_COLUMNS deliberately."
        )
    missing = [
        pl.lit(None, dtype=empty[c]).alias(c)
        for c in PROJECTION_SNAPSHOT_COLUMNS
        if c not in frame.columns
    ]
    if missing:
        frame = frame.with_columns(missing)
    return frame.select(PROJECTION_SNAPSHOT_COLUMNS)


def coverage(frame: pl.DataFrame) -> dict[str, Any]:
    """How much of the board survived the ID join (S12, S37).

    Reported rather than absorbed. Unmatched rows skew fringe -- camp bodies and
    rookies whose crosswalk entry lags -- so a replacement level computed on the
    survivors sits slightly too high, and the tier board built on it is slightly
    too flat. dead_zone.py reports the same block for the same reason.
    """
    total = frame.height
    if not total:
        return {"rows": 0, "matched": 0, "matched_share": None, "providers": []}
    matched = frame.filter(pl.col("player_id").is_not_null()).height
    return {
        "rows": total,
        "matched": matched,
        "matched_share": round(matched / total, 4),
        "unmatched": total - matched,
        "providers": sorted(frame["provider_id"].unique().drop_nulls().to_list()),
        "snapshot_dates": sorted(str(d) for d in frame["snapshot_date"].unique().to_list()),
    }


def vintages(frame: pl.DataFrame) -> dict[str, dt.date]:
    """The newest capture date held for each provider (S38.1).

    Not the newest date overall: providers are captured on different cadences --
    the API board daily, a manual export once -- so one number cannot describe
    both. Cross-provider comparison and every report of it reads this.
    """
    if not frame.height or "provider_id" not in frame.columns:
        return {}
    newest = frame.group_by("provider_id").agg(pl.col("snapshot_date").max())
    return {
        str(row["provider_id"]): row["snapshot_date"]
        for row in newest.iter_rows(named=True)
        if row["provider_id"] is not None and row["snapshot_date"] is not None
    }


def latest(
    frame: pl.DataFrame,
    *,
    season: int | None = None,
    on_or_before: dt.date | None = None,
) -> pl.DataFrame:
    """The most recent capture per provider per player.

    Stacking every snapshot date is what makes the table an archive; a tier board
    wants today's price of today's opinion, not an eight-week smear of both.

    `on_or_before` pins the answer to a past day. S38.1 compares two providers
    captured on different cadences, and comparing each one's newest capture would
    measure the days between them as though they were disagreement. Pinning both
    to a shared date measures the providers.
    """
    if not frame.height:
        return frame
    if season is not None:
        frame = frame.filter(pl.col("season") == season)
    if on_or_before is not None:
        frame = frame.filter(pl.col("snapshot_date") <= on_or_before)
    if not frame.height:
        return frame
    return (
        frame.sort("snapshot_date")
        .group_by(["provider_id", "source_player_name", "position"], maintain_order=True)
        .last()
    )


def _empty_schema() -> dict[str, pl.DataType]:
    schema: dict[str, pl.DataType] = {
        "season": pl.Int64, "snapshot_date": pl.Date, "as_of": pl.Date,
        "source_as_of": pl.Date, "value_type": pl.String,
        "source": pl.String, "provider_id": pl.String, "transport": pl.String,
        "player_id": pl.String, "source_player_id": pl.String,
        "source_player_name": pl.String, "position": pl.String, "team": pl.String,
        "match_method": pl.String, "match_confidence": pl.Float64,
    }
    for stat in PROJECTION_SNAPSHOT_COLUMNS:
        schema.setdefault(stat, pl.Float64)
    return schema
