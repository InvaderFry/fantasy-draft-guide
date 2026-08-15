"""S31.3 -- price movement, and the sign that is the whole risk.

An ADP is a pick number, so a player the market wants MORE has a SMALLER one.
Every bug this feature can ship is that sentence being got backwards somewhere,
and it would not look like a bug: the sheet would print a confident arrow for
every riser and point it the wrong way, at a draft table, under a pick clock.
So the direction is asserted in words rather than by sign, and asserted again on
the rendered HTML.

The other half is honesty about the span. The archive is three days old as this
is written; a delta labelled a week that is really two days is the same lie as a
sheet whose generated date is current while its board is weeks old, and this repo
already refuses that one.
"""

import datetime as dt

import polars as pl
import pytest

from research import sheet
from research.foundations import price_movement

TEAMS = 12
LATEST = dt.date(2026, 8, 15)


def capture(
    date: dt.date, rows: list[tuple], *, window=("2026-08-08", "2026-08-13")
) -> pl.DataFrame:
    """One archived capture, shaped as adp_history stores it."""
    return pl.DataFrame(
        [
            {
                "snapshot_date": date,
                "source_player_id": pid,
                "source_player_name": name,
                "position": pos,
                "adp": adp,
                "window_start": window[0],
                "window_end": window[1],
                "total_drafts": 2214,
            }
            for pid, name, pos, adp in rows
        ],
        schema_overrides={"source_player_id": pl.String},
    )


BOARD_NOW = [("101", "Hunter Henry", "TE", 147.9), ("102", "Jaxson Dart", "QB", 110.9)]
BOARD_THEN = [("101", "Hunter Henry", "TE", 156.8), ("102", "Jaxson Dart", "QB", 98.6)]


def archive(days_back: list[int]) -> pl.DataFrame:
    """A history whose captures sit this many days before the latest one."""
    frames = [capture(LATEST, BOARD_NOW)]
    frames += [capture(LATEST - dt.timedelta(days=d), BOARD_THEN) for d in days_back]
    return pl.concat(frames)


# -- the sign ----------------------------------------------------------------


def test_a_player_being_drafted_earlier_is_rising():
    """Hunter Henry went 156.8 and now goes 147.9. He is RISING, and his delta is
    NEGATIVE, because the number is a pick and the pick got earlier."""
    latest, meta = price_movement.with_movement(
        capture(LATEST, BOARD_NOW), archive([2]), teams=TEAMS
    )
    henry = latest.filter(pl.col("source_player_name") == "Hunter Henry")
    assert henry["adp_delta"].item() == pytest.approx(-8.9)
    assert price_movement.direction(henry["adp_delta"].item()) == price_movement.RISING
    assert meta["available"] is True


def test_a_player_being_drafted_later_is_falling():
    """Jaxson Dart went 98.6 and now goes 110.9 -- twelve picks of the wrong way."""
    latest, _ = price_movement.with_movement(
        capture(LATEST, BOARD_NOW), archive([2]), teams=TEAMS
    )
    dart = latest.filter(pl.col("source_player_name") == "Jaxson Dart")
    assert dart["adp_delta"].item() == pytest.approx(12.3)
    assert price_movement.direction(dart["adp_delta"].item()) == price_movement.FALLING


def test_the_rendered_glyph_points_the_way_the_market_moved():
    """The assertion the table actually depends on: up-triangle means earlier."""
    up = sheet.move_mark(-8.9, TEAMS)     # Henry, rising
    down = sheet.move_mark(12.3, TEAMS)   # Dart, falling
    assert "&#9650;" in up and "&#9660;" not in up      # 9650 = up triangle
    assert "&#9660;" in down and "&#9650;" not in down  # 9660 = down triangle


# -- the span ----------------------------------------------------------------


def test_the_prior_capture_is_the_one_a_lookback_away():
    """Given captures at 2 and 9 days back, a 7-day lookback measures against 9."""
    history = archive([2, 9])
    assert price_movement.prior_capture(history, latest=LATEST) == LATEST - dt.timedelta(days=9)


def test_a_short_archive_measures_what_it_has_and_says_so():
    """Two days is not seven, and the artifact must not imply otherwise."""
    _, meta = price_movement.with_movement(capture(LATEST, BOARD_NOW), archive([2]), teams=TEAMS)
    assert meta["prior_snapshot_date"] == "2026-08-13"
    assert meta["span_days"] == 2
    assert meta["lookback_days_requested"] == price_movement.LOOKBACK_DAYS


def test_both_source_windows_are_recorded_not_just_the_dates():
    """FFC publishes a rolling average; two captures days apart share most of
    their drafts. How damped the delta is can only be read from the windows."""
    _, meta = price_movement.with_movement(capture(LATEST, BOARD_NOW), archive([2]), teams=TEAMS)
    assert meta["latest_window"]["window_end"]
    assert meta["prior_window"]["window_start"]


def test_a_single_capture_archive_has_no_movement_and_says_why():
    """The state the archive was in on 2026-08-13, and the state every newly
    captured format starts in."""
    only = capture(LATEST, BOARD_NOW)
    latest, meta = price_movement.with_movement(only, only, teams=TEAMS)
    assert latest["adp_delta"].null_count() == latest.height
    assert meta["available"] is False
    assert "one capture" in meta["reason"]


# -- who gets marked ---------------------------------------------------------


def test_the_threshold_is_half_a_round_and_scales_with_the_league():
    assert price_movement.mover_threshold(12) == 6.0
    assert price_movement.mover_threshold(10) == 5.0
    assert price_movement.is_mover(-6.0, 12)      # exactly half a round counts
    assert not price_movement.is_mover(-5.9, 12)
    assert price_movement.is_mover(-5.9, 10)      # ...and it does in a 10-team league


def test_a_player_new_to_the_board_is_not_a_mover():
    """He has not moved from anywhere. Marking him would invent the largest move
    on the page out of a player the prior capture simply did not quote."""
    now = capture(LATEST, [*BOARD_NOW, ("103", "Camp Body", "WR", 180.0)])
    latest, meta = price_movement.with_movement(now, archive([2]), teams=TEAMS)
    new = latest.filter(pl.col("source_player_name") == "Camp Body")
    assert new["adp_delta"].item() is None
    assert sheet.move_mark(new["adp_delta"].item(), TEAMS) == ""
    assert meta["unmatched_in_prior_capture"] == 1


def test_a_player_the_id_join_misses_is_matched_on_name_and_position():
    """Same source both sides, so the id is normally there -- but a capture that
    predates one must not silently lose its whole delta column."""
    now = capture(LATEST, BOARD_NOW)
    then = capture(LATEST - dt.timedelta(days=2), BOARD_THEN).with_columns(
        pl.lit(None, dtype=pl.String).alias("source_player_id")
    )
    latest, meta = price_movement.with_movement(now, pl.concat([now, then]), teams=TEAMS)
    assert meta["matched_across_captures"] == 2
    henry = latest.filter(pl.col("source_player_name") == "Hunter Henry")
    assert henry["adp_delta"].item() == pytest.approx(-8.9)


def test_an_ambiguous_name_with_no_id_prices_nobody():
    """S12's rule for the loose key, applied here too: two players sharing a name
    and a position cannot be told apart, and guessing moves one man's price onto
    another man's row."""
    now = capture(LATEST, [("101", "Same Name", "WR", 40.0)])
    then = capture(
        LATEST - dt.timedelta(days=2),
        [("201", "Same Name", "WR", 30.0), ("202", "Same Name", "WR", 90.0)],
    ).with_columns(pl.lit(None, dtype=pl.String).alias("source_player_id"))
    latest, _ = price_movement.with_movement(now, pl.concat([now, then]), teams=TEAMS)
    assert latest["adp_delta"].item() is None


# -- the sheet ---------------------------------------------------------------


def test_no_movement_means_no_legend_and_no_marks():
    art = {
        "primary_results": {
            "price_movement": price_movement.unavailable("the archive holds one capture")
        }
    }
    legend = sheet.movement_legend(
        {"tiers_and_replacement_level__x": art}, {"id": "x", "teams": 12}
    )
    assert legend == ""


def test_the_legend_names_the_day_the_moves_are_measured_from():
    art = {
        "primary_results": {
            "price_movement": {
                "available": True,
                "prior_snapshot_date": "2026-08-13",
                "mover_threshold_picks": 6.0,
            }
        }
    }
    legend = sheet.movement_legend(
        {"tiers_and_replacement_level__x": art}, {"id": "x", "teams": 12}
    )
    assert "2026-08-13" in legend and "6+ picks" in legend
    # S83 keeps evidence language off the sheet; the legend is not an exception.
    sheet.assert_sheet_constraints(legend)
