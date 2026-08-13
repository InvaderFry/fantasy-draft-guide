"""S25 -- team scoring and touchdown regression. DESCRIPTIVE (S2.2, S88).

Two questions, deliberately kept apart because they are not the same question.

**Does a team's touchdown total regress toward what its opportunity implies?**
The registry frames this as a residual model: fit expected offensive
touchdowns from opportunity, take the residual, and ask what it says about
next season. Its kill rule is numeric -- stop if the prior-season residual
explains under 2% of next-season variance -- so the R-squared is reported
whichever way it falls.

Note what the expectation model may NOT contain: `red_zone_td_rate`. Red-zone
trips times conversion rate is approximately the touchdown count, so including
the rate would absorb the very quantity the residual is supposed to isolate
and produce a residual of nearly zero for everyone. Opportunity means plays,
yards and red-zone trips; conversion is what we are measuring against them.

**Do extreme team rates move back toward the league?** This is S19.1's
method, which S25 inherits: z-score each metric within its season, pair each
team-season with its next season, and report the average change by z bucket
along with the share of teams that moved toward the mean at all.

Population: `team_season.parquet`, 448 team-seasons over 2012-2025, which S5.1
identifies as the best-powered population in the project. Pairing consecutive
seasons leaves 416.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl

from pipeline.config import PROCESSED_DIR
from research import stats
from research.method import MethodArtifact

METHOD_ID = "team_scoring_regression"
VERSION = "1.0.0"

# Opportunity, and nothing that already encodes conversion.
EXPECTATION_FEATURES = ("plays", "yards_per_play", "red_zone_trips", "pass_rate")

# S25's metric list, plus the two TD shares it defines inline.
REGRESSION_METRICS = (
    "offensive_tds",
    "pass_td_share",
    "rush_td_share",
    "plays_per_game",
    "pass_rate",
    "neutral_pass_rate",
    "red_zone_td_rate",
    "turnover_rate",
    "yards_per_play",
)

# S19.1 asks for E[next_change | z bucket]; these are the buckets.
Z_BUCKETS = ((-99.0, -2.0), (-2.0, -1.0), (-1.0, -0.5), (-0.5, 0.5),
             (0.5, 1.0), (1.0, 2.0), (2.0, 99.0))

# S19.1: "List teams/players at |z| >= 1.5 and |z| >= 2.0."
EXTREME_THRESHOLDS = (1.5, 2.0)

KILL_RULE_MIN_R2 = 0.02


def population(processed_dir=PROCESSED_DIR) -> pl.DataFrame:
    """team_season with S25's derived rates attached."""
    frame = pl.read_parquet(processed_dir / "team_season.parquet")
    return frame.with_columns(
        _ratio("passing_tds", "offensive_tds").alias("pass_td_share"),
        _ratio("rushing_tds", "offensive_tds").alias("rush_td_share"),
        _ratio("turnovers", "plays").alias("turnover_rate"),
    )


def _ratio(numerator: str, denominator: str) -> pl.Expr:
    return (
        pl.when(pl.col(denominator) > 0)
        .then(pl.col(numerator) / pl.col(denominator))
        .otherwise(None)
    )


def compute(frame: pl.DataFrame) -> dict[str, Any]:
    residuals = _td_residuals(frame)
    return {
        "td_residual": _residual_persistence(residuals),
        "regression_to_mean": _regression_to_mean(frame),
        "current_extremes": _current_extremes(frame),
    }


def _td_residuals(frame: pl.DataFrame) -> pl.DataFrame:
    """Actual minus opportunity-implied offensive touchdowns, per team-season."""
    usable = frame.drop_nulls([*EXPECTATION_FEATURES, "offensive_tds"])
    design = usable.select(EXPECTATION_FEATURES).to_numpy()
    target = usable["offensive_tds"].to_numpy().astype(float)
    fit = stats.ols(design, target, list(EXPECTATION_FEATURES))
    expected = fit.predict(design)
    return usable.select("season", "team", "offensive_tds").with_columns(
        pl.Series("expected_tds", expected),
        pl.Series("td_residual", target - expected),
        pl.lit(fit.r_squared).alias("_expectation_r2"),
    )


def _residual_persistence(residuals: pl.DataFrame) -> dict[str, Any]:
    """What last season's residual says about this one (the registry's kill rule)."""
    pairs = _pair_consecutive_seasons(
        residuals, ["offensive_tds", "td_residual", "expected_tds"]
    )
    if pairs.height == 0:
        return {"n": 0, "kill_rule_triggered": True}

    prior = pairs["td_residual"].to_numpy()
    next_tds = pairs["offensive_tds_next"].to_numpy().astype(float)
    next_residual = pairs["td_residual_next"].to_numpy()
    change = next_tds - pairs["offensive_tds"].to_numpy().astype(float)

    # The kill rule as written: variance in next-season TD *totals*.
    r2_next_total = stats.r_squared_of(prior, next_tds)
    # The question the decision actually turns on: does overperformance persist?
    r2_next_residual = stats.r_squared_of(prior, next_residual)

    mean_change, low, high = stats.mean_ci(change[prior > 0])
    return {
        "n": int(pairs.height),
        "expectation_model_r2": round(float(residuals["_expectation_r2"][0]), 4),
        "r2_residual_to_next_season_tds": round(r2_next_total, 4),
        "r2_residual_to_next_season_residual": round(r2_next_residual, 4),
        "spearman_residual_to_next_change": round(stats.spearman(prior, change), 4),
        "mean_td_change_after_overperformance": {
            "mean": round(mean_change, 3), "ci_low": round(low, 3), "ci_high": round(high, 3),
            "n": int((prior > 0).sum()),
        },
        "kill_rule": (
            f"stop if the prior-season TD residual explains under "
            f"{KILL_RULE_MIN_R2:.0%} of next-season variance"
        ),
        "kill_rule_triggered": r2_next_total < KILL_RULE_MIN_R2,
    }


def _regression_to_mean(frame: pl.DataFrame) -> list[dict[str, Any]]:
    """S19.1: E[next_change | z bucket], per metric, with the share moving toward the mean."""
    out: list[dict[str, Any]] = []
    zed = _z_within_season(frame, REGRESSION_METRICS)
    for metric in REGRESSION_METRICS:
        pairs = _pair_consecutive_seasons(
            zed.select("season", "team", metric, f"{metric}_z"), [metric, f"{metric}_z"]
        ).drop_nulls([metric, f"{metric}_next", f"{metric}_z"])
        if pairs.height == 0:
            continue
        z = pairs[f"{metric}_z"].to_numpy()
        change = pairs[f"{metric}_next"].to_numpy() - pairs[metric].to_numpy()
        out.append(
            {
                "metric": metric,
                "n": int(pairs.height),
                "spearman_z_to_change": round(stats.spearman(z, change), 4),
                "buckets": [_bucket_row(lo, hi, z, change) for lo, hi in Z_BUCKETS],
            }
        )
    return out


def _bucket_row(low: float, high: float, z: np.ndarray, change: np.ndarray) -> dict[str, Any]:
    mask = (z >= low) & (z < high)
    n = int(mask.sum())
    if n == 0:
        return {"z_from": low, "z_to": high, "n": 0}
    mean, ci_low, ci_high = stats.mean_ci(change[mask])
    # "Toward the mean" means the change ran opposite to the sign of z.
    toward = float((np.sign(change[mask]) != np.sign(z[mask])).mean())
    return {
        "z_from": low, "z_to": high, "n": n,
        "mean_next_change": round(mean, 4),
        "ci_low": round(ci_low, 4), "ci_high": round(ci_high, 4),
        "share_moving_toward_mean": round(toward, 4),
    }


def _current_extremes(frame: pl.DataFrame) -> dict[str, Any]:
    """S19.1's |z| >= 1.5 and >= 2.0 list, for the most recent season built."""
    season = int(frame["season"].max())
    zed = _z_within_season(frame, REGRESSION_METRICS).filter(pl.col("season") == season)
    listed: dict[str, Any] = {"season": season}
    for threshold in EXTREME_THRESHOLDS:
        rows = []
        for metric in REGRESSION_METRICS:
            hits = zed.filter(pl.col(f"{metric}_z").abs() >= threshold)
            rows.extend(
                {
                    "team": r["team"], "metric": metric,
                    "value": round(r[metric], 4) if r[metric] is not None else None,
                    "z": round(r[f"{metric}_z"], 3),
                    "direction": "above league" if r[f"{metric}_z"] > 0 else "below league",
                }
                for r in hits.select("team", metric, f"{metric}_z").to_dicts()
            )
        listed[f"abs_z_at_least_{threshold}"] = sorted(
            rows, key=lambda r: -abs(r["z"])
        )
    return listed


def _z_within_season(frame: pl.DataFrame, metrics) -> pl.DataFrame:
    """z against the same season's league, not the whole 14 years.

    Pass rates and scoring drift across a decade, so a 2012 team measured
    against a 2012-2025 mean would read as extreme for playing in 2012.
    """
    return frame.with_columns(
        [
            (
                (pl.col(m) - pl.col(m).mean().over("season")) / pl.col(m).std().over("season")
            ).alias(f"{m}_z")
            for m in metrics
        ]
    )


def _pair_consecutive_seasons(frame: pl.DataFrame, carry: list[str]) -> pl.DataFrame:
    """Join each team-season to the same team's next season (S6.1's Y -> Y+1 lag)."""
    nxt = frame.select(
        pl.col("season") - 1,
        pl.col("team"),
        *[pl.col(c).alias(f"{c}_next") for c in carry],
    )
    return frame.join(nxt, on=["season", "team"], how="inner")


def export(frame: pl.DataFrame, results: dict[str, Any]) -> MethodArtifact:
    seasons = sorted(frame["season"].unique().to_list())
    residual = results["td_residual"]
    return MethodArtifact(
        method_id=METHOD_ID,
        title="Team scoring and touchdown regression",
        version=VERSION,
        claim_type="DESCRIPTIVE",
        spec_sections=["S25", "S19.1", "S24"],
        population={
            "table": "team_season",
            "seasons": [seasons[0], seasons[-1]],
            "team_seasons": int(frame.height),
            "consecutive_season_pairs": residual["n"],
        },
        outcome="next_season_offensive_tds",
        sample_size=residual["n"],
        primary_results=results,
        limitations=[
            "DESCRIPTIVE only (S88). No evidence grade, no out-of-sample validation, "
            "and no prescriptive claim is made from it.",
            "The opportunity model excludes red_zone_td_rate by construction: trips x "
            "conversion is approximately the touchdown count, so including it would "
            "absorb the residual being measured.",
            "z-scores are computed within season, so a team is extreme relative to its "
            "own league year rather than to a 14-year mean.",
            "2022 Buffalo and Cincinnati played 16 games after a cancelled game; "
            "per-game metrics account for it, season totals do not.",
        ],
        sources=["nflverse play_by_play", "nflverse/nfldata schedules"],
    )


def run(processed_dir=PROCESSED_DIR) -> tuple[dict[str, Any], MethodArtifact]:
    frame = population(processed_dir)
    results = compute(frame)
    return results, export(frame, results)
