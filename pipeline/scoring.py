"""Fantasy scoring from a league profile (S14).

Never hard-code one scoring system into the raw data. Points are computed on
demand from a profile, so the same weekly table serves half-PPR, PPR and
superflex leagues, and every research page can state which profile produced
the number it displays.

The function takes a week range rather than a whole season so it accepts a
mid-season roster state, not only a draft-day one (S87).
"""

from __future__ import annotations

from typing import Any

import polars as pl

# Stat column -> scoring key. Columns absent from a frame are treated as zero.
STAT_TO_RULE = {
    "passing_yards": "pass_yd",
    "passing_tds": "pass_td",
    "interceptions": "interception",
    "rushing_yards": "rush_yd",
    "rushing_tds": "rush_td",
    "receptions": "reception",
    "receiving_yards": "receiving_yd",
    "receiving_tds": "receiving_td",
    "fumbles_lost": "fumble_lost",
    "two_point_conversions": "two_point_conversion",
    "special_teams_tds": "special_teams_td",
}

# Standard / half-PPR / PPR differ only in the reception value; these three are
# precomputed on player_week so the common case needs no profile.
BUILT_IN_RECEPTION_VALUES = {
    "fantasy_points_standard": 0.0,
    "fantasy_points_half_ppr": 0.5,
    "fantasy_points_ppr": 1.0,
}

BASE_RULES = {
    "pass_yd": 0.04,
    "pass_td": 4,
    "interception": -2,
    "rush_yd": 0.10,
    "rush_td": 6,
    "receiving_yd": 0.10,
    "receiving_td": 6,
    "fumble_lost": -2,
    "two_point_conversion": 2,
    "special_teams_td": 6,
}


def points_expr(rules: dict[str, float], available: list[str]) -> pl.Expr:
    """Build the scoring expression for the stat columns actually present."""
    terms: list[pl.Expr] = []
    for stat, rule in STAT_TO_RULE.items():
        if stat not in available:
            continue
        weight = rules.get(rule)
        if not weight:
            continue
        terms.append(pl.col(stat).fill_null(0) * float(weight))
    if not terms:
        return pl.lit(0.0)
    expr = terms[0]
    for term in terms[1:]:
        expr = expr + term
    return expr


def score_frame(
    frame: pl.DataFrame, profile: dict[str, Any], alias: str = "fantasy_points"
) -> pl.DataFrame:
    """Attach a fantasy-points column computed under one league profile (S14)."""
    rules = profile.get("scoring") or {}
    if not rules:
        raise ValueError(f"league profile {profile.get('id')} has no `scoring` block (S14)")
    return frame.with_columns(points_expr(rules, frame.columns).alias(alias))


def built_in_scoring_exprs(available: list[str]) -> list[pl.Expr]:
    """Standard / half-PPR / PPR columns for player_week (S13)."""
    exprs = []
    for alias, reception_value in BUILT_IN_RECEPTION_VALUES.items():
        rules = {**BASE_RULES, "reception": reception_value}
        exprs.append(points_expr(rules, available).alias(alias))
    return exprs
