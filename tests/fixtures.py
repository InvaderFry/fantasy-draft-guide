"""A tiny synthetic season, written as real parquet files (S51).

Two teams, four weeks, seven players, each chosen to exercise one thing the
canonical tables got wrong when they were only checked against a build nobody
runs in CI:

    WR1  plays every week                      -- the ordinary case
    WR2  plays 2 weeks, then Reserve/Injured    -- IR absences (S15.1)
    RB1  traded from AAA to BBB mid-season      -- share denominators
    TE1  inactive one week, not on the report   -- a coach's decision
    WR3  Reserve/Suspended in week 1            -- suspension, not injury
    RB2  a week with snaps but no stat line,
         then a week with neither               -- appearing vs missing
    WR4  inactive with an injury-report "Out"   -- injury without a reserve list

The numbers are small enough to check by hand; `expected.py` holds the
arithmetic so a test states the answer rather than recomputing the code under
test.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl

SEASON = 2023
WEEKS = (1, 2, 3, 4)
GAMEDAYS = {
    1: dt.date(2023, 9, 10),
    2: dt.date(2023, 9, 17),
    3: dt.date(2023, 9, 24),
    4: dt.date(2023, 10, 1),
}

WR1, WR2, RB1, TE1, WR3, RB2, WR4 = (f"00-000000{n}" for n in range(1, 8))

# player, week, team, position, targets, receptions, rec_yards, air_yards,
# carries, rush_yards
STAT_ROWS = [
    (WR1, 1, "AAA", "WR", 10, 6, 80, 100, 0, 0),
    (WR1, 2, "AAA", "WR", 10, 6, 80, 100, 0, 0),
    (WR1, 3, "AAA", "WR", 10, 6, 80, 100, 0, 0),
    (WR1, 4, "AAA", "WR", 10, 6, 80, 100, 0, 0),
    (WR2, 1, "AAA", "WR", 5, 3, 40, 50, 0, 0),
    (WR2, 2, "AAA", "WR", 5, 3, 40, 50, 0, 0),
    (RB1, 1, "AAA", "RB", 2, 2, 15, 10, 12, 50),
    (RB1, 2, "AAA", "RB", 2, 2, 15, 10, 12, 50),
    (RB1, 3, "BBB", "RB", 2, 2, 15, 10, 12, 50),
    (RB1, 4, "BBB", "RB", 2, 2, 15, 10, 12, 50),
    (TE1, 1, "AAA", "TE", 4, 3, 30, 20, 0, 0),
    (TE1, 3, "AAA", "TE", 4, 3, 30, 20, 0, 0),
    (TE1, 4, "AAA", "TE", 4, 3, 30, 20, 0, 0),
    (WR3, 2, "BBB", "WR", 6, 4, 45, 30, 0, 0),
    (WR3, 3, "BBB", "WR", 6, 4, 45, 30, 0, 0),
    (WR3, 4, "BBB", "WR", 6, 4, 45, 30, 0, 0),
    (RB2, 1, "BBB", "RB", 0, 0, 0, 0, 10, 40),
    (RB2, 2, "BBB", "RB", 0, 0, 0, 0, 10, 40),
    (WR4, 1, "AAA", "WR", 3, 2, 20, 0, 0, 0),
    (WR4, 2, "AAA", "WR", 3, 2, 20, 0, 0, 0),
    (WR4, 4, "AAA", "WR", 3, 2, 20, 0, 0, 0),
]

# player, week, team, status, status_description_abbr, depth_chart_position
ROSTER_ROWS = (
    [(WR1, w, "AAA", "ACT", "A01", "WR") for w in WEEKS]
    + [(WR2, w, "AAA", "ACT", "A01", "WR") for w in (1, 2)]
    + [(WR2, w, "AAA", "RES", "R01", "WR") for w in (3, 4)]
    + [(RB1, w, "AAA", "ACT", "A01", "RB") for w in (1, 2)]
    + [(RB1, w, "BBB", "ACT", "A01", "RB") for w in (3, 4)]
    + [(TE1, w, "AAA", "ACT", "A01", "TE") for w in (1, 3, 4)]
    + [(TE1, 2, "AAA", "INA", "A01", "TE")]
    + [(WR3, 1, "BBB", "RES", "R40", "WR")]
    + [(WR3, w, "BBB", "ACT", "A01", "WR") for w in (2, 3, 4)]
    + [(RB2, w, "BBB", "ACT", "A01", "RB") for w in WEEKS]
    + [(WR4, w, "AAA", "ACT", "A01", "WR") for w in (1, 2, 4)]
    + [(WR4, 3, "AAA", "INA", "A01", "WR")]
)

# RB2 week 3: snaps but no stat line, so the player appeared. Week 4 has
# neither, so the player did not.
SNAP_WEEKS = [(p, w) for p, w, *_ in STAT_ROWS] + [(RB2, 3)]

# player, week, report_status, report_primary_injury
INJURY_ROWS = [
    (WR4, 3, "Out", "Hamstring"),
    (TE1, 3, "Questionable", "Ankle"),  # played anyway: not an absence
]

TEAMS = ("AAA", "BBB")
PASS_PLAYS_PER_TEAM_WEEK = 20
RUSH_PLAYS_PER_TEAM_WEEK = 15


def _stats_frame() -> pl.DataFrame:
    rows = []
    for player, week, team, position, tgt, rec, ryds, air, car, ryd in STAT_ROWS:
        rows.append(
            {
                "season": SEASON, "week": week, "season_type": "REG",
                "player_id": player, "position": position,
                "recent_team": team, "opponent_team": _opponent(team),
                "targets": tgt, "receptions": rec, "receiving_yards": ryds,
                "receiving_air_yards": air, "receiving_tds": 0,
                "carries": car, "rushing_yards": ryd, "rushing_tds": 0,
                "passing_yards": 0, "passing_tds": 0, "interceptions": 0,
                "sack_fumbles_lost": 0, "rushing_fumbles_lost": 0,
                "receiving_fumbles_lost": 0,
                "passing_2pt_conversions": 0, "rushing_2pt_conversions": 0,
                "receiving_2pt_conversions": 0, "special_teams_tds": 0,
                "target_share": None, "air_yards_share": None,
            }
        )
    return pl.DataFrame(rows)


def _opponent(team: str) -> str:
    return "BBB" if team == "AAA" else "AAA"


def _roster_frame() -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "season": SEASON, "week": week, "game_type": "REG",
                "gsis_id": player, "pfr_id": _pfr(player), "team": team,
                "status": status, "status_description_abbr": code,
                "position": "RB" if depth == "FB" else depth,
                "depth_chart_position": depth,
                "full_name": player,
            }
            for player, week, team, status, code, depth in ROSTER_ROWS
        ]
    )


def _pfr(player: str) -> str:
    return f"PFR{player[-1]}"


def _snap_frame() -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "season": SEASON, "week": week, "game_type": "REG",
                "pfr_player_id": _pfr(player),
                "offense_snaps": 40, "offense_pct": 0.6,
            }
            for player, week in SNAP_WEEKS
        ]
    )


def _injury_frame() -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "season": SEASON, "week": week, "game_type": "REG",
                "gsis_id": player, "report_status": status,
                "report_primary_injury": injury,
            }
            for player, week, status, injury in INJURY_ROWS
        ]
    )


def _pbp_frame() -> pl.DataFrame:
    """Enough play-by-play for the team aggregates and red-zone opportunity."""
    rows = []
    for week in WEEKS:
        game_id = f"{SEASON}_{week:02d}_AAA_BBB"
        for team in TEAMS:
            receiver = WR1 if team == "AAA" else WR3
            rusher = RB1 if team == "AAA" else RB2
            for i in range(PASS_PLAYS_PER_TEAM_WEEK):
                red_zone = i < 2
                rows.append(
                    _play(
                        game_id, week, team, drive=i,
                        pass_attempt=1, rush_attempt=0,
                        yardline_100=10 if red_zone else 50,
                        receiver=receiver, rusher=None,
                    )
                )
            for i in range(RUSH_PLAYS_PER_TEAM_WEEK):
                goal_line = i < 2
                rows.append(
                    _play(
                        game_id, week, team, drive=100 + i,
                        pass_attempt=0, rush_attempt=1,
                        yardline_100=3 if goal_line else 40,
                        receiver=None, rusher=rusher,
                    )
                )
    return pl.DataFrame(rows, schema_overrides={"td_team": pl.String})


def _play(game_id, week, team, *, drive, pass_attempt, rush_attempt,
          yardline_100, receiver, rusher) -> dict:
    return {
        "season": SEASON, "week": week, "season_type": "REG",
        "game_id": game_id, "posteam": team, "play": 1,
        "pass_attempt": pass_attempt, "rush_attempt": rush_attempt,
        "yards_gained": 6, "pass_touchdown": 0, "rush_touchdown": 0,
        "touchdown": 0, "td_team": None, "interception": 0, "fumble_lost": 0,
        "extra_point_attempt": 0, "two_point_attempt": 0,
        "qtr": 1, "score_differential": 0, "yardline_100": yardline_100,
        "fixed_drive": drive,
        "receiver_player_id": receiver, "rusher_player_id": rusher,
    }


def _schedule_csv() -> str:
    lines = ["season,game_type,week,gameday,home_team,away_team,home_score,away_score"]
    for week in WEEKS:
        lines.append(f"{SEASON},REG,{week},{GAMEDAYS[week].isoformat()},AAA,BBB,24,17")
    return "\n".join(lines) + "\n"


def crosswalk() -> pl.DataFrame:
    """The S12 columns player_week and player_season actually read."""
    players = [WR1, WR2, RB1, TE1, WR3, RB2, WR4]
    return pl.DataFrame(
        {
            "gsis_id": players,
            "pfr_id": [_pfr(p) for p in players],
            "birth_date": [dt.date(1998, 1, 1)] * len(players),
            "rookie_season": [2020] * len(players),
            "draft_round": [2] * len(players),
            "draft_pick": [40] * len(players),
        }
    )


def write_raw(directory: Path) -> Path:
    """Materialize the season as the files an ingest would have produced."""
    directory.mkdir(parents=True, exist_ok=True)
    _stats_frame().write_parquet(directory / f"player_stats_{SEASON}.parquet")
    _roster_frame().write_parquet(directory / f"roster_weekly_{SEASON}.parquet")
    _snap_frame().write_parquet(directory / f"snap_counts_{SEASON}.parquet")
    _injury_frame().write_parquet(directory / f"injuries_{SEASON}.parquet")
    _pbp_frame().write_parquet(directory / f"play_by_play_{SEASON}.parquet")
    (directory / "games.csv").write_text(_schedule_csv())
    return directory
