"""S19.3 -- tiers and value over replacement. NOT BUILT, and blocked twice.

Its kill rule in research/questions.yaml says "blocked, not killed", so this
module exists to state the blocks precisely rather than to half-build around
them.

**No real league profile.** S19.4: "Define [replacement level] from league
demand and starting requirements. Do not use the same replacement level for
1-QB and Superflex. Use the real league profiles from S14." Both profiles in
config/league_profiles.yaml are placeholders with `real: false` and TODO draft
dates, so `require_real_profiles()` raises. Replacement level is undefined
without teams and starter counts, and a tier board built on a guessed profile
would look exactly like a real one.

**No projections.** S19.3's MVP tier metric is
`player_value = projected_points - replacement_points(position)`, and there is
no projection source at all: `config/sources.yaml` has an empty
`projection_providers` map and the FantasyPros API is marked deferred. Filling
in a league profile alone will not unblock this -- the second gate has to be
cleared too, either by a manual projection CSV through
`pipeline/ingest/projections_csv.py` (S11 option 1B) or by substituting an
ADP-derived value curve, which would be a documented departure from S19.3's
stated input rather than a silent one.
"""

from __future__ import annotations

from pipeline.config import ConfigError, real_profiles, sources

METHOD_ID = "tiers_and_replacement_level"


class BlockedError(ConfigError):
    """A prerequisite is missing, and guessing at it would produce a plausible lie."""


def blockers() -> list[str]:
    """Everything standing between here and a tier board."""
    out = []
    if not real_profiles():
        out.append(
            "no league profile is marked `real: true` in config/league_profiles.yaml "
            "-- replacement level is undefined without teams and starter counts (S14, S19.4)"
        )
    if not (sources().get("projection_providers") or {}):
        out.append(
            "no projection source is configured in config/sources.yaml -- S19.3's tier "
            "metric is projected_points - replacement_points, and there are no "
            "projected points (S11)"
        )
    return out


def run() -> None:
    problems = blockers()
    if problems:
        raise BlockedError(
            f"{METHOD_ID} is blocked, not killed (S88 Week 2):\n  - " + "\n  - ".join(problems)
        )
    raise NotImplementedError(
        "prerequisites are met; the tier construction itself is still to be written (S19.3)"
    )
