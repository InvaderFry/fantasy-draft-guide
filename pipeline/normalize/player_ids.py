"""Player ID crosswalk (S12).

``gsis_id`` is the canonical key. Nothing in the pipeline joins solely by
player name.

Two nflverse tables build the crosswalk:

* ``players.parquet`` -- the durable spine: gsis_id, pfr_id, espn_id, pff_id,
  esb_id, nfl_id, otc_id, smart_id, plus birth date, rookie season and draft
  capital.
* ``roster_<season>.parquet`` -- adds the fantasy-platform IDs the players
  table does not carry (``sleeper_id``, ``yahoo_id``, ``rotowire_id``,
  ``fantasy_data_id``, ``sportradar_id``), taken from the most recent season a
  player appears in.

Sources that publish none of these -- Fantasy Football Calculator, manual
projection exports -- are matched on a normalized name/position/team key, and
every such row is labelled with ``match_method`` and ``match_confidence``.
Corrections live in ``config/manual_id_overrides.yaml``, in version control,
and win over both.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from pipeline.config import PROCESSED_DIR, RAW_DIR, manual_id_overrides
from pipeline.normalize.names import match_key, name_position_key, normalize_name

NFLVERSE_DIR = RAW_DIR / "nflverse"
OUTPUT = PROCESSED_DIR / "player_ids.parquet"

# Platform IDs carried by the roster tables but not by players.parquet.
ROSTER_ID_COLUMNS = (
    "sleeper_id",
    "yahoo_id",
    "rotowire_id",
    "fantasy_data_id",
    "sportradar_id",
)


class CrosswalkError(RuntimeError):
    pass


def _roster_files() -> list[Path]:
    return sorted(NFLVERSE_DIR.glob("roster_*.parquet"))


def _platform_ids() -> pl.DataFrame:
    """Latest-season platform IDs per gsis_id."""
    files = _roster_files()
    if not files:
        return pl.DataFrame(
            schema={"gsis_id": pl.String, "season": pl.Int64,
                    **{c: pl.String for c in ROSTER_ID_COLUMNS}}
        )
    frames = []
    for path in files:
        lf = pl.scan_parquet(path)
        available = [c for c in ROSTER_ID_COLUMNS if c in lf.collect_schema().names()]
        frames.append(
            lf.select(
                pl.col("gsis_id").cast(pl.String),
                pl.col("season").cast(pl.Int64),
                *[pl.col(c).cast(pl.String) for c in available],
            )
        )
    stacked = pl.concat(frames, how="diagonal").collect()
    return (
        stacked.drop_nulls("gsis_id")
        .sort("season", descending=True)
        .unique(subset="gsis_id", keep="first")
    )


def build_player_ids(output: Path = OUTPUT) -> Path:
    """Write data/processed/player_ids.parquet."""
    players_path = NFLVERSE_DIR / "players.parquet"
    if not players_path.exists():
        raise CrosswalkError(
            f"{players_path} not found. Run `research ingest --datasets players` first (S10A)."
        )

    players = pl.read_parquet(players_path)
    spine_cols = [
        "gsis_id", "display_name", "position", "position_group", "birth_date",
        "rookie_season", "last_season", "latest_team", "years_of_experience",
        "draft_year", "draft_round", "draft_pick", "draft_team",
        "esb_id", "nfl_id", "pfr_id", "pff_id", "otc_id", "espn_id", "smart_id",
    ]
    spine = players.select([c for c in spine_cols if c in players.columns]).with_columns(
        pl.col("gsis_id").cast(pl.String)
    )

    crosswalk = spine.join(_platform_ids().drop("season"), on="gsis_id", how="left")

    crosswalk = crosswalk.with_columns(
        pl.col("display_name")
        .map_elements(normalize_name, return_dtype=pl.String)
        .alias("name_normalized"),
        pl.struct(["display_name", "position", "latest_team"])
        .map_elements(
            lambda s: match_key(s["display_name"], s["position"], s["latest_team"]),
            return_dtype=pl.String,
        )
        .alias("match_key"),
        pl.struct(["display_name", "position"])
        .map_elements(
            lambda s: name_position_key(s["display_name"], s["position"]),
            return_dtype=pl.String,
        )
        .alias("name_position_key"),
    )

    crosswalk = _apply_overrides(crosswalk)

    output.parent.mkdir(parents=True, exist_ok=True)
    crosswalk.write_parquet(output)
    return output


def _apply_overrides(crosswalk: pl.DataFrame) -> pl.DataFrame:
    """Attach manual corrections as explicit alias rows (S12)."""
    overrides = manual_id_overrides()
    if not overrides:
        return crosswalk.with_columns(pl.lit(None, dtype=pl.String).alias("override_source"))

    rows = []
    for o in overrides:
        rows.append(
            {
                "gsis_id": o["gsis_id"],
                "override_source": o.get("source"),
                "override_match_key": match_key(
                    o.get("source_name"), o.get("position"), o.get("source_team")
                ),
            }
        )
    ov = pl.DataFrame(rows)
    return crosswalk.join(ov, on="gsis_id", how="left")


def load_player_ids(path: Path = OUTPUT) -> pl.DataFrame:
    if not path.exists():
        raise CrosswalkError(f"{path} not found. Run `research normalize-ids` first (S12).")
    return pl.read_parquet(path)


def match_external(
    frame: pl.DataFrame,
    *,
    name_col: str = "source_player_name",
    position_col: str = "position",
    team_col: str = "team",
    crosswalk: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Attach ``gsis_id`` to rows from a source with no shared ID (S12).

    Tries, in order: a manual override, name+position+team, then
    name+position. Every row keeps ``match_method`` and ``match_confidence``,
    and unmatched rows are returned with a null ``gsis_id`` rather than dropped
    -- ``unmatched_report`` surfaces them.
    """
    xwalk = crosswalk if crosswalk is not None else load_player_ids()

    frame = frame.with_columns(
        pl.struct([name_col, position_col, team_col])
        .map_elements(
            lambda s: match_key(s[name_col], s[position_col], s[team_col]),
            return_dtype=pl.String,
        )
        .alias("_match_key"),
        pl.struct([name_col, position_col])
        .map_elements(
            lambda s: name_position_key(s[name_col], s[position_col]),
            return_dtype=pl.String,
        )
        .alias("_name_position_key"),
    )

    override_map = (
        xwalk.filter(pl.col("override_match_key").is_not_null())
        .select(pl.col("override_match_key").alias("_match_key"), pl.col("gsis_id").alias("_ov_id"))
        .unique(subset="_match_key")
        if "override_match_key" in xwalk.columns
        else pl.DataFrame(schema={"_match_key": pl.String, "_ov_id": pl.String})
    )
    strict = (
        xwalk.select(pl.col("match_key").alias("_match_key"), pl.col("gsis_id").alias("_strict_id"))
        .unique(subset="_match_key", keep="none")  # ambiguous keys resolve to nothing
    )
    loose = (
        xwalk.select(
            pl.col("name_position_key").alias("_name_position_key"),
            pl.col("gsis_id").alias("_loose_id"),
        )
        .unique(subset="_name_position_key", keep="none")
    )

    joined = (
        frame.join(override_map, on="_match_key", how="left")
        .join(strict, on="_match_key", how="left")
        .join(loose, on="_name_position_key", how="left")
    )

    return joined.with_columns(
        pl.coalesce("_ov_id", "_strict_id", "_loose_id").alias("gsis_id"),
        pl.when(pl.col("_ov_id").is_not_null())
        .then(pl.lit("manual_override"))
        .when(pl.col("_strict_id").is_not_null())
        .then(pl.lit("name_position_team"))
        .when(pl.col("_loose_id").is_not_null())
        .then(pl.lit("name_position"))
        .otherwise(pl.lit("unmatched"))
        .alias("match_method"),
        pl.when(pl.col("_ov_id").is_not_null())
        .then(pl.lit(1.0))
        .when(pl.col("_strict_id").is_not_null())
        .then(pl.lit(0.9))
        .when(pl.col("_loose_id").is_not_null())
        .then(pl.lit(0.6))
        .otherwise(pl.lit(0.0))
        .alias("match_confidence"),
    ).drop("_ov_id", "_strict_id", "_loose_id", "_match_key", "_name_position_key")


def unmatched_report(frame: pl.DataFrame, *, name_col: str = "source_player_name") -> pl.DataFrame:
    """Rows that failed to match, for triage into manual_id_overrides.yaml."""
    return (
        frame.filter(pl.col("match_method") == "unmatched")
        .select([c for c in (name_col, "position", "team", "adp") if c in frame.columns])
        .unique()
        .sort(name_col)
    )
