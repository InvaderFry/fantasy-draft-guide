"""The research layer's contracts (S2.2, S15, S16, S49).

These run on synthetic frames rather than the built tables, so they execute in
CI where data/processed is empty.
"""

import numpy as np
import polars as pl
import pytest

from research import outcomes, stats
from research.foundations import tiers
from research.method import ClaimTypeError, MethodArtifact
from research.teams import team_scoring_regression as tsr


def _outcome_frame() -> pl.DataFrame:
    """Twelve RBs and two WRs, scored so the top-12 boundary is unambiguous."""
    return pl.DataFrame(
        {
            "season": [2024] * 16,
            "player_id": [f"00-000{i:04d}" for i in range(16)],
            "position": ["RB"] * 14 + ["WR"] * 2,
            "games": [17] * 16,
            "fantasy_points_ppr": [float(300 - 10 * i) for i in range(16)],
        }
    )


def test_an_outcome_definition_comes_from_config_not_code():
    definition = outcomes.get("rb_high_end").definition
    assert definition["type"] == "positional_finish"
    assert definition["max_finish"] == 12


def test_an_undefined_outcome_raises_rather_than_defaulting():
    with pytest.raises(outcomes.OutcomeError, match="not defined"):
        outcomes.evaluate(_outcome_frame(), "no_such_outcome")


def test_an_outcome_the_spec_names_but_never_defines_raises():
    """`bust` is a named type with no formula anywhere; guessing would publish the guess."""
    with pytest.raises(outcomes.OutcomeError, match="no implementation"):
        outcomes.evaluate(_outcome_frame(), "bust")


def test_positional_finish_marks_exactly_the_top_n():
    frame = outcomes.evaluate(_outcome_frame(), "rb_high_end")
    assert frame.filter(pl.col("rb_high_end")).height == 12


def test_a_player_of_another_position_is_null_not_false():
    """A receiver did not fail to be a top-12 back; the question does not apply."""
    frame = outcomes.evaluate(_outcome_frame(), "rb_high_end")
    assert frame.filter(pl.col("position") == "WR")["rb_high_end"].null_count() == 2


def test_rb_usable_is_a_wider_net_than_rb_high_end():
    frame = outcomes.evaluate(outcomes.evaluate(_outcome_frame(), "rb_high_end"), "rb_usable")
    assert frame.filter(pl.col("rb_usable")).height >= frame.filter(pl.col("rb_high_end")).height


def test_ols_recovers_a_known_slope():
    rng = np.random.default_rng(11)
    x = rng.normal(size=400)
    y = 3.0 * x + 1.5
    fit = stats.ols(x.reshape(-1, 1), y, ["x"])
    assert fit.coefficients["x"] == pytest.approx(3.0, abs=1e-6)
    assert fit.intercept == pytest.approx(1.5, abs=1e-6)
    assert fit.r_squared == pytest.approx(1.0, abs=1e-9)


def test_a_wilson_interval_stays_inside_zero_and_one():
    """The normal interval goes negative on the small buckets S5.1 warns about."""
    _, low, high = stats.proportion_ci(0, 5)
    assert low == 0.0
    assert 0.0 < high < 1.0


def test_spearman_is_a_rank_correlation_not_a_linear_one():
    x = np.arange(1.0, 21.0)
    assert stats.spearman(x, x**3) == pytest.approx(1.0, abs=1e-9)


def test_an_artifact_rejects_a_claim_type_the_spec_does_not_define():
    with pytest.raises(ClaimTypeError, match="S2.2"):
        MethodArtifact(
            method_id="x", title="x", version="1.0.0", claim_type="PROVEN",
            population={}, outcome=None, sample_size=1,
        )


def test_an_artifact_carries_no_evidence_grade_field(tmp_path):
    """S88 forbids grading here, and a null grade invites something to fill it in."""
    artifact = MethodArtifact(
        method_id="x", title="x", version="1.0.0", claim_type="DESCRIPTIVE",
        population={}, outcome=None, sample_size=1,
    )
    assert "evidence_grade" not in artifact.to_dict()
    path = artifact.write("test-edition", root=tmp_path)
    assert path.exists()


def test_the_opportunity_model_excludes_the_conversion_rate():
    """Trips x conversion is the touchdown count; including it zeroes the residual."""
    assert "red_zone_td_rate" not in tsr.EXPECTATION_FEATURES


def test_consecutive_seasons_pair_forward_only():
    frame = pl.DataFrame(
        {"season": [2022, 2023, 2024], "team": ["AAA"] * 3, "offensive_tds": [10, 20, 30]}
    )
    paired = tsr._pair_consecutive_seasons(frame, ["offensive_tds"])
    assert paired.sort("season")["offensive_tds_next"].to_list() == [20, 30]


def test_z_scores_are_computed_within_season_not_across_the_window():
    """A 2012 team measured against a 2012-2025 mean reads as extreme for its era."""
    frame = pl.DataFrame(
        {
            "season": [2012, 2012, 2024, 2024],
            "team": ["AAA", "BBB", "AAA", "BBB"],
            "pass_rate": [0.50, 0.54, 0.60, 0.64],
        }
    )
    zed = tsr._z_within_season(frame, ["pass_rate"])
    # Identical spread in both seasons, so identical z-scores despite different levels.
    assert zed.sort("season", "team")["pass_rate_z"].to_list() == pytest.approx(
        [-0.7071, 0.7071, -0.7071, 0.7071], abs=1e-3
    )


def test_tiers_reports_both_blockers_rather_than_the_first():
    problems = tiers.blockers()
    assert len(problems) == 2
    assert any("real: true" in p for p in problems)
    assert any("projection" in p for p in problems)


def test_tiers_refuses_to_run_while_blocked():
    with pytest.raises(tiers.BlockedError, match="blocked, not killed"):
        tiers.run()
