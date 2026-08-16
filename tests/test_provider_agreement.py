"""S38.1's cross-provider spread, and the two things it must refuse to measure.

The formula is three lines out of the spec. Everything here is about the states in
which those three lines compute cleanly and mean something other than what they
say -- which is the failure mode this whole module is shaped around, and the same
one the FantasyPros stat map had (a mapping can be wrong in a way that still
produces a number, and a board built on it looks exactly like a correct one).
"""

import datetime as dt

import polars as pl
import pytest

from research.foundations import provider_agreement as agree

PROFILE = {
    "id": "half_ppr_12",
    "teams": 12,
    "scoring": {
        "pass_td": 4, "pass_yd": 0.04, "interception": -2,
        "rush_yd": 0.10, "rush_td": 6,
        "reception": 0.5, "receiving_yd": 0.10, "receiving_td": 6,
        "fumble_lost": -2,
    },
}

NAMES = ("Bijan Robinson", "Breece Hall", "Chase Brown", "Derrick Henry")


def _frame(provider, points, *, date=dt.date(2026, 8, 16), names=NAMES,
           receptions=0.0, fumbles=0.0, ids=True):
    rows = []
    for i, pts in enumerate(points):
        rows.append(
            {
                "season": 2026,
                "snapshot_date": date,
                "provider_id": provider,
                "player_id": f"00-{i:05d}" if ids else None,
                "source_player_name": names[i],
                "position": "RB",
                "team": "ATL",
                "projected_points": float(pts),
                "receptions": receptions,
                "fumbles_lost": fumbles,
            }
        )
    return pl.DataFrame(rows)


def test_the_labels_are_the_thresholds_the_spec_committed():
    """S38.1 gives 0.08 and 0.15 by name. They are not ours to tune (S80)."""
    assert agree.agreement_label(0.07) == agree.HIGH
    assert agree.agreement_label(0.08) == agree.MEDIUM
    assert agree.agreement_label(0.14) == agree.MEDIUM
    assert agree.agreement_label(0.15) == agree.LOW
    assert agree.agreement_label(None) is None


def test_only_low_agreement_is_ever_marked():
    """S38.1's Use table gives MEDIUM and HIGH normal treatment."""
    assert agree.is_low(agree.LOW)
    assert not agree.is_low(agree.MEDIUM)
    assert not agree.is_low(agree.HIGH)
    assert not agree.is_low(None)


def test_a_player_both_providers_price_gets_a_spread_and_a_label():
    board = _frame("fantasypros", [300.0, 200.0, 150.0, 100.0])
    other = _frame("fftoday", [305.0, 140.0, 150.0, 99.0])

    joined, meta = agree.with_agreement(
        board, other, profile=PROFILE, board_as_of=dt.date(2026, 8, 16)
    )

    labels = dict(
        zip(joined["source_player_name"], joined["provider_agreement"], strict=True)
    )
    assert labels["Bijan Robinson"] == agree.HIGH      # 300 vs 305
    assert labels["Breece Hall"] == agree.LOW          # 200 vs 140
    assert labels["Chase Brown"] == agree.HIGH         # identical
    assert meta["measurable"] is True
    assert meta["players_compared"] == 4
    assert meta["counts_by_agreement"][agree.LOW] == 1


def test_a_player_the_second_provider_does_not_price_is_unexamined_not_agreed_with():
    """Dropping him would change how many players sit above replacement."""
    board = _frame("fantasypros", [300.0, 200.0, 150.0, 100.0])
    other = _frame("fftoday", [305.0, 199.0], names=NAMES[:2])

    joined, meta = agree.with_agreement(
        board, other, profile=PROFILE, board_as_of=dt.date(2026, 8, 16)
    )

    assert joined.height == 4
    labels = dict(
        zip(joined["source_player_name"], joined["provider_agreement"], strict=True)
    )
    assert labels["Derrick Henry"] is None
    assert meta["unpriced_by_other"] == 2


def test_a_provider_missing_a_scored_stat_is_refused_rather_than_reported_as_disagreement():
    """The defect `fill_null(0)` makes invisible.

    A provider publishing no receptions scores ~60 points low in a half-PPR
    league. That computes, and it arrives as a confident LOW against every player
    on the board -- a mark, at a draft table, produced entirely by a column that
    was never published.
    """
    board = _frame("fantasypros", [300.0, 200.0, 150.0, 100.0], receptions=60.0)
    other = _frame("fftoday", [270.0, 170.0, 120.0, 70.0], receptions=None)

    joined, meta = agree.with_agreement(
        board, other, profile=PROFILE, board_as_of=dt.date(2026, 8, 16)
    )

    assert meta["measurable"] is False
    assert "receptions" in meta["reason"]
    assert joined["provider_agreement"].drop_nulls().len() == 0


def test_a_stat_the_league_does_not_score_does_not_block_the_comparison():
    """Refusing on an unscored column would reject a perfectly sound comparison."""
    profile = {**PROFILE, "scoring": {k: v for k, v in PROFILE["scoring"].items()
                                      if k != "fumble_lost"}}
    board = _frame("fantasypros", [300.0, 200.0, 150.0, 100.0], fumbles=2.0)
    other = _frame("fftoday", [299.0, 201.0, 149.0, 101.0], fumbles=None)

    _, meta = agree.with_agreement(
        board, other, profile=profile, board_as_of=dt.date(2026, 8, 16)
    )
    assert meta["measurable"] is True


def test_a_second_board_older_than_the_limit_reports_nothing_rather_than_the_calendar():
    """S38.1 across two vintages measures the days, not the providers."""
    board = _frame("fantasypros", [300.0, 200.0, 150.0, 100.0])
    other = _frame("fftoday", [250.0, 240.0, 130.0, 90.0], date=dt.date(2026, 7, 20))

    joined, meta = agree.with_agreement(
        board, other, profile=PROFILE, board_as_of=dt.date(2026, 8, 16)
    )

    assert meta["measurable"] is False
    assert meta["days_behind_board"] == 27
    assert "measures the calendar" in meta["reason"]
    assert joined["provider_agreement"].drop_nulls().len() == 0


def test_one_provider_still_reports_that_the_board_has_no_error_bar():
    """The message CI sees, unchanged: there is no second provider in the archive."""
    board = _frame("fantasypros", [300.0, 200.0])
    joined, meta = agree.with_agreement(
        board, board.clear(), profile=PROFILE, board_as_of=dt.date(2026, 8, 16)
    )

    assert meta["measurable"] is False
    assert meta["reason"] == agree.NO_SECOND_PROVIDER
    assert joined.height == 2


def test_providers_that_spell_a_name_differently_are_still_one_player():
    """Joining on the raw string compares only the subset that agrees about
    punctuation, and then reports that subset as coverage."""
    board = _frame("fantasypros", [300.0, 200.0], names=("Marvin Harrison Jr.", "Breece Hall"),
                   ids=False)
    other = _frame("fftoday", [180.0, 199.0], names=("Marvin Harrison", "Breece Hall"),
                   ids=False)

    joined, meta = agree.with_agreement(
        board, other, profile=PROFILE, board_as_of=dt.date(2026, 8, 16)
    )

    assert meta["players_compared"] == 2
    labels = dict(
        zip(joined["source_player_name"], joined["provider_agreement"], strict=True)
    )
    assert labels["Marvin Harrison Jr."] == agree.LOW


def test_two_players_sharing_a_name_and_position_are_compared_to_neither():
    """S12's rule for its loose key: guessing puts one man's second opinion on
    another man's row."""
    board = _frame("fantasypros", [300.0, 200.0], names=("Josh Allen", "Josh Allen"),
                   ids=False)
    other = _frame("fftoday", [180.0], names=("Josh Allen",), ids=False)

    joined, meta = agree.with_agreement(
        board, other, profile=PROFILE, board_as_of=dt.date(2026, 8, 16)
    )

    assert meta["players_compared"] == 0
    assert joined["provider_agreement"].drop_nulls().len() == 0


def test_a_projected_total_of_zero_produces_no_label_rather_than_the_largest_one():
    """Dividing by a mean of zero would manufacture the loudest disagreement on
    the board out of its least interesting player."""
    spread, cv = agree._spread_and_cv(0.0, 0.0)
    assert spread == 0.0
    assert cv is None
    assert agree.agreement_label(cv) is None


@pytest.mark.parametrize("missing", [(None, 5.0), (5.0, None), (None, None)])
def test_one_opinion_is_not_a_spread(missing):
    assert agree._spread_and_cv(*missing) == (None, None)


def test_the_comparison_is_taken_at_a_date_both_providers_were_captured_on():
    """Otherwise the number is the calendar wearing the providers' clothes.

    The board provider is captured daily and a manual export once, so their newest
    captures are days apart. Here the board has moved a long way since the export
    landed and the two providers agreed exactly on the day the export was taken --
    so the honest answer is HIGH, and comparing newest-to-newest would print LOW
    against a player nobody disagrees about.
    """
    displayed = _frame("fantasypros", [300.0, 200.0])
    board_then = _frame("fantasypros", [180.0, 200.0], date=dt.date(2026, 8, 12))
    other = _frame("fftoday", [181.0, 199.0], date=dt.date(2026, 8, 12))

    joined, meta = agree.with_agreement(
        displayed,
        other,
        profile=PROFILE,
        board_as_of=dt.date(2026, 8, 16),
        board_at_comparison=board_then,
    )

    labels = dict(
        zip(joined["source_player_name"], joined["provider_agreement"], strict=True)
    )
    assert labels["Bijan Robinson"] == agree.HIGH
    assert meta["comparison_pinned_to_shared_capture"] is True
    assert meta["comparison_as_of"] == "2026-08-12"
    assert meta["days_behind_board"] == 4
    # The board itself is untouched: the sheet still prices off today.
    assert joined["projected_points"].to_list() == [300.0, 200.0]


def test_without_a_shared_capture_the_comparison_says_it_was_not_pinned():
    """An archive that does not hold the board provider on the export's day is a
    weaker comparison, and it says so rather than implying the pin."""
    displayed = _frame("fantasypros", [300.0, 200.0])
    other = _frame("fftoday", [181.0, 199.0], date=dt.date(2026, 8, 12))

    _, meta = agree.with_agreement(
        displayed, other, profile=PROFILE, board_as_of=dt.date(2026, 8, 16)
    )
    assert meta["comparison_pinned_to_shared_capture"] is False
