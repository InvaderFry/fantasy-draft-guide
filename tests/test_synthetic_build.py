"""End-to-end table build on the synthetic season (S13, S15.1, S51).

test_tables.py checks the real 14-season build and skips when it has not been
run, which is why two defects reached main behind a green suite. These run the
same builders against `tests/fixtures.py`, whose numbers are small enough to
assert exactly.
"""

import polars as pl
import pytest

from pipeline.features import build
from tests.fixtures import RB1, RB2, SEASON, STAT_ROWS, TE1, WR1, WR2, WR3, WR4


@pytest.fixture
def tables(synthetic_season, tmp_path):
    written = build.build_all(
        [SEASON],
        tables=["player_week", "player_season", "team_season"],
        output_dir=tmp_path / "processed",
    )
    return {name: pl.read_parquet(path) for name, path in written.items()}


def _row(frame: pl.DataFrame, player: str) -> dict:
    return frame.filter(pl.col("player_id") == player).to_dicts()[0]


def test_player_week_covers_rostered_weeks_not_just_played_ones(tables):
    weekly = tables["player_week"]
    assert weekly.height == 7 * 4  # every player, every week, played or not
    assert weekly.filter(pl.col("player_id") == WR2).height == 4  # 2 played, 2 on IR
    # every week with a stat line, plus RB2's week of snaps without one
    assert weekly.filter(pl.col("games_active") == 1).height == len(STAT_ROWS) + 1


def test_every_active_status_the_spec_defines_is_reachable(tables):
    """S13: active | inactive | ir | dnp. The stat-row population had only one."""
    weekly = tables["player_week"]
    assert set(weekly["active_status"].to_list()) == {"active", "inactive", "ir", "dnp"}


def test_inactive_reason_distinguishes_injury_from_discipline_and_choice(tables):
    weekly = tables["player_week"]

    def reason(player, week):
        return _row(weekly.filter(pl.col("week") == week), player)["inactive_reason"]

    assert reason(WR2, 3) == "injury"       # Reserve/Injured
    assert reason(WR4, 3) == "injury"       # injury report says Out, no reserve list
    assert reason(WR3, 1) == "suspension"   # Reserve/Suspended
    assert reason(TE1, 2) == "coach"        # inactive, not on the injury report
    assert reason(RB2, 4) == "coach"        # no stat line and no snap
    assert reason(WR1, 1) is None           # played


def test_a_week_with_snaps_but_no_stat_line_counts_as_played(tables):
    weekly = tables["player_week"]
    week3 = _row(weekly.filter(pl.col("week") == 3), RB2)
    assert week3["games_active"] == 1
    assert week3["active_status"] == "active"
    assert week3["fantasy_points_ppr"] == 0.0


def test_ir_weeks_are_counted_as_injury_absences(tables):
    """The defect: a player who leaves the injury report for IR still missed."""
    outcomes = _row(tables["player_season_outcomes"], WR2)
    assert outcomes["rostered_weeks"] == 4
    assert outcomes["games"] == 2
    assert outcomes["games_missed"] == 2
    assert outcomes["games_missed_injury"] == 2


def test_a_suspension_is_not_an_injury_absence(tables):
    outcomes = _row(tables["player_season_outcomes"], WR3)
    assert outcomes["games_missed"] == 1
    assert outcomes["games_missed_injury"] == 0


def test_a_healthy_scratch_is_not_an_injury_absence(tables):
    outcomes = _row(tables["player_season_outcomes"], TE1)
    assert outcomes["games_missed"] == 1
    assert outcomes["games_missed_injury"] == 0


def test_injury_absences_never_exceed_absences(tables):
    outcomes = tables["player_season_outcomes"]
    assert outcomes.filter(
        pl.col("games_missed_injury") > pl.col("games_missed")
    ).height == 0


def test_air_yard_share_uses_the_active_week_denominator(tables):
    """Team air yards by week: AAA 180/160/120/120, BBB 0/30/40/40."""
    season = tables["player_season"]
    assert _row(season, WR1)["air_yard_share"] == pytest.approx(400 / 580)
    assert _row(season, WR2)["air_yard_share"] == pytest.approx(100 / 340)
    assert _row(season, TE1)["air_yard_share"] == pytest.approx(60 / 420)


def test_a_traded_player_is_credited_against_each_weeks_own_team(tables):
    """RB1 plays AAA in weeks 1-2 and BBB in weeks 3-4.

    The denominator is 180 + 160 + 40 + 40. Dividing by BBB's full season
    (110) -- the previous behaviour, one team, every week -- gives 0.364.
    """
    share = _row(tables["player_season"], RB1)["air_yard_share"]
    assert share == pytest.approx(40 / 420)
    assert share != pytest.approx(40 / 110)


def test_target_share_divides_by_team_targets_not_pass_attempts(tables):
    """Pass attempts count sacks and throwaways; targets are what a share is of.

    TE1 played weeks 1, 3 and 4 for 12 targets. AAA's targets in those weeks
    were 24, 14 and 17 -- not the 20 pass plays per week the play-by-play
    fixture generates, which is what the old denominator would have used.
    """
    row = _row(tables["player_season"], TE1)
    assert row["target_share"] == pytest.approx(12 / 55)
    assert row["target_share"] != pytest.approx(12 / 60)


def test_a_player_who_never_played_gets_null_shares_not_nan(tables):
    """0/0 is NaN in polars, and NaN sorts ahead of every real value."""
    season = tables["player_season"]
    for column in ("target_share", "rush_share", "air_yard_share"):
        assert season.filter(pl.col(column).is_nan()).height == 0


def test_team_season_still_builds_from_play_by_play(tables):
    teams = tables["team_season"]
    assert sorted(teams["team"].to_list()) == ["AAA", "BBB"]
    assert teams["games"].to_list() == [4, 4]
