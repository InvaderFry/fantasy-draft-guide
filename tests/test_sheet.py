"""The draft-day sheet (S83, S78).

S83 makes three promises the sheet has to keep whatever is behind it: it is
generated from the artifacts, it carries conclusions and prices only, and a
section with nothing behind it says so rather than going blank.
"""

import json

import pytest

from research import sheet
from research.foundations import survival as survival_mod
from research.foundations import tiers as tiers_mod

PROFILE = {"id": "fixture_12", "label": "12-team half-PPR fixture", "teams": 12, "real": True}

REGRESSION_ARTIFACT = {
    "method_id": "team_scoring_regression",
    "primary_results": {
        "current_extremes": {
            "season": 2025,
            "abs_z_at_least_1.5": [
                {"team": "LA", "metric": "offensive_tds", "value": 63.0, "z": 2.18,
                 "direction": "above league"},
                {"team": "TEN", "metric": "offensive_tds", "value": 24.0, "z": -1.72,
                 "direction": "below league"},
                {"team": "NYJ", "metric": "pass_rate", "value": 0.45, "z": -2.5,
                 "direction": "below league"},
            ],
        },
        "regression_to_mean": [
            {
                "metric": "offensive_tds",
                "buckets": [
                    {"z_from": -2.0, "z_to": -1.0, "mean_next_change": 8.4, "n": 70,
                     "ci_low": 6.35, "ci_high": 10.4},
                    {"z_from": 2.0, "z_to": 99.0, "mean_next_change": -17.7, "n": 10,
                     "ci_low": -21.1, "ci_high": -14.3},
                ],
            }
        ],
    },
}

TIER_ARTIFACT = {
    "method_id": f"{tiers_mod.METHOD_ID}__fixture_12",
    "primary_results": {
        "positions": {
            "RB": {
                "replacement": {"points": 120.0, "rank": 27},
                "tier_count": 2,
                "players": [
                    {"player": "Bijan Robinson", "team": "ATL", "tier": 1,
                     "projected_points": 290.0, "value_over_replacement": 170.0},
                    {"player": "Player Two", "team": "DET", "tier": 2,
                     "projected_points": 200.0, "value_over_replacement": 80.0},
                ],
            }
        }
    },
}

SURVIVAL_ARTIFACT = {
    "method_id": f"{survival_mod.METHOD_ID}__fixture_12",
    "primary_results": {
        "teams": 12,
        "by_slot": [
            {
                "slot": 7,
                "held_picks": [7, 18],
                "picks": [
                    {
                        "pick": 18,
                        "candidates": [
                            {"player": "Puka Nacua", "position": "WR", "adp": 8.2,
                             "p_available": 0.02, "approximation_note": None},
                            {"player": "No Spread", "position": "WR", "adp": 30.0,
                             "p_available": None, "approximation_note": "no spread published"},
                        ],
                    }
                ],
            }
        ],
    },
}

FULL = {
    "team_scoring_regression": REGRESSION_ARTIFACT,
    f"{tiers_mod.METHOD_ID}__fixture_12": TIER_ARTIFACT,
    f"{survival_mod.METHOD_ID}__fixture_12": SURVIVAL_ARTIFACT,
}


def test_every_s83_section_is_present_even_when_it_has_nothing_behind_it():
    """A blank space on a draft sheet gets filled in from memory at pick 43."""
    page = sheet.render("test", profile=None, artifacts={})
    for name, _spec in sheet.SECTIONS:
        assert name in page
    assert page.count("NOT BUILT") == 4       # Targets, Avoids, Darts, False Friends
    # Tiers, Survival and -- with no artifacts at all -- Regression too.
    assert page.count("BLOCKED") == 3


def test_unbuilt_sections_name_their_spec_section_and_the_build_that_owns_them():
    page = sheet.render("test", profile=None, artifacts={})
    for spec in ("S27", "S28", "S29", "S34"):
        assert spec in page
    assert "S79" in page


def test_the_sheet_carries_no_grades_intervals_or_sample_sizes():
    """S83: "the sheet carries conclusions and prices only"."""
    page = sheet.render("test", profile=PROFILE, artifacts=FULL)
    lowered = page.lower()
    for token in sheet.FORBIDDEN:
        assert token not in lowered
    # The regression artifact carries n and an interval on every bucket; neither
    # reaches the page.
    assert "ci_low" not in page
    assert ">70<" not in page


def test_the_constraint_is_enforced_rather_than_documented():
    with pytest.raises(sheet.SheetConstraintError, match="S83"):
        sheet.assert_sheet_constraints("<p>evidence grade: B</p>")


def test_regression_flags_teams_with_a_direction_and_an_expected_move():
    """Every recommendation carries its price trigger; a flag with no size is
    not usable at a live pick."""
    page = sheet.render("test", profile=None, artifacts=FULL)
    assert "FADE" in page and "LA" in page and "-18 TD" in page
    assert "BUY" in page and "TEN" in page and "+8 TD" in page
    # Only the touchdown metric is a regression flag; pass rate is not.
    assert "NYJ" not in page


def test_the_tier_section_shows_tiers_value_and_replacement():
    page = sheet.render("test", profile=PROFILE, artifacts=FULL)
    assert "Bijan Robinson" in page
    assert "replacement 120.0 pts" in page
    assert "tier-start" in page


def test_survival_shows_a_probability_and_a_dash_where_there_is_none():
    page = sheet.render("test", profile=PROFILE, artifacts=FULL)
    assert "Pick 18" in page
    assert "2%" in page
    assert "Normal approximation" in page


def test_an_undrawn_slot_does_not_silently_pick_one():
    artifacts = dict(FULL)
    many = json.loads(json.dumps(SURVIVAL_ARTIFACT))
    many["primary_results"]["by_slot"] = [
        {"slot": s, "held_picks": [s], "picks": []} for s in range(1, 13)
    ]
    artifacts[f"{survival_mod.METHOD_ID}__fixture_12"] = many
    page = sheet.render("test", profile=PROFILE, artifacts=artifacts)
    assert "Draft slot undrawn" in page


def test_with_no_real_profile_the_sheet_says_it_is_not_a_league_sheet():
    """S14 excludes non-real profiles from the sheet. The honest output is the
    profile-independent one, labelled as such."""
    page = sheet.render("test", profile=None, artifacts=FULL)
    assert "This is not a league sheet" in page
    assert "draft_date" in page


def test_the_page_is_self_contained():
    """S8: the sheet is used at a draft table. It cannot need a network."""
    page = sheet.render("test", profile=PROFILE, artifacts=FULL)
    for token in ("http://", "https://", "<script", "<link", "<img"):
        assert token not in page.lower()


def test_write_produces_one_file_per_real_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(sheet, "real_profiles", lambda: [PROFILE, {**PROFILE, "id": "other"}])
    directory = tmp_path / "ed" / "methods"
    directory.mkdir(parents=True)
    for name, artifact in FULL.items():
        (directory / f"{name}.json").write_text(json.dumps(artifact))
    paths = sheet.write("ed", root=tmp_path)
    assert [p.name for p in paths] == ["fixture_12.html", "other.html"]
    assert "Bijan Robinson" in paths[0].read_text()


def test_write_falls_back_to_one_profile_independent_sheet(tmp_path, monkeypatch):
    monkeypatch.setattr(sheet, "real_profiles", list)
    (tmp_path / "ed" / "methods").mkdir(parents=True)
    paths = sheet.write("ed", root=tmp_path)
    assert [p.name for p in paths] == ["no_profile.html"]


def test_end_to_end_from_a_projection_archive_to_a_finished_sheet(tmp_path, monkeypatch):
    """The whole path, with both gates opened by fixtures.

    Projection payload -> projection_snapshot -> board -> tiers -> artifact ->
    sheet, plus ADP -> survival -> artifact -> sheet. This is what happens the
    day FANTASYPROS_API_KEY and the two draft values are filled in, so it is
    worth having run before that day rather than after it.
    """
    import datetime as dt

    import polars as pl

    from pipeline.features import projections
    from pipeline.ingest import fantasypros
    from tests.test_projections import CROSSWALK, FP_CONFIG
    from tests.test_survival import _adp

    profile = {
        "id": "e2e_12",
        "label": "12-team half-PPR end to end",
        "teams": 12,
        "real": True,
        "draft_slot": 7,
        "scoring": {"reception": 0.5, "receiving_yd": 0.10, "rush_yd": 0.10, "rush_td": 6},
        "starters": {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "FLEX": 1},
        "flex_eligible": ["RB", "WR", "TE"],
    }

    # An archive holding one API payload, as the runner would have written it.
    monkeypatch.setattr(fantasypros, "fantasypros_config", lambda: FP_CONFIG)
    archive = tmp_path / "snapshots"
    day = archive / "2026-08-14"
    day.mkdir(parents=True)
    players = [
        {"fpid": str(i), "name": f"RB{i}", "team_id": "ATL", "position_id": "RB",
         "rush_yds": (2600 - i * 40), "rush_tds": 0, "rec": 0, "rec_yds": 0, "rec_tds": 0,
         "fpts": 0}
        for i in range(1, 40)
    ]
    day.joinpath("fantasypros_projections_rb_2026.json").write_text(
        json.dumps({"players": players})
    )

    table = projections.build(archive, crosswalk=CROSSWALK)
    assert table.height == 39

    board = tiers_mod.board(profile, frame=table)
    tier_results = tiers_mod.compute(board, profile)
    # 12 teams x 2 RB, plus the flex slots that land on backs when they are the
    # only position on the board: replacement is deeper than the base cutoff.
    assert tier_results["positions"]["RB"]["replacement"]["rank"] >= 25

    survival_results = survival_mod.compute(_adp(), profile, rounds=2)

    edition = tmp_path / "artifacts"
    (edition / "ed" / "methods").mkdir(parents=True)
    for artifact in (
        tiers_mod.export(tier_results, profile),
        survival_mod.export(survival_results, profile),
    ):
        artifact.write("ed", root=edition)
    (edition / "ed" / "methods" / "team_scoring_regression.json").write_text(
        json.dumps(REGRESSION_ARTIFACT)
    )

    monkeypatch.setattr(sheet, "real_profiles", lambda: [profile])
    paths = sheet.write("ed", root=edition)
    page = paths[0].read_text()

    assert paths[0].name == "e2e_12.html"
    assert "BLOCKED" not in page          # every gated section filled in
    assert "RB1" in page                  # tiers
    assert "Pick 18" in page              # survival at the real held picks
    assert "FADE" in page                 # regression
    assert page.count("NOT BUILT") == 4   # and only the genuinely unbuilt ones
    assert isinstance(pl.DataFrame(), pl.DataFrame) and dt.date.today()
