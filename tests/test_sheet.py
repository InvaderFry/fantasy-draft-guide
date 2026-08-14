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
                     "projected_points": 290.0, "value_over_replacement": 170.0,
                     "adp": 2.1, "position_adp": 2},
                    {"player": "Player Two", "team": "DET", "tier": 2,
                     "projected_points": 200.0, "value_over_replacement": 80.0,
                     "adp": 41.2, "position_adp": 18},
                    {"player": "Unpriced Man", "team": "CHI", "tier": 2,
                     "projected_points": 180.0, "value_over_replacement": 60.0,
                     "adp": None, "position_adp": None},
                ],
            }
        }
    },
}

SURVIVAL_ARTIFACT = {
    "method_id": f"{survival_mod.METHOD_ID}__fixture_12",
    "primary_results": {
        "teams": 12,
        # Deliberately not today: the sheet has to distinguish the capture it was
        # priced from at the generation date, and a fixture where they coincide
        # cannot tell whether the page is reading one or the other.
        "adp_snapshot_date": "2026-08-01",
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
                            {"player": "Dead Zone Back", "position": "RB", "adp": 41.2,
                             "p_available": 0.31, "approximation_note": None},
                        ],
                    }
                ],
            }
        ],
    },
}

DEAD_ZONE_ARTIFACT = {
    "method_id": "rb_dead_zone_bucket_rates",
    "primary_results": {
        "n": 1285,
        "rb_vs_wr": [
            {"bucket": 1, "bucket_label": "1-12", "n_rb": 63, "n_wr": 31,
             "rb_high_end_rate": 0.6349, "rb_ci": [0.51, 0.74],
             "wr_high_end_rate": 0.8065, "wr_ci": [0.63, 0.90],
             "absolute_difference_pp": -17.15},
            {"bucket": 2, "bucket_label": "13-24", "n_rb": 39, "n_wr": 41,
             "rb_high_end_rate": 0.5128, "wr_high_end_rate": 0.6098,
             "absolute_difference_pp": -9.69},
            {"bucket": 3, "bucket_label": "25-36", "n_rb": 39, "n_wr": 47,
             "rb_high_end_rate": 0.2308, "wr_high_end_rate": 0.4043,
             "absolute_difference_pp": -17.35},
            {"bucket": 4, "bucket_label": "37-48", "n_rb": 34, "n_wr": 45,
             "rb_high_end_rate": 0.1176, "wr_high_end_rate": 0.4222,
             "absolute_difference_pp": -30.46},
            {"bucket": 5, "bucket_label": "49-60", "n_rb": 32, "n_wr": 40,
             "rb_high_end_rate": 0.0938, "wr_high_end_rate": 0.225,
             "absolute_difference_pp": -13.12},
            {"bucket": 6, "bucket_label": "61-72", "n_rb": 27, "n_wr": 37,
             "rb_high_end_rate": 0.1481, "wr_high_end_rate": 0.2162,
             "absolute_difference_pp": -6.81},
        ],
    },
}

FULL = {
    "team_scoring_regression": REGRESSION_ARTIFACT,
    "rb_dead_zone_bucket_rates": DEAD_ZONE_ARTIFACT,
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
    # S21.1 carries n and an interval on every bucket too; only the label, the
    # two rates and the gap are read.
    assert "n_rb" not in page
    assert ">34<" not in page and ">45<" not in page


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


def test_the_tier_board_carries_the_price_beside_the_value():
    """S83: "with ADP alongside". A value with no price cannot be acted on."""
    page = sheet.render("test", profile=PROFILE, artifacts=FULL)
    assert "<th class=\"num\">ADP</th>" in page
    assert ">2<" in page          # Bijan, ADP 2.1
    assert ">41<" in page         # Player Two, ADP 41.2


def test_a_player_the_market_never_priced_says_so_rather_than_showing_a_zero():
    page = sheet.render("test", profile=PROFILE, artifacts=FULL)
    assert "Unpriced Man" in page
    # An em dash, not a 0 -- an unpriced player is not a free one.
    assert '<td class="num">&mdash;</td>' in page


def test_avoids_carries_the_band_its_rates_and_a_price_trigger():
    page = sheet.render("test", profile=PROFILE, artifacts=FULL)
    assert "DEAD ZONE" in page
    assert "picks 25&ndash;60" in page
    assert "12%" in page and "42%" in page      # the widest bucket, both positions
    assert "-30 pp" in page


def test_avoids_still_names_the_section_it_is_not():
    """A price band is not S28. Letting it read as S28 is the failure
    `_not_built` exists to prevent."""
    page = sheet.render("test", profile=PROFILE, artifacts=FULL)
    assert "S28" in page and "S79" in page
    assert "positional price band, not a list of players" in page


def test_the_band_follows_the_artifact_rather_than_a_number_typed_here():
    """A hard-coded band would print unchanged over a refreshed artifact."""
    moved = json.loads(json.dumps(DEAD_ZONE_ARTIFACT))
    rows = moved["primary_results"]["rb_vs_wr"]
    for row in rows:
        row["absolute_difference_pp"] = -1.0
    rows[5]["absolute_difference_pp"] = -25.0      # 61-72 is now the only gap

    page = sheet.render(
        "test", profile=PROFILE, artifacts={**FULL, "rb_dead_zone_bucket_rates": moved}
    )
    assert "picks 61&ndash;72" in page
    assert "picks 25&ndash;60" not in page


def test_a_band_nothing_clears_is_a_result_and_not_a_silence():
    flat = json.loads(json.dumps(DEAD_ZONE_ARTIFACT))
    for row in flat["primary_results"]["rb_vs_wr"]:
        row["absolute_difference_pp"] = -1.0
    page = sheet.render(
        "test", profile=PROFILE, artifacts={**FULL, "rb_dead_zone_bucket_rates": flat}
    )
    assert "flags no price band" in page
    assert "DEAD ZONE" not in page


def test_a_price_inside_the_band_is_marked_on_the_board():
    """The finding has to reach the pick, not sit eight lines below it."""
    page = sheet.render("test", profile=PROFILE, artifacts=FULL)
    assert '<td class="num dz">41</td>' in page      # Player Two, RB, ADP 41.2
    assert '<td class="num dz">2</td>' not in page   # Bijan is priced above it


def test_the_band_is_marked_where_the_pick_is_actually_made():
    """S21.1 sits at picks 25-60, which on a board sorted by value is past the end
    of the tier list -- and right in the middle of the survival candidates."""
    page = sheet.render("test", profile=PROFILE, slot=7, artifacts=FULL)
    survival = page.split("SURVIVAL")[1]
    assert '<td class="dz">41.2</td>' in survival     # the back
    assert '<td class="dz">30.0</td>' not in survival  # the receiver beside him


def test_the_band_is_not_applied_to_a_position_it_was_never_measured_on():
    """S21.1 measured running backs. A WR at pick 41 is not evidence."""
    assert sheet.in_dead_zone({"low": 25, "high": 60}, "WR", 41.2) is False
    assert sheet.in_dead_zone({"low": 25, "high": 60}, "RB", 41.2) is True
    assert sheet.in_dead_zone(None, "RB", 41.2) is False
    assert sheet.in_dead_zone({"low": 25, "high": 60}, "RB", None) is False


def test_survival_shows_a_probability_and_a_dash_where_there_is_none():
    page = sheet.render("test", profile=PROFILE, artifacts=FULL)
    assert "Pick 18" in page
    assert "2%" in page
    assert "Normal approximation" in page


def _many_slots() -> dict:
    """A survival artifact covering all twelve seats -- the undrawn state."""
    artifacts = dict(FULL)
    many = json.loads(json.dumps(SURVIVAL_ARTIFACT))
    many["primary_results"]["by_slot"] = [
        {"slot": s, "held_picks": [s, 25 - s], "picks": []} for s in range(1, 13)
    ]
    artifacts[f"{survival_mod.METHOD_ID}__fixture_12"] = many
    return artifacts


def test_an_undrawn_slot_does_not_silently_pick_one():
    """No seat is guessed. But the page does not go blank either.

    This drafter's order is drawn about an hour before the draft, so undrawn is
    the expected state, and a sheet that says BLOCKED in its expected state is the
    blank space somebody fills in from memory at pick 43.
    """
    page = sheet.render("test", profile=PROFILE, artifacts=_many_slots())
    assert "Draft order undrawn" in page
    assert "slot&lt;NN&gt;" in page          # says which file to open
    for seat in range(1, 13):
        assert f">{seat}</td>" in page       # and shows every seat's picks


def test_a_slot_renders_only_that_slot():
    page = sheet.render("test", profile=PROFILE, slot=7, artifacts=_many_slots())
    assert "Slot 7 holds 7, 18" in page
    assert "slot 7" in page                  # in the title too
    assert "Draft order undrawn" not in page
    assert "Slot 12 holds" not in page


def test_a_slot_the_artifact_does_not_cover_is_blocked_not_invented():
    page = sheet.render("test", profile=PROFILE, slot=44, artifacts=_many_slots())
    assert "BLOCKED" in page
    assert "slot 44 is not in the survival artifact" in page


def test_with_no_real_profile_the_sheet_says_it_is_not_a_league_sheet():
    """S14 excludes non-real profiles from the sheet. The honest output is the
    profile-independent one, labelled as such."""
    page = sheet.render("test", profile=None, artifacts=FULL)
    assert "This is not a league sheet" in page
    assert "real: true" in page


def test_the_page_is_self_contained():
    """S8: the sheet is used at a draft table. It cannot need a network."""
    page = sheet.render("test", profile=PROFILE, artifacts=FULL)
    for token in ("http://", "https://", "<script", "<link", "<img"):
        assert token not in page.lower()


def _seed(tmp_path) -> None:
    directory = tmp_path / "ed" / "methods"
    directory.mkdir(parents=True)
    for name, artifact in FULL.items():
        (directory / f"{name}.json").write_text(json.dumps(artifact))


def test_write_produces_a_sheet_per_seat_when_the_order_is_undrawn(tmp_path, monkeypatch):
    """The draw happens an hour before the draft; the rendering happens now."""
    monkeypatch.setattr(sheet, "real_profiles", lambda: [PROFILE])
    _seed(tmp_path)
    names = [p.name for p in sheet.write("ed", root=tmp_path)]
    assert names == (
        [f"fixture_12__slot{s:02d}.html" for s in range(1, 13)]
        + ["fixture_12.html", "index.html"]
    )
    assert "Bijan Robinson" in (tmp_path / "ed" / "sheets" / "fixture_12__slot01.html").read_text()


def test_a_configured_slot_produces_one_sheet_named_for_the_league(tmp_path, monkeypatch):
    monkeypatch.setattr(sheet, "real_profiles", lambda: [{**PROFILE, "draft_slot": 7}])
    _seed(tmp_path)
    names = [p.name for p in sheet.write("ed", root=tmp_path)]
    assert names == ["fixture_12.html", "index.html"]
    assert "slot 7" in (tmp_path / "ed" / "sheets" / "fixture_12.html").read_text()


def test_writing_one_slot_leaves_the_pre_rendered_set_alone(tmp_path, monkeypatch):
    """The draft-hour path: refresh one seat, do not rebuild twelve."""
    monkeypatch.setattr(sheet, "real_profiles", lambda: [PROFILE])
    _seed(tmp_path)
    sheet.write("ed", root=tmp_path)
    names = [p.name for p in sheet.write("ed", root=tmp_path, slot=7)]
    assert names == ["fixture_12__slot07.html", "index.html"]
    assert (tmp_path / "ed" / "sheets" / "fixture_12__slot01.html").exists()


def test_the_index_lists_every_seat_and_needs_no_network(tmp_path, monkeypatch):
    """S8: opened on a phone at a draft table, possibly with no signal."""
    monkeypatch.setattr(sheet, "real_profiles", lambda: [PROFILE])
    _seed(tmp_path)
    sheet.write("ed", root=tmp_path)
    index = (tmp_path / "ed" / "sheets" / "index.html").read_text()
    for seat in range(1, 13):
        assert f'href="fixture_12__slot{seat:02d}.html"' in index
    for token in ("http://", "https://", "<script", "<link", "<img"):
        assert token not in index
    sheet.assert_sheet_constraints(index)


def test_an_unknown_profile_id_is_an_error_not_an_empty_run(tmp_path, monkeypatch):
    monkeypatch.setattr(sheet, "real_profiles", lambda: [PROFILE])
    _seed(tmp_path)
    with pytest.raises(ValueError, match="no real league profile"):
        sheet.write("ed", root=tmp_path, profile_id="nope")


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

    assert paths[0].name == "e2e_12.html"   # slot 7 is configured, so one sheet
    assert "BLOCKED" not in page          # every gated section filled in
    assert "RB1" in page                  # tiers
    assert "Pick 18" in page              # survival at the real held picks
    assert "FADE" in page                 # regression
    assert page.count("NOT BUILT") == 4   # and only the genuinely unbuilt ones
    assert isinstance(pl.DataFrame(), pl.DataFrame) and dt.date.today()


# -- the one date on the page that can go stale (S84) -----------------------


def test_the_sheet_says_which_adp_capture_it_is_priced_from():
    """`generated` is written by the run that writes the page, so it is current
    even when the board underneath is weeks old. The capture date is the one a
    drafter at the table can actually check."""
    page = sheet.render("test", profile=PROFILE, slot=7, artifacts=FULL)
    captured = SURVIVAL_ARTIFACT["primary_results"]["adp_snapshot_date"]
    assert f"priced from the ADP capture of <strong>{captured}</strong>" in page
    sheet.assert_sheet_constraints(page)


def test_a_capture_older_than_today_is_called_out_rather_than_shown_quietly():
    """The failure this exists for is the refresh silently stopping."""
    page = sheet.render("test", profile=PROFILE, slot=7, artifacts=FULL)
    assert "not today" in page


def test_no_survival_artifact_means_unpriced_not_today():
    """Absent provenance must not render as fresh provenance."""
    artifacts = {k: v for k, v in FULL.items() if not k.startswith(survival_mod.METHOD_ID)}
    page = sheet.render("test", profile=PROFILE, artifacts=artifacts)
    assert "ADP not priced" in page
    assert "priced from the ADP capture" not in page


def test_the_index_carries_the_capture_date_and_flags_a_stale_one(tmp_path, monkeypatch):
    monkeypatch.setattr(sheet, "real_profiles", lambda: [PROFILE])
    _seed(tmp_path)
    sheet.write("ed", root=tmp_path)
    index = (tmp_path / "ed" / "sheets" / "index.html").read_text()
    captured = SURVIVAL_ARTIFACT["primary_results"]["adp_snapshot_date"]
    assert captured in index
    assert "daily refresh has not run" in index
