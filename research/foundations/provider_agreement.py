"""S38.1 -- how far apart two providers are on the same player. DESCRIPTIVE (S2.2, S88).

S38.1 opens on the uncertainty nothing in this repository could measure: "Every
'research fair value' in the guide is a provider projection plus an adjustment.
The provider number carries error, and because historical preseason projection
archives are unavailable (S66), that error cannot be measured." Until a second
provider was archived, `tiers.provider_dispersion` said so honestly and uselessly
-- "the board carries no error bar" -- and a player one provider likes far more
than the other printed exactly like one they agree on, at a draft table, under a
pick clock.

The treatment is S38.1's own, thresholds included:

    provider_spread   = max(points) - min(points)
    provider_cv       = stdev(points) / mean(points)
    provider_agreement = high if cv < 0.08 else medium if cv < 0.15 else low

Those two constants come from the spec rather than from this data, which is what
makes them legitimate: S80 prohibits choosing a threshold after seeing what it
produces, and these were chosen before anyone here had two providers to look at.

**Never averaged.** S38.1 and S80 both forbid folding providers into a consensus
row -- the spread IS the signal and a mean destroys it. The second opinion is
CARRIED beside the board exactly as the market price is, and S19.3's value metric
is computed as though this module did not exist.

Two refusals stand between the formula and the page, and without either one the
number silently measures something else:

**Comparability.** `pipeline.scoring.points_expr` scores over the columns a frame
carries and does `fill_null(0)` on each; `projections._conform` then gives every
provider all of S13's columns whether it published them or not. So a provider that
omits fumbles-lost -- a NEGATIVE term -- scores systematically HIGHER than one that
publishes it, and a provider that omits receptions scores catastrophically lower
under PPR. Those are mapping artifacts and they compute cleanly, arriving as a
confident LOW against every player on the board. `populated_scored_stats` refuses
the comparison when the two do not populate the same scored columns.

**Vintage.** A manual export is captured once (S11 option 1B) and the API board is
re-captured daily, so their newest captures are days apart and the difference
between them is partly the calendar. The comparison is pinned to a date both
providers were captured on or before, which measures the providers rather than the
days; the board itself still prices off today. Past `MAX_VINTAGE_GAP_DAYS` even the
pinned comparison is too old to describe today's board, and nothing is reported.
"""

from __future__ import annotations

import datetime as dt
import statistics
from typing import Any

import polars as pl

from pipeline.normalize.names import name_position_key
from pipeline.scoring import STAT_TO_RULE

# S38.1, verbatim. Committed in the specification before this repository held two
# providers to fit them against, which is the property S80 asks for.
AGREEMENT_CV_HIGH = 0.08
AGREEMENT_CV_MEDIUM = 0.15

HIGH = "high"
MEDIUM = "medium"
LOW = "low"

# How far behind the board a pinned comparison may sit and still describe it.
#
# One preseason week: a full round of preseason games and one injury cycle -- the
# shortest span over which a provider's board moves for reasons that are not
# disagreement. Chosen for that reason and not from the observed gaps, which S80
# prohibits.
MAX_VINTAGE_GAP_DAYS = 7


def agreement_label(cv: float | None) -> str | None:
    """S38.1's three-way label. The only place the thresholds are read."""
    if cv is None:
        return None
    if cv < AGREEMENT_CV_HIGH:
        return HIGH
    if cv < AGREEMENT_CV_MEDIUM:
        return MEDIUM
    return LOW


def is_low(label: str | None) -> bool:
    """Whether S38.1's Use table has anything to say about this player.

    MEDIUM and HIGH both get "normal treatment", so only LOW is ever marked. A
    mark that appears against every row is not a mark.
    """
    return label == LOW


def populated_scored_stats(frame: pl.DataFrame, profile: dict[str, Any]) -> set[str]:
    """Which scored stat columns this provider actually publishes.

    `_conform` gives every provider every column, so presence in `frame.columns`
    says nothing; a column is populated when some row of it is not null.

    Only columns the profile actually scores count. A provider omitting a stat the
    league does not pay for cannot move its own total, and counting it here would
    refuse a comparison that is perfectly sound.
    """
    if not frame.height:
        return set()
    rules = profile.get("scoring") or {}
    return {
        stat
        for stat, rule in STAT_TO_RULE.items()
        if stat in frame.columns and rules.get(rule) and frame[stat].drop_nulls().len() > 0
    }


def unavailable(reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "measurable": False,
        "reason": reason,
        "cv_thresholds": {"high_below": AGREEMENT_CV_HIGH, "medium_below": AGREEMENT_CV_MEDIUM},
        **extra,
    }


NO_SECOND_PROVIDER = (
    "one provider archived, so projection uncertainty cannot be estimated "
    "from cross-provider spread (S38.1). The board carries no error bar."
)


def _name_key() -> pl.Expr:
    return (
        pl.struct("source_player_name", "position")
        .map_elements(
            lambda s: name_position_key(s["source_player_name"], s["position"]),
            return_dtype=pl.String,
        )
        .alias("_name_key")
    )


def _spread_and_cv(a: float | None, b: float | None) -> tuple[float | None, float | None]:
    """S38.1's two statistics for one player, over the providers that priced him."""
    points = [p for p in (a, b) if p is not None]
    if len(points) < 2:
        return None, None
    spread = max(points) - min(points)
    mean = statistics.fmean(points)
    if mean <= 0:
        # A projected total of zero or less has no meaningful relative spread,
        # and dividing by it would manufacture the largest disagreement on the
        # board out of the least interesting player on it.
        return round(spread, 1), None
    return round(spread, 1), round(statistics.stdev(points) / mean, 4)


def _attach_points(left: pl.DataFrame, right: pl.DataFrame, alias: str) -> pl.DataFrame:
    """Carry one frame's `projected_points` onto another's rows, as `alias`.

    Joined on `player_id` first: both frames come out of `projection_snapshot`, so
    it is S12's `gsis_id` under the same name on both sides. Then on normalized
    name and position for the remainder -- providers spell differently ("Marvin
    Harrison Jr." against "Marvin Harrison"), and joining on the raw string would
    quietly compare only the subset that happens to agree about punctuation and
    then report that subset as coverage.

    The loose key has to be unambiguous on BOTH sides (`keep="none"`, plus the row
    count over the left key): the board itself can carry two players sharing a
    name and a position, and one row of the other frame would be attached to both.
    """
    right = right.with_columns(_name_key())
    by_id = (
        right.filter(pl.col("player_id").is_not_null())
        .select(pl.col("player_id"), pl.col("projected_points").alias("_id_pts"))
        .unique(subset="player_id", keep="none")
    )
    by_name = (
        right.select(pl.col("_name_key"), pl.col("projected_points").alias("_name_pts"))
        .unique(subset="_name_key", keep="none")
    )
    return (
        left.with_columns(_name_key())
        .with_columns(pl.len().over("_name_key").alias("_name_key_rows"))
        .join(by_id, on="player_id", how="left")
        .join(by_name, on="_name_key", how="left")
        .with_columns(
            pl.when(pl.col("_name_key_rows") > 1)
            .then(pl.col("_id_pts"))
            .otherwise(pl.coalesce("_id_pts", "_name_pts"))
            .alias(alias)
        )
        .drop("_id_pts", "_name_pts", "_name_key", "_name_key_rows")
    )


def with_agreement(
    board: pl.DataFrame,
    other: pl.DataFrame,
    *,
    profile: dict[str, Any],
    board_as_of: dt.date | None = None,
    board_at_comparison: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Attach S38.1's spread, cv and label to the board, plus what was compared.

    `board` is the board as displayed -- today's capture, the rows the sheet
    prints. `other` is the second provider, and `board_at_comparison` is the
    board provider's OWN capture at the same date `other` was taken, when the
    archive holds one.

    That third frame is the whole vintage argument. A manual export is captured
    once (S11 option 1B) and the API board daily, so their newest captures are
    days apart; comparing them directly measures the days as though they were
    disagreement. Comparing each provider's capture from the SAME day measures the
    providers. The board still prices off today either way -- only the comparison
    moves back.

    A player the second provider does not price keeps his row with a null label.
    He is unexamined, not agreed with, and dropping him would change how many
    players sit above replacement, which is what the board is measured from.
    """
    null_cols = (
        pl.lit(None, dtype=pl.Float64).alias("other_projected_points"),
        pl.lit(None, dtype=pl.Float64).alias("provider_spread"),
        pl.lit(None, dtype=pl.Float64).alias("provider_cv"),
        pl.lit(None, dtype=pl.String).alias("provider_agreement"),
    )
    if not board.height:
        return board.with_columns(*null_cols), unavailable("no board to compare against")
    if not other.height:
        return board.with_columns(*null_cols), unavailable(NO_SECOND_PROVIDER)

    board_stats = populated_scored_stats(board, profile)
    other_stats = populated_scored_stats(other, profile)
    if board_stats != other_stats:
        return board.with_columns(*null_cols), unavailable(
            "the two providers do not publish the same stats this league scores, so their "
            "point totals are not comparable (S37, S38.1). Missing from the second "
            f"provider: {sorted(board_stats - other_stats) or 'none'}; missing from the "
            f"board: {sorted(other_stats - board_stats) or 'none'}. Scoring fills an "
            "unpublished column with zero, so the difference would arrive as "
            "disagreement about every player rather than as missing data.",
            scored_stats_board=sorted(board_stats),
            scored_stats_other=sorted(other_stats),
        )

    comparison_as_of = other["snapshot_date"].max()
    days_behind = _gap_days(board_as_of, comparison_as_of)
    if days_behind is not None and days_behind > MAX_VINTAGE_GAP_DAYS:
        return board.with_columns(*null_cols), unavailable(
            f"the newest shared capture is {days_behind} days behind the board being "
            f"priced, past the {MAX_VINTAGE_GAP_DAYS}-day limit (S38.1). A spread taken "
            "across two vintages measures the calendar rather than the providers. "
            "Re-export the second provider to restore it.",
            comparison_as_of=_date_str(comparison_as_of),
            days_behind_board=days_behind,
        )

    joined = _attach_points(board, other, "other_projected_points")
    if board_at_comparison is not None and board_at_comparison.height:
        joined = _attach_points(joined, board_at_comparison, "_board_points_then")
        pinned = True
    else:
        joined = joined.with_columns(
            pl.col("projected_points").alias("_board_points_then")
        )
        pinned = False

    stats = [
        _spread_and_cv(mine, theirs)
        for mine, theirs in zip(
            joined["_board_points_then"].to_list(),
            joined["other_projected_points"].to_list(),
            strict=True,
        )
    ]
    joined = joined.drop("_board_points_then").with_columns(
        pl.Series("provider_spread", [s for s, _ in stats], dtype=pl.Float64),
        pl.Series("provider_cv", [c for _, c in stats], dtype=pl.Float64),
        pl.Series(
            "provider_agreement",
            [agreement_label(c) for _, c in stats],
            dtype=pl.String,
        ),
    )

    labelled = joined.filter(pl.col("provider_agreement").is_not_null())
    counts = {level: 0 for level in (HIGH, MEDIUM, LOW)}
    for level in labelled["provider_agreement"].to_list():
        counts[level] += 1
    cvs = labelled["provider_cv"].drop_nulls().to_list()
    spreads = labelled["provider_spread"].drop_nulls().to_list()

    meta = {
        "measurable": True,
        "providers": sorted({_one(board, "provider_id"), _one(other, "provider_id")} - {None}),
        "board_provider": _one(board, "provider_id"),
        "other_provider": _one(other, "provider_id"),
        "board_as_of": _date_str(board_as_of),
        # Both providers read at the same capture date, so the number describes
        # the providers rather than the days between their captures.
        "comparison_as_of": _date_str(comparison_as_of),
        "comparison_pinned_to_shared_capture": pinned,
        "days_behind_board": days_behind,
        "max_vintage_gap_days": MAX_VINTAGE_GAP_DAYS,
        "cv_thresholds": {"high_below": AGREEMENT_CV_HIGH, "medium_below": AGREEMENT_CV_MEDIUM},
        "board_rows": joined.height,
        "players_compared": labelled.height,
        "unpriced_by_other": joined.height - labelled.height,
        "counts_by_agreement": counts,
        "median_cv": round(statistics.median(cvs), 4) if cvs else None,
        "median_spread_points": round(statistics.median(spreads), 1) if spreads else None,
        "scored_stats_compared": sorted(board_stats),
    }
    return joined, meta


def _one(frame: pl.DataFrame, column: str) -> str | None:
    values = frame[column].unique().drop_nulls().to_list()
    return str(values[0]) if len(values) == 1 else None


def _date_str(value: Any) -> str | None:
    return str(value) if value is not None else None


def _gap_days(board_date: Any, other_date: Any) -> int | None:
    if isinstance(board_date, dt.date) and isinstance(other_date, dt.date):
        return abs((board_date - other_date).days)
    return None


DISPERSION_LIMITATION = (
    "Provider dispersion measures disagreement, not accuracy (S38.1). Two providers can "
    "agree closely and both be wrong, particularly where they share upstream inputs. It "
    "is a floor on uncertainty, never a ceiling, and it is two providers -- the minimum "
    "S38.1 contemplates -- so it is a floor taken over the smallest possible sample of "
    "opinions. Replace it with measured projection error when a historical preseason "
    "projection archive exists (S66), as a new methodology version. The comparison is "
    "pinned to a capture date both providers were archived on or before, so it measures "
    "the providers rather than the days between their captures; `comparison_as_of` and "
    "`days_behind_board` record where that pin fell. Descriptive only (S2.2, S88): "
    "nothing here reweights a projection, moves a tier break, or changes a value."
)
