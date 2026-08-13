"""Configuration gates (S14, S6.1, S3.1)."""

import datetime as dt

import pytest

from pipeline import config


def test_decision_dates_cover_the_research_window():
    dates = config.decision_dates()
    for season in range(2012, 2027):
        assert season in dates, f"S6.1 requires a decision date for {season}"
        assert isinstance(dates[season], dt.date)
        assert dates[season].month == 8, "decision dates are the last week of August (S6.1)"


def test_research_is_blocked_until_a_real_league_profile_exists():
    """S14: every downstream conclusion is conditional on scoring and roster structure."""
    if config.real_profiles():
        pytest.skip("real profiles are now encoded")
    with pytest.raises(config.ConfigError, match="real"):
        config.require_real_profiles()


# -- the two values that were never going to arrive in time -----------------


def test_the_shipped_profiles_are_real_and_validate():
    """S14's gate is closed, and it closed without a draft date or a slot.

    Both were unknowable at encode time -- the dates are unset and the order is
    drawn about an hour before the draft -- so a gate that waited for them would
    still be shut on draft morning.
    """
    profiles = config.require_real_profiles()
    assert len(profiles) == 2
    assert {p["id"] for p in profiles} == {"half_ppr_12", "ppr_12"}


@pytest.mark.parametrize("field", ["draft_date", "draft_slot"])
def test_unknown_is_an_answer_and_todo_is_not(field):
    """`unknown` is a decision the build handles; TODO is an unanswered question.

    Treating them alike is how a placeholder becomes twelve confidently rendered
    sheets nobody asked for.
    """
    base = {"id": "t", "teams": 12, "draft_date": "unknown", "draft_slot": "unknown"}
    assert config.validate_profile(base) is base
    with pytest.raises(config.ConfigError, match=field):
        config.validate_profile({**base, field: "TODO"})


def test_an_unknown_slot_reads_as_undrawn_and_a_number_reads_as_a_seat():
    base = {"id": "t", "teams": 12, "draft_date": "unknown"}
    assert config.draft_slot({**base, "draft_slot": "unknown"}) is None
    assert config.draft_slot({**base, "draft_slot": None}) is None
    assert config.draft_slot({**base, "draft_slot": 7}) == 7
    assert config.draft_slot({**base, "draft_slot": "7"}) == 7


def test_a_slot_outside_the_league_is_rejected_at_config_time():
    with pytest.raises(config.ConfigError, match="outside 1..12"):
        config.draft_slot({"id": "t", "teams": 12, "draft_slot": 13})


def test_an_unparseable_slot_does_not_fall_through_as_undrawn():
    with pytest.raises(config.ConfigError, match="neither a seat number"):
        config.draft_slot({"id": "t", "teams": 12, "draft_slot": "seven"})


def test_a_known_draft_date_still_parses():
    """Unknown today; S36.2 will want a real one, and it is read the same way."""
    assert config.draft_date({"id": "t", "draft_date": "unknown"}) is None
    assert config.draft_date({"id": "t", "draft_date": "2026-08-24"}) == dt.date(2026, 8, 24)


def test_every_profile_declares_scoring_and_starters():
    for profile in config.league_profiles():
        assert profile.get("scoring"), f"{profile['id']} has no scoring block"
        assert profile.get("starters"), f"{profile['id']} has no starters block"
        assert "teams" in profile, f"{profile['id']} has no team count"


def test_adp_capture_covers_every_encoded_profile():
    """S84 captures a superset; it must never be narrower than what is played.

    Over every profile, not only the `real: true` ones. The failure being
    defended against is flipping a league to real on draft week and finding it
    has no price history -- and by then the days are unrecoverable, so a check
    that waits for `real: true` arrives after it could have helped.

    Matching on (format, teams) rather than team count alone: a 12-team
    superflex league shares a team count with 12-team PPR and needs an entirely
    different board.
    """
    captured = {(c["format"], c["teams"]) for c in config.adp_capture_formats()}
    assert captured, "no ADP capture formats configured"
    profiles = config.league_profiles()
    assert profiles, "no league profiles encoded"
    for profile in profiles:
        wanted = (config.profile_adp_format(profile), profile["teams"])
        assert wanted in captured, (
            f"profile {profile['id']} needs {wanted[0]}/{wanted[1]}team ADP, which "
            f"config/league_profiles.yaml does not capture. Add it to `adp_capture` "
            f"now -- every day it is missing is a day of price movement that cannot "
            f"be bought back (S84). Captured: {sorted(captured)}"
        )


@pytest.mark.parametrize(
    ("scoring", "starters", "expected"),
    [
        ({"reception": 0}, {"QB": 1}, "standard"),
        ({"reception": 0.5}, {"QB": 1}, "half-ppr"),
        ({"reception": 1.0}, {"QB": 1}, "ppr"),
        # a second quarterback changes the board regardless of the reception value
        ({"reception": 1.0}, {"QB": 1, "SUPERFLEX": 1}, "2qb"),
        ({"reception": 0.5}, {"QB": 2}, "2qb"),
    ],
)
def test_a_profiles_adp_format_follows_from_its_scoring(scoring, starters, expected):
    profile = {"id": "x", "scoring": scoring, "starters": starters}
    assert config.profile_adp_format(profile) == expected


def test_a_scoring_system_with_no_published_adp_is_named_not_guessed():
    """A quarter-point reception has no FFC board; silently picking one would lie."""
    profile = {"id": "x", "scoring": {"reception": 0.25}, "starters": {"QB": 1}}
    with pytest.raises(config.UnknownFormatError, match="0.25"):
        config.profile_adp_format(profile)


def test_evidence_rules_are_committed_and_dated():
    """S3.1: thresholds are committed before results exist, by code, not by hand."""
    rules = config.load_yaml(config.CONFIG_DIR / "evidence_rules.yaml")["evidence_rules"]
    assert rules["version"]
    assert isinstance(rules["committed"], dt.date)
    for grade in ("A", "B", "C", "D", "F", "U"):
        assert grade in rules, f"evidence grade {grade} has no rule"
    assert rules["A"]["min_analysis_n"] >= rules["B"]["min_analysis_n"]


def test_sources_declare_license_and_attribution():
    for name, meta in config.sources().items():
        assert "purpose" in meta, f"source {name} declares no purpose (S46)"
        assert "url" in meta, f"source {name} declares no url (S46)"


def test_questions_carry_decision_value_and_a_kill_rule():
    """S2.3 and S68: no question is built without both."""
    registry = config.load_yaml(config.RESEARCH_DIR / "questions.yaml")["questions"]
    for q in registry:
        assert q.get("decision_change"), f"{q['id']} has no decision_change (S2.3)"
        if q.get("type") == "source_resolution":
            continue
        assert q.get("kill_rule"), f"{q['id']} has no kill_rule (S68)"
        assert q.get("power_status"), f"{q['id']} has no power_status (S5.1)"
