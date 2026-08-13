"""S31.2 / S19.4 -- will he make it back to my next pick? DESCRIPTIVE (S2.2, S88).

S19.4 calls this the most decision-relevant quantity in the guide and the one
asked most often during an actual draft. It is also the one the spec had to
promote into the MVP after r2 noticed that opportunity cost cannot be computed
without it.

**This is an approximation, and it is labelled as one everywhere it appears.**
S31.1 is resolved: Fantasy Football Calculator publishes a mean, a standard
deviation, a high, a low and a draft count per player, and no percentiles and no
per-pick histogram. So P(available at pick N) cannot be computed empirically
from the archive, and S19.4's stated fallback is the only route:

    P_available = 1 - Phi((next_pick - adp_mean) / adp_stdev)

Every artifact records ``opportunity_cost_method: normal_approximation``, which
S19.4 requires by name so that pages built on the approximation can be found and
regenerated if a real pick distribution ever arrives.

The approximation is worst where it matters most. Draft-pick distributions are
right-skewed and truncated at pick 1, so a player with an early ADP has a
normal-implied left tail that does not exist. ``calibration_note()`` reports the
size of that error from the high/low the source does publish.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from pipeline.config import PROCESSED_DIR, ConfigError, profile_adp_format, real_profiles
from research import stats
from research.method import MethodArtifact

METHOD_ID = "survival_probability"
VERSION = "1.0.0"

# S19.4's label, recorded in every artifact this module writes.
OPPORTUNITY_COST_METHOD = "normal_approximation"

# The sheet is one page (S83). Beyond this the survival numbers are all ~1.0 and
# nobody is consulting a sheet in round 16 anyway.
DEFAULT_ROUNDS = 15

UNKNOWN_SLOT = "unknown"


class BlockedError(ConfigError):
    """A prerequisite is missing, and guessing at it would produce a plausible lie."""


def blockers() -> list[str]:
    out = []
    if not real_profiles():
        out.append(
            "no league profile is marked `real: true` in config/league_profiles.yaml -- "
            "held picks depend on team count and draft slot (S14, S31.2)"
        )
    return out


def held_picks(teams: int, slot: int, rounds: int = DEFAULT_ROUNDS) -> list[int]:
    """The overall pick numbers one drafter actually owns, in a snake draft.

    Odd rounds run 1..teams, even rounds run back. This is the whole reason the
    sheet is generated per profile: the same board hands slot 1 and slot 12
    entirely different questions.
    """
    if not 1 <= slot <= teams:
        raise ValueError(f"draft slot {slot} is outside 1..{teams}")
    picks = []
    for rnd in range(1, rounds + 1):
        offset = slot if rnd % 2 else teams - slot + 1
        picks.append((rnd - 1) * teams + offset)
    return picks


def probability_available(next_pick: int, adp: float, adp_stdev: float | None) -> float | None:
    """S19.4's labelled fallback. None when the source published no spread.

    Returning None rather than a point estimate is deliberate: with no stdev the
    only honest answer is that the spread is unknown, and a 0/1 step function at
    the mean would read on the sheet as certainty.
    """
    if adp is None or adp_stdev is None or adp_stdev <= 0:
        return None
    return 1.0 - stats.normal_cdf((next_pick - adp) / adp_stdev)


def calibration_note(adp: float, adp_stdev: float, pick_high: float | None) -> str | None:
    """How far the normal approximation strays on this player.

    A pick distribution is truncated at 1 and skewed right; a normal is neither.
    Where the earliest pick the source has ever recorded is closer to the mean
    than the normal's own 2-sigma reach, the approximation is putting probability
    mass on picks that never happened.
    """
    if pick_high is None or adp_stdev <= 0:
        return None
    implied_earliest = adp - 2 * adp_stdev
    if implied_earliest < pick_high - 0.5:
        return (
            f"normal approximation implies picks as early as {implied_earliest:.1f}; "
            f"the earliest actually recorded is {pick_high:.0f}"
        )
    return None


def latest_adp(
    profile: dict[str, Any], *, processed_dir=PROCESSED_DIR, season: int | None = None
) -> pl.DataFrame:
    """The most recent archived ADP capture for this league's format."""
    path = processed_dir / "adp_history.parquet"
    if not path.exists():
        raise BlockedError(
            "data/processed/adp_history.parquet has not been built. Run "
            "`research build-tables --tables adp_history` (S13)."
        )
    frame = pl.read_parquet(path)
    fmt = profile_adp_format(profile)
    teams = int(profile["teams"])
    frame = frame.filter((pl.col("format") == fmt) & (pl.col("teams") == teams))
    if season is not None:
        frame = frame.filter(pl.col("season") == season)
    if not frame.height:
        raise BlockedError(
            f"no archived ADP for format {fmt!r} / {teams} teams"
            + (f" / season {season}" if season else "")
            + ". S84's capture list must cover every real profile."
        )
    newest = frame["snapshot_date"].max()
    return frame.filter(pl.col("snapshot_date") == newest).sort("adp")


def compute(
    adp: pl.DataFrame,
    profile: dict[str, Any],
    *,
    rounds: int = DEFAULT_ROUNDS,
) -> dict[str, Any]:
    """Survival at each held pick, for the slot(s) this profile could draft from.

    An undrawn slot is a real state -- config/league_profiles.yaml anticipates
    `draft_slot: unknown` -- and it is answered by reporting every slot rather
    than by guessing one. The sheet then still works the moment the order is
    drawn.
    """
    teams = int(profile["teams"])
    slot = profile.get("draft_slot")
    slots = (
        list(range(1, teams + 1))
        if slot in (None, UNKNOWN_SLOT, "TODO")
        else [int(slot)]
    )

    with_spread = adp.filter(pl.col("adp_stdev").is_not_null() & (pl.col("adp_stdev") > 0)).height
    by_slot = []
    for s in slots:
        picks = held_picks(teams, s, rounds)
        by_slot.append(
            {
                "slot": s,
                "held_picks": picks,
                "picks": [
                    _pick_block(adp, pick, picks[i + 1] if i + 1 < len(picks) else None)
                    for i, pick in enumerate(picks)
                ],
            }
        )

    return {
        "profile_id": profile.get("id"),
        "profile_label": profile.get("label"),
        "teams": teams,
        "draft_slot": slot if slots != list(range(1, teams + 1)) else UNKNOWN_SLOT,
        "rounds": rounds,
        "opportunity_cost_method": OPPORTUNITY_COST_METHOD,
        "adp_snapshot_date": str(adp["snapshot_date"].max()),
        "adp_format": profile_adp_format(profile),
        "players_priced": adp.height,
        "players_with_spread": with_spread,
        "by_slot": by_slot,
        "n": adp.height,
    }


# How many candidates to carry per pick. The sheet is one page; a drafter
# scanning at a 90-second clock reads the top of the list, not all 200.
CANDIDATES_PER_PICK = 12

# A player whose mean ADP sits slightly before the pick can still be sitting
# there, and he is often the most interesting name on the list. Half a round is
# wide enough to catch him and narrow enough not to fill the block with players
# who left in the first round.
AVAILABILITY_BUFFER = 6


def _pick_block(adp: pl.DataFrame, pick: int, next_pick: int | None) -> dict[str, Any]:
    """Who is plausibly on the board at this pick, and whether he lasts to the next.

    This is S19.4's question in its operational form -- "what is lost by not
    taking him now" is answered by P(he is still there at my next pick), not by
    P(he is there at this one, where I am currently sitting).

    Getting that distinction wrong is not a rounding error. Keyed on the current
    pick and drawing candidates from a window that reached back past pick 1, the
    block listed the highest-ADP players in the league at every pick and gave
    every one of them a 0% chance -- true, useless, and printed on a sheet
    somebody drafts from.

    The last held pick has no next one, so it reports availability at itself and
    says so.
    """
    window = adp.filter(pl.col("adp") >= pick - AVAILABILITY_BUFFER).head(CANDIDATES_PER_PICK)
    target = next_pick if next_pick is not None else pick
    rows = []
    for row in window.iter_rows(named=True):
        p = probability_available(target, row["adp"], row["adp_stdev"])
        rows.append(
            {
                "player": row["source_player_name"],
                "position": row["position"],
                "team": row["team"],
                "adp": round(float(row["adp"]), 1),
                "adp_stdev": (
                    round(float(row["adp_stdev"]), 1) if row["adp_stdev"] is not None else None
                ),
                "p_available": None if p is None else round(p, 3),
                "approximation_note": (
                    calibration_note(row["adp"], row["adp_stdev"], row.get("pick_high"))
                    if row["adp_stdev"]
                    else "no spread published; survival not computable"
                ),
            }
        )
    return {
        "pick": pick,
        "survival_measured_at": target,
        "is_last_pick": next_pick is None,
        "candidates": rows,
    }


def export(results: dict[str, Any], profile: dict[str, Any]) -> MethodArtifact:
    return MethodArtifact(
        method_id=f"{METHOD_ID}__{profile['id']}",
        title=f"Survival probability at held picks -- {profile.get('label')}",
        version=VERSION,
        claim_type="DESCRIPTIVE",
        spec_sections=["S31.2", "S19.4", "S31.1", "S14"],
        population={
            "registry_id": METHOD_ID,
            "profile_id": profile.get("id"),
            "teams": profile.get("teams"),
            "draft_slot": results.get("draft_slot"),
            "adp_format": results.get("adp_format"),
            "adp_snapshot_date": results.get("adp_snapshot_date"),
            "opportunity_cost_method": OPPORTUNITY_COST_METHOD,
        },
        outcome=None,
        sample_size=results.get("n", 0),
        primary_results=results,
        limitations=[
            "NORMAL APPROXIMATION, not an empirical survival curve. S31.1 is resolved: "
            "Fantasy Football Calculator publishes a mean, stdev, high, low and draft "
            "count, and no percentiles, so P(available) cannot be computed from the "
            "distribution. S19.4's labelled fallback is used and recorded as "
            f"opportunity_cost_method: {OPPORTUNITY_COST_METHOD}.",
            "Pick distributions are truncated at pick 1 and right-skewed; a normal is "
            "neither, so early-ADP players carry a left tail that cannot happen. Affected "
            "rows carry an approximation_note.",
            "ADP is the Fantasy Football Calculator mock-draft population, not the league "
            "being drafted, and not the whole market (S10B). A home league with keepers or "
            "a different scoring lean will not follow it.",
            "The figure is a rolling window average, not a same-day price (S31.3); the "
            "window length varies by format and is carried on adp_history.",
            "Survival is computed per player independently. Runs on a position -- four "
            "backs going in six picks -- are correlated in reality and are not modelled.",
        ],
        sources=["Fantasy Football Calculator ADP archive (S84)", "league profile (S14)"],
    )


def run(processed_dir=PROCESSED_DIR) -> list[tuple[dict[str, Any], MethodArtifact]]:
    problems = blockers()
    if problems:
        raise BlockedError(
            f"{METHOD_ID} is blocked, not killed:\n  - " + "\n  - ".join(problems)
        )
    out = []
    for profile in real_profiles():
        adp = latest_adp(profile, processed_dir=processed_dir)
        results = compute(adp, profile)
        out.append((results, export(results, profile)))
    return out
