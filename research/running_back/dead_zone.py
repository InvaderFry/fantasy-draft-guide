"""S21.1 -- RB dead-zone ADP bucket hit rates. DESCRIPTIVE ONLY (S2.2, S88).

The question: at each slice of draft price, how often did a running back
return a high-end season, and how does that compare with the wide receivers
going at the same price?

**What this module deliberately does not do.** S21.1 also defines a
`dead_zone_score` -- a composite of four z-scored rates -- and a robustness
matrix across bucket sizes, scoring formats and eras. Both are modelling, and
S88's entry for this analysis is explicit: "Observed rates by bucket. No
modelling, no evidence grades, no exception research -- the sample will not
support it here." The registry's kill rule repeats it. So this computes
observed rates, and S4's reporting for a rate comparison -- both hit rates,
the absolute percentage-point gap, risk ratio, odds ratio, an interval and n
-- and stops there.

Outcome definitions come from config/outcomes.yaml and are named in the
artifact, per S15. They are not restated here.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from pipeline.config import PROCESSED_DIR
from research import outcomes as outcome_lib
from research import stats
from research.method import MethodArtifact

METHOD_ID = "rb_dead_zone_bucket_rates"
VERSION = "1.0.0"

# S21.1: "Create ADP buckets, e.g. 1-12, 13-24, 25-36, 37-48, etc." -- a round
# in a 12-team league. Alternative widths are Robustness, which S88 removes.
BUCKET_SIZE = 12
MAX_BUCKET_PICK = 180  # beyond round 15 the sample is noise and nobody is drafting

# The format the placeholder profiles and most redraft leagues use. Comparing
# formats is Robustness, so one is chosen and named rather than swept.
DEFAULT_FORMAT = "half-ppr"
DEFAULT_TEAMS = 12

POSITION_OUTCOMES = {
    "RB": ("rb_high_end", "rb_usable"),
    "WR": ("wr_high_end", "wr_usable"),
    "TE": ("te_high_end",),
}

# Kickers and team defences are drafted and have an ADP, but they are out of
# scope for every S88 analysis and have no scoring rules configured. Excluded
# by name so they are not silently lost in a join and mistaken for attrition.
ADP_POSITIONS_OUT_OF_SCOPE = ("PK", "K", "DEF", "DST")


def population(
    processed_dir=PROCESSED_DIR,
    *,
    adp_format: str = DEFAULT_FORMAT,
    teams: int = DEFAULT_TEAMS,
) -> pl.DataFrame:
    """One row per drafted player per season: their price, and how they finished.

    A season contributes only if it has both an ADP capture and a completed
    outcome, so the current season is excluded -- it has a price and no result.
    """
    priced = pl.read_parquet(processed_dir / "adp_history.parquet").filter(
        (pl.col("format") == adp_format)
        & (pl.col("teams") == teams)
        & (pl.col("adp") <= MAX_BUCKET_PICK)
        & ~pl.col("position").is_in(ADP_POSITIONS_OUT_OF_SCOPE)
    )
    if priced.height == 0:
        return priced
    adp = priced.filter(pl.col("player_id").is_not_null())

    # One price per player-season: the latest window that closed. Historical
    # captures give one per season anyway; a live season may have many.
    adp = (
        adp.sort("window_end", "snapshot_date")
        .group_by(["season", "player_id"])
        .last()
        .select("season", "player_id", "adp", "window_end", "position")
        .rename({"position": "adp_position"})
    )

    results = pl.read_parquet(processed_dir / "player_season_outcomes.parquet")
    for name in {n for names in POSITION_OUTCOMES.values() for n in names}:
        results = outcome_lib.evaluate(results, name)

    joined = adp.join(results, on=["season", "player_id"], how="inner")
    if joined.height == 0:
        return joined

    # Attrition is carried on the frame rather than left implicit. Every player
    # dropped between "was drafted" and "has a result" is one the hit-rate
    # denominator loses, and the ones that drop are disproportionately the
    # fringe players who bust -- so silent attrition biases every rate upward.
    #
    # Measured only over seasons that have finished: a current-season price with
    # no result yet is not attrition, it is a season still being played.
    scored_seasons = joined["season"].unique().to_list()
    in_scope = priced.filter(pl.col("season").is_in(scored_seasons))
    matched = adp.filter(pl.col("season").is_in(scored_seasons))
    joined = joined.with_columns(
        pl.lit(in_scope.height).alias("_priced_in_scope"),
        pl.lit(in_scope.filter(pl.col("player_id").is_null()).height).alias("_unmatched_id"),
        pl.lit(matched.height - joined.height).alias("_no_outcome_row"),
    )
    return joined.with_columns(
        (((pl.col("adp") - 1) // BUCKET_SIZE) + 1).cast(pl.Int64).alias("bucket"),
    ).with_columns(
        (
            pl.format(
                "{}-{}",
                (pl.col("bucket") - 1) * BUCKET_SIZE + 1,
                pl.col("bucket") * BUCKET_SIZE,
            )
        ).alias("bucket_label")
    )


def compute(frame: pl.DataFrame) -> dict[str, Any]:
    if frame.height == 0:
        return {"n": 0, "seasons": [], "buckets": [], "rb_vs_wr": []}
    seasons = sorted(frame["season"].unique().to_list())
    return {
        "n": int(frame.height),
        "seasons": [seasons[0], seasons[-1]],
        "season_list": seasons,
        "bucket_size": BUCKET_SIZE,
        "coverage": _coverage(frame),
        "buckets": _bucket_rates(frame),
        "rb_vs_wr": [_compare(frame, b) for b in sorted(frame["bucket"].unique().to_list())],
    }


def _coverage(frame: pl.DataFrame) -> dict[str, Any]:
    """What the denominator lost on the way in, stated rather than absorbed."""
    priced = int(frame["_priced_in_scope"][0])
    unmatched = int(frame["_unmatched_id"][0])
    no_outcome = int(frame["_no_outcome_row"][0])
    return {
        "drafted_players_in_scope": priced,
        "dropped_no_id_match": unmatched,
        "dropped_no_outcome_row": no_outcome,
        "analysed": int(frame.height),
        "retained_share": _round(frame.height / priced) if priced else None,
        "note": (
            "Players lost here are disproportionately fringe, so every rate below is "
            "an upper bound by roughly the share dropped (S12 name matching)."
        ),
    }


def _bucket_rates(frame: pl.DataFrame) -> list[dict[str, Any]]:
    """Observed rates per ADP bucket per position -- the reader-facing table."""
    rows: list[dict[str, Any]] = []
    for bucket in sorted(frame["bucket"].unique().to_list()):
        slice_ = frame.filter(pl.col("bucket") == bucket)
        for position, names in POSITION_OUTCOMES.items():
            at_position = slice_.filter(pl.col("position") == position)
            if at_position.height == 0:
                continue
            entry: dict[str, Any] = {
                "bucket": bucket,
                "bucket_label": at_position["bucket_label"][0],
                "position": position,
                "n": int(at_position.height),
                "mean_ppg": _round(at_position["fantasy_ppg_active"].mean()),
                "mean_games": _round(at_position["games"].mean()),
            }
            for name in names:
                hits = int(at_position[name].sum() or 0)
                rate, low, high = stats.proportion_ci(hits, at_position.height)
                entry[name] = {
                    "hits": hits, "rate": _round(rate),
                    "ci_low": _round(low), "ci_high": _round(high),
                }
            rows.append(entry)
    return rows


def _compare(frame: pl.DataFrame, bucket: int) -> dict[str, Any]:
    """S4's reporting for a binary feature against a binary outcome."""
    slice_ = frame.filter(pl.col("bucket") == bucket)
    rb = slice_.filter(pl.col("position") == "RB")
    wr = slice_.filter(pl.col("position") == "WR")
    if rb.height == 0 or wr.height == 0:
        return {"bucket": bucket, "n_rb": rb.height, "n_wr": wr.height, "comparable": False}

    rb_hits = int(rb["rb_high_end"].sum() or 0)
    wr_hits = int(wr["wr_high_end"].sum() or 0)
    rb_rate, rb_low, rb_high = stats.proportion_ci(rb_hits, rb.height)
    wr_rate, wr_low, wr_high = stats.proportion_ci(wr_hits, wr.height)
    return {
        "bucket": bucket,
        "bucket_label": slice_["bucket_label"][0],
        "comparable": True,
        "n_rb": int(rb.height),
        "n_wr": int(wr.height),
        "rb_high_end_rate": _round(rb_rate),
        "rb_ci": [_round(rb_low), _round(rb_high)],
        "wr_high_end_rate": _round(wr_rate),
        "wr_ci": [_round(wr_low), _round(wr_high)],
        "absolute_difference_pp": _round((rb_rate - wr_rate) * 100, 2),
        "risk_ratio": _round(rb_rate / wr_rate) if wr_rate else None,
        # Neither position hit: there is no association to estimate, and the
        # continuity correction would report one anyway off the denominators.
        "odds_ratio": (
            None
            if rb_hits == 0 and wr_hits == 0
            else _round(_odds_ratio(rb_hits, rb.height, wr_hits, wr.height))
        ),
    }


def _odds_ratio(a_hits: int, a_n: int, b_hits: int, b_n: int) -> float | None:
    # Haldane-Anscombe: a zero cell is common in these bucket sizes and would
    # otherwise make the ratio undefined rather than merely uncertain.
    a, b = a_hits + 0.5, a_n - a_hits + 0.5
    c, d = b_hits + 0.5, b_n - b_hits + 0.5
    return (a / b) / (c / d)


def _round(value: Any, places: int = 4) -> float | None:
    return None if value is None else round(float(value), places)


def export(frame: pl.DataFrame, results: dict[str, Any]) -> MethodArtifact:
    return MethodArtifact(
        method_id=METHOD_ID,
        title="Running back dead zone -- observed hit rates by ADP bucket",
        version=VERSION,
        claim_type="DESCRIPTIVE",
        spec_sections=["S21.1", "S4", "S15"],
        population={
            "adp_format": DEFAULT_FORMAT,
            "teams": DEFAULT_TEAMS,
            "seasons": results.get("seasons", []),
            "bucket_size": BUCKET_SIZE,
        },
        outcome="rb_high_end",
        sample_size=results.get("n", 0),
        primary_results=results,
        limitations=[
            "DESCRIPTIVE only (S88): observed rates, no model, no evidence grade, no "
            "dead_zone_score, no robustness sweep, no exception research (S21.2).",
            "Fantasy Football Calculator serves historical ADP only from 2018, not from "
            "2007 as S10B states -- 2015 and 2017 return empty. The window is therefore "
            "8 seasons, against the 14 S5.1 assumed when it estimated n~175 for the "
            "37-72 band, so every bucket is smaller than the power note anticipated.",
            "Historical ADP is a single final-preseason value per season, not the price "
            "on any particular draft day, and its window closes a few days after the "
            "decision date in config/decision_dates.yaml.",
            "ADP is the Fantasy Football Calculator draft population, not the whole "
            "fantasy market (S10B).",
            "rb_high_end is a season-total positional finish, so it is unaffected by the "
            "pre-2020 gap in games_missed_injury. An availability-conditional variant "
            "would not be, and none is reported here.",
            "Roughly 3% of drafted players carry no ID match and leave the denominator; "
            "they skew fringe, so every rate here is a slight upper bound (see coverage).",
        ],
        sources=["Fantasy Football Calculator ADP API", "nflverse player stats"],
    )


def run(processed_dir=PROCESSED_DIR) -> tuple[dict[str, Any], MethodArtifact]:
    frame = population(processed_dir)
    results = compute(frame)
    return results, export(frame, results)
