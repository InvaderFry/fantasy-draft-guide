"""Evaluate the outcome definitions in config/outcomes.yaml (S15).

S15: "Centralize outcomes in configuration. Do not scatter definitions through
code." and "Every chapter must link to the exact outcome definition used."
The file existed and nothing read it, so a chapter had no definition to link
to. This turns a definition into a boolean column and returns the definition
alongside it, so the artifact can record what was actually applied.

Only the types S88 Week 2 needs are implemented. `value_over_adp_baseline` and
`value_over_adp_percentile` are named in the config but have no formula
anywhere in the spec; asking for one raises rather than guessing, because a
guessed bust definition would silently become the published one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import polars as pl

from pipeline.config import outcomes as load_outcomes

SCORING_COLUMN = "fantasy_points_ppr"


class OutcomeError(KeyError):
    """The definition is missing, or its type has no implementation."""


@dataclass(frozen=True)
class Outcome:
    name: str
    definition: dict[str, Any]

    @property
    def type(self) -> str:
        return self.definition.get("type", "")

    @property
    def position(self) -> str | None:
        return self.definition.get("position")


def get(name: str) -> Outcome:
    definitions = load_outcomes()
    if name not in definitions:
        raise OutcomeError(
            f"outcome {name!r} is not defined in config/outcomes.yaml. S15 requires "
            "definitions to live in configuration, so add it there rather than here."
        )
    return Outcome(name, definitions[name])


def evaluate(frame: pl.DataFrame, name: str, *, scoring: str = SCORING_COLUMN) -> pl.DataFrame:
    """Attach a boolean column `name` to a player_season_outcomes frame.

    Rows outside the outcome's position are null, not False: a wide receiver
    did not fail to be a top-12 running back, the question does not apply.
    """
    outcome = get(name)
    if outcome.type == "positional_finish":
        hit = _positional_finish(frame, outcome, scoring)
    elif outcome.type == "positional_finish_ppg":
        hit = _positional_finish(frame, outcome, scoring, per_game=True)
    elif outcome.type == "ppg_threshold":
        hit = _ppg_threshold(frame, outcome, scoring)
    elif outcome.type == "games_threshold":
        hit = pl.col("games") >= outcome.definition["min_games"]
    else:
        raise OutcomeError(
            f"outcome {name!r} has type {outcome.type!r}, which has no implementation. "
            "The spec names it but gives no formula; implementing a guess would make "
            "the guess the published definition."
        )

    if outcome.position:
        hit = pl.when(pl.col("position") == outcome.position).then(hit).otherwise(None)
    return frame.with_columns(hit.alias(name))


def _rank_within_position(scoring: str, per_game: bool, min_games: int | None) -> pl.Expr:
    value = pl.col(scoring)
    if per_game:
        # Ranking a rate needs a games floor or a one-game cameo tops the table.
        value = pl.when(pl.col("games") >= (min_games or 0)).then(
            pl.col(scoring) / pl.col("games")
        )
    return value.rank("min", descending=True).over(["season", "position"])


def _positional_finish(
    frame: pl.DataFrame, outcome: Outcome, scoring: str, *, per_game: bool = False
) -> pl.Expr:
    ceiling = outcome.definition.get("max_finish") or outcome.definition["max_finish_ppg"]
    min_games = outcome.definition.get("min_games")
    return _rank_within_position(scoring, per_game, min_games) <= ceiling


def _ppg_threshold(frame: pl.DataFrame, outcome: Outcome, scoring: str) -> pl.Expr:
    min_games = outcome.definition.get("min_games", 0)
    return (pl.col("games") >= min_games) & (
        (pl.col(scoring) / pl.col("games")) >= outcome.definition["min_ppg"]
    )
