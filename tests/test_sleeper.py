"""S38.1's second provider, written against a payload nobody here has seen.

api.sleeper.app answers 403 at CONNECT from this sandbox, exactly as FantasyPros
and FFC do, so every shape-dependent value is configuration and these tests pin
the ADAPTER's behaviour rather than the provider's format. The format question is
answered from the first runner capture -- see `sleeper_projection_shape` in
research/questions.yaml -- and a wrong guess is a YAML edit re-parsed against the
same stored bytes.

What is worth pinning before that payload exists: that a wrong guess FAILS rather
than emitting a frame of nulls, that identity comes from the provider's own
gsis_id when it is there, and that none of it can cost a day of ADP.
"""

import datetime as dt
import json

import pytest

from pipeline.config import sleeper_config
from pipeline.ingest import sleeper

CAPTURE = dt.date(2026, 8, 16)


def row(**over):
    """One row shaped the way config/sources.yaml currently guesses."""
    base = {
        "player_id": "4034",
        "stats": {
            "rush_yd": 1290.0, "rush_td": 11.0, "rec": 52.0, "rec_yd": 420.0,
            "rec_td": 3.0, "fum_lost": 2.0, "pts_half_ppr": 291.4,
        },
        "player": {
            "player_id": "4034", "first_name": "Bijan", "last_name": "Robinson",
            "full_name": "Bijan Robinson", "team": "ATL", "position": "RB",
            "gsis_id": "00-0038996",
        },
    }
    base.update(over)
    return base


def payload(*rows) -> bytes:
    return json.dumps(list(rows) or [row()]).encode()


def test_the_provider_supplies_the_canonical_id_so_no_name_match_is_needed():
    """S12 by id rather than by spelling.

    Every other source here is name-matched because none of them publishes a
    canonical id, and that join is what lost `Travis Hunter` across twelve
    captures. Sleeper publishes gsis_id on its player objects, so the row arrives
    already resolved and the name never enters into it.
    """
    parsed = sleeper.parse(payload(), snapshot_date=CAPTURE, season=2026)[0]

    assert parsed["player_id"] == "00-0038996"
    assert parsed["match_method"] == "provider_gsis_id"
    assert parsed["match_confidence"] == 1.0


def test_a_row_without_a_provider_id_is_left_for_the_name_match():
    """Absent is not zero. A row with no gsis_id must fall through to S12's name
    path rather than arriving with a null id that looks resolved."""
    bare = row(player={"full_name": "Camp Body", "team": "ATL", "position": "RB"})
    parsed = sleeper.parse(payload(bare), snapshot_date=CAPTURE, season=2026)[0]

    assert "player_id" not in parsed or parsed.get("player_id") is None
    assert parsed["source_player_name"] == "Camp Body"


def test_a_name_split_across_first_and_last_is_still_a_name():
    """The one identity guess worth making: `full_name` may not exist."""
    split = row(player={"first_name": "Bijan", "last_name": "Robinson",
                        "team": "ATL", "position": "RB"})
    parsed = sleeper.parse(payload(split), snapshot_date=CAPTURE, season=2026)[0]
    assert parsed["source_player_name"] == "Bijan Robinson"


def test_a_stat_map_that_matches_nothing_raises_instead_of_emitting_nulls():
    """The defect this check exists for.

    A mapping aimed at columns the provider does not publish parses cleanly and
    produces a full frame of nulls -- which reads downstream as a provider with
    no projections rather than as a mapping that missed, and S19.3 would report
    the board as blocked for a reason that is not true.
    """
    wrong = row(stats={"totally_different_name": 1.0})
    with pytest.raises(sleeper.ResponseShapeError) as exc:
        sleeper.parse(payload(wrong), snapshot_date=CAPTURE, season=2026)

    message = str(exc.value)
    assert "totally_different_name" in message      # what arrived
    assert "config/sources.yaml" in message          # where to fix it
    assert "same capture" in message                 # and that nothing was lost


def test_the_published_total_is_a_check_and_never_an_input():
    """S38.1's comparison is only as good as the stat map, and a stat map can be
    wrong in a way that still computes. The provider's own scored total is the
    independent check -- so it must be readable, and it must NOT be mapped into
    the frame, or the check would compare a column to itself."""
    raw = row()
    assert sleeper.published_points(raw) == 291.4

    parsed = sleeper.parse(payload(raw), snapshot_date=CAPTURE, season=2026)[0]
    assert parsed.get("projected_fantasy_points") is None
    assert "pts_half_ppr" not in parsed


def test_the_mapped_stats_reproduce_the_providers_own_total():
    """The check itself, run against the shape currently guessed.

    This is the FantasyPros precedent: scoring the mapped frame under a half-PPR
    profile reproduced that provider's published `points_half` to the cent, and
    that is what proved the mapping right rather than merely non-empty. Here it
    runs on a synthetic row whose stats were chosen to sum to the total it
    carries -- so it pins the ARITHMETIC and the column names, and it becomes a
    real check the moment a captured row replaces the fixture.
    """
    import polars as pl

    from pipeline.scoring import score_frame

    profile = {"id": "t", "scoring": {
        "rush_yd": 0.10, "rush_td": 6, "reception": 0.5,
        "receiving_yd": 0.10, "receiving_td": 6, "fumble_lost": -2,
    }}
    raw = row()
    parsed = sleeper.parse(payload(raw), snapshot_date=CAPTURE, season=2026)
    frame = score_frame(pl.DataFrame(parsed), profile, alias="points")

    # 1290*.1 + 11*6 + 52*.5 + 420*.1 + 3*6 + 2*-2 = 129 + 66 + 26 + 42 + 18 - 4
    assert round(frame["points"][0], 2) == 277.0
    # ...and the provider says 291.4, so on a REAL payload this difference is the
    # signal to fix the map -- not to shrug and ship it.
    assert sleeper.published_points(raw) == 291.4


def test_every_host_failing_names_all_of_them():
    """The endpoint is reported at two hosts and this adapter cannot reach either
    to settle which. A failure has to say what it tried."""
    adapter = sleeper.SleeperAdapter(season=2026)
    assert adapter.hosts() == ["https://api.sleeper.com", "https://api.sleeper.app"]


def test_the_position_filter_is_a_repeated_parameter_not_a_comma_list():
    """Sleeper takes `position[]` repeated, so one request covers the board.
    Sending a comma list instead returns one position or none, and a board of
    quarterbacks would look exactly like a board."""
    query = sleeper.SleeperAdapter(season=2026).query()
    assert query["position[]"] == ["QB", "RB", "WR", "TE"]
    assert query["season_type"] == "regular"


def test_the_capture_records_what_it_actually_received():
    """The probe. The shape is a guess, so the FIRST capture has to answer the
    question from the archive -- the manifest is committed and a CI log is not."""
    cfg = sleeper_config()
    rows = json.loads(payload())
    assert sleeper._observed_stat_keys(rows, cfg) == {
        "rush_yd", "rush_td", "rec", "rec_yd", "rec_td", "fum_lost", "pts_half_ppr"
    }
    assert "gsis_id" in sleeper._observed_player_keys(rows, cfg)


def test_the_two_providers_map_every_column_the_leagues_actually_score():
    """The way S38.1 silently does nothing, caught in CI instead of on 26 sheets.

    `provider_agreement.populated_scored_stats` compares SETS. One scored column
    that Sleeper does not publish and FantasyPros does suppresses every mark on
    every sheet, permanently -- and it does not look like a fault, it looks like
    a careful refusal with a well-reasoned message, arriving every morning after
    a successful capture.

    This shipped once already: the first Sleeper stat_map omitted
    `two_point_conversions` and `special_teams_tds`, both of which both real
    profiles score and both of which FantasyPros publishes. Nothing would have
    been marked, ever.

    A config test rather than a payload test, deliberately -- it is the declared
    mapping that is wrong in this failure, and it can be checked before a single
    byte is captured.
    """
    from pipeline.config import fantasypros_config, real_profiles, sources
    from pipeline.scoring import STAT_TO_RULE

    board = set(fantasypros_config()["stat_map"].values())
    second = set(sources()["sleeper_api"]["stat_map"].values())

    for profile in real_profiles():
        rules = profile["scoring"]
        scored = {stat for stat, rule in STAT_TO_RULE.items() if rules.get(rule)}
        missing = (board & scored) - second
        assert not missing, (
            f"{profile['id']} scores {sorted(missing)}, FantasyPros publishes them and the "
            "Sleeper stat_map does not. S38.1 will refuse the comparison on every player "
            "on every sheet. Map them in config/sources.yaml, or change the comparison "
            "basis deliberately in provider_agreement."
        )


def test_repeated_destinations_are_summed_and_absent_ones_stay_null():
    """Sleeper splits 2-point conversions three ways; FantasyPros publishes one
    column. An assignment loop keeps whichever source came last in dict order --
    a third of the total, still a number, still plausible on a board.

    And a destination with no source present must stay NULL, not become zero: a
    zero tells `populated_scored_stats` the provider publishes a column it does
    not, which is exactly what the comparability gate exists to catch.
    """
    split = row(stats={"rush_yd": 100.0, "pass_2pt": 1.0, "rush_2pt": 2.0, "rec_2pt": 3.0})
    parsed = sleeper.parse(payload(split), snapshot_date=CAPTURE, season=2026)[0]

    assert parsed["two_point_conversions"] == 6.0
    assert parsed["special_teams_tds"] is None
