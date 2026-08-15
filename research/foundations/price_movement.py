"""S31.3 -- how the price has moved across the archive. DESCRIPTIVE (S2.2, S88).

S84 opens by naming what the intra-summer archive is for: "S31.3 (recency-weighted
ADP) and parts of S31.1 need intra-summer ADP history: how a player's price moved
across July and August." The archive has been capturing daily since 2026-08-13
and nothing read the series -- every board priced off `snapshot_date == max`, so
a player being drafted twelve picks earlier this week than last read exactly like
one who had not moved.

**This module reports the movement. It does not act on it.** S31.3's actual
research question is whether recency weighting predicts the next draft better,
and that needs a draft to score against -- S76's audit trail, which does not
exist until one is recorded. So the published mean stays the price, survival
keeps computing from it, and nothing here is folded into a value. A direction
printed beside a price is an observation about the market; a reweighted price
would be a claim about where a player will actually go, and S88 forbids making
one from a two-week analysis.

**The sign is the trap.** An ADP is a pick number, so a player getting more
expensive has a *falling* ADP. `adp_delta` is `now - prior`, which makes a
negative delta a player going earlier. Everything downstream reads that through
`direction()` rather than testing the sign itself, and a test pins it by name.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import polars as pl

# The span the delta is measured over when the archive is long enough to offer
# it. A week is the shortest span over which FFC's own rolling window turns over
# enough drafts for a move to be something other than the same drafts re-averaged
# -- the published window ran six days wide on the format this was written
# against.
LOOKBACK_DAYS = 7

# What counts as a move worth marking, as a fraction of a round.
#
# Half a round: six picks in both real profiles. Chosen for decision relevance
# and NOT from the observed distribution, which S80 prohibits -- half a round is
# roughly the granularity at which a drafter's plan changes, and expressing it in
# rounds makes it scale with league size instead of being a magic number. A
# 10-team league's round is shorter and so is its threshold.
MOVER_ROUND_FRACTION = 0.5

RISING = "rising"      # drafted earlier than before -- adp_delta < 0
FALLING = "falling"    # drafted later than before  -- adp_delta > 0


def mover_threshold(teams: int) -> float:
    """Picks of movement that count as a move, for a league of this size."""
    return round(int(teams) * MOVER_ROUND_FRACTION, 1)


def direction(delta: float | None) -> str | None:
    """RISING, FALLING, or None for no delta and for moves below the threshold's sign.

    Deliberately not a comparison callers write themselves. `adp_delta < 0` reads
    like a player getting worse and means the opposite.
    """
    if delta is None:
        return None
    if delta < 0:
        return RISING
    if delta > 0:
        return FALLING
    return None


def is_mover(delta: float | None, teams: int) -> bool:
    return delta is not None and abs(delta) >= mover_threshold(teams)


def prior_capture(history: pl.DataFrame, *, latest: dt.date) -> dt.date | None:
    """The capture the latest one is measured against.

    The most recent capture at or before `latest - LOOKBACK_DAYS`, and failing
    that the oldest capture there is. A two-day-old archive answers with its two
    days rather than with nothing -- but it says `span_days: 2`, because a delta
    labelled a week that is really two days is the same lie as a sheet whose
    generated date is current while the board under it is weeks old.
    """
    dates = sorted({d for d in history["snapshot_date"].to_list() if d is not None and d < latest})
    if not dates:
        return None
    cutoff = latest - dt.timedelta(days=LOOKBACK_DAYS)
    at_or_before = [d for d in dates if d <= cutoff]
    return at_or_before[-1] if at_or_before else dates[0]


def _window(frame: pl.DataFrame) -> dict[str, Any]:
    """The rolling window the source averaged over, for one capture."""
    if not frame.height:
        return {}
    row = frame.row(0, named=True)
    return {
        "window_start": str(row.get("window_start")) if row.get("window_start") else None,
        "window_end": str(row.get("window_end")) if row.get("window_end") else None,
        "total_drafts": row.get("total_drafts"),
    }


def unavailable(reason: str, *, latest: dt.date | None = None) -> dict[str, Any]:
    return {
        "available": False,
        "reason": reason,
        "latest_snapshot_date": str(latest) if latest else None,
        "lookback_days_requested": LOOKBACK_DAYS,
    }


def with_movement(
    latest: pl.DataFrame, history: pl.DataFrame, *, teams: int
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Attach `adp_delta` to the newest capture, plus what it was measured against.

    Joined on `source_player_id` -- Fantasy Football Calculator's own id, the same
    source on both sides, so S12's crosswalk is not involved and a player with no
    `gsis_id` still has a price that moved. Name and position are the fallback for
    a capture that predates an id, and an ambiguous name resolves to nothing.

    A player absent from the prior capture keeps a null delta. He has not moved
    from anywhere; he is new to the board, and marking him as a riser would be
    inventing the largest move on the page.
    """
    null_delta = pl.lit(None, dtype=pl.Float64).alias("adp_delta")
    if not latest.height:
        return latest.with_columns(null_delta), unavailable("no capture to price from")

    latest_date = latest["snapshot_date"].max()
    prior_date = prior_capture(history, latest=latest_date)
    if prior_date is None:
        return (
            latest.with_columns(null_delta),
            unavailable("the archive holds one capture, so nothing has moved yet",
                        latest=latest_date),
        )

    prior = history.filter(pl.col("snapshot_date") == prior_date)
    by_id = (
        prior.filter(pl.col("source_player_id").is_not_null())
        .select(pl.col("source_player_id"), pl.col("adp").alias("_id_prior"))
        .unique(subset="source_player_id", keep="none")
    )
    by_name = (
        prior.select(
            pl.col("source_player_name"),
            pl.col("position"),
            pl.col("adp").alias("_name_prior"),
        )
        .unique(subset=["source_player_name", "position"], keep="none")
    )
    joined = (
        latest.join(by_id, on="source_player_id", how="left")
        .join(by_name, on=["source_player_name", "position"], how="left")
        .with_columns(pl.coalesce("_id_prior", "_name_prior").alias("_prior_adp"))
        .with_columns((pl.col("adp") - pl.col("_prior_adp")).alias("adp_delta"))
        .drop("_id_prior", "_name_prior", "_prior_adp")
    )

    matched = joined.filter(pl.col("adp_delta").is_not_null()).height
    threshold = mover_threshold(teams)
    movers = joined.filter(pl.col("adp_delta").abs() >= threshold).height
    meta = {
        "available": True,
        "latest_snapshot_date": str(latest_date),
        "prior_snapshot_date": str(prior_date),
        "span_days": (latest_date - prior_date).days,
        "lookback_days_requested": LOOKBACK_DAYS,
        "mover_threshold_picks": threshold,
        "mover_threshold_rule": f"{MOVER_ROUND_FRACTION} of a round in a {teams}-team league",
        "matched_across_captures": matched,
        "unmatched_in_prior_capture": joined.height - matched,
        "movers": movers,
        # Both windows, not just both dates. FFC publishes a rolling average, so
        # two captures days apart share most of their underlying drafts and the
        # delta is damped -- how damped is readable only from these.
        "latest_window": _window(latest),
        "prior_window": _window(prior),
    }
    return joined, meta


ROLLING_WINDOW_LIMITATION = (
    "Price movement is the difference between two captures of a ROLLING WINDOW "
    "average, not between two same-day prices (S31.3). Consecutive captures share "
    "most of their underlying drafts, so a delta understates the movement and "
    "successive deltas are not independent; both windows are recorded in "
    "price_movement so the overlap is readable. It is descriptive: nothing here "
    "reweights the price or feeds survival, and whether recency weighting predicts "
    "the next draft better is unanswerable until S76 records one."
)
