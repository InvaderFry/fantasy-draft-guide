"""S19.3 / S19.4 -- tiers and value over replacement. DESCRIPTIVE (S2.2, S88).

The question S19.3 asks is not "who is better" but "where does the board break":
which players are close enough in value that the order between them is noise,
and where is the drop worth reaching for.

**The metric is S19.3's, verbatim.** ``player_value = projected_points -
replacement_points(position)``. Nothing is added to it -- no ADP blend, no
uncertainty adjustment, no positional multiplier. Those are S39 and S56 and they
grade evidence, which S88 forbids here.

**The market price is carried beside that metric, never inside it.** S83
specifies the tier board "with ADP alongside", because a value with no price
attached cannot answer the question asked at a live pick -- not "is he good" but
"is he good *here*". So ``adp`` and ``position_adp`` ride along as their own
columns and the value above is computed exactly as if they were absent. A
column is not a blend; the moment one is folded into the other this module is
doing S56's job without S56's evidence.

**Replacement level is per profile and never shared.** S19.4: "Do not use the
same replacement level for 1-QB and Superflex." It is computed from
``teams x starters`` for the specific league, which is why S14 gates this module
on a profile marked ``real: true``: a tier board built on a guessed team count
looks exactly like a real one and is wrong in a way nobody can see.

**Two gates, and the module stays blocked-not-killed while either is shut.**
Its kill rule in research/questions.yaml says so. As of this writing both are
shut -- no profile is real, and no projection source is configured -- so
``run()`` reports both and refuses. Everything below the gates is built and
tested against fixtures; clearing the gates is the whole remaining change.
"""

from __future__ import annotations

import statistics
from typing import Any

import polars as pl

from pipeline.config import (
    PROCESSED_DIR,
    ConfigError,
    draft_season,
    projection_source_available,
    real_profiles,
)
from pipeline.features import projections as projection_table
from pipeline.normalize.names import name_position_key
from pipeline.scoring import score_frame
from research.foundations import price_movement
from research.foundations import survival as survival_mod
from research.method import MethodArtifact

METHOD_ID = "tiers_and_replacement_level"
VERSION = "1.0.0"

# A break is an adjacent value gap at least this many times the median adjacent
# gap for the position. Committed here rather than tuned against an output:
# S80 prohibits choosing a threshold after seeing what it produces. 2.0 says "the
# step down to the next player is twice a normal step", which is the plain-language
# reading of a tier boundary.
TIER_BREAK_MULTIPLE = 2.0

# Positions with scoring rules and a replacement level. Kickers and defences are
# drafted but S88 studies none of them, and they have no scoring configured.
POSITIONS = ("QB", "RB", "WR", "TE")

# Below this many players at a position the median adjacent gap is not a stable
# denominator, and every gap looks like a break.
MIN_PLAYERS_FOR_BREAKS = 6


class BlockedError(ConfigError):
    """A prerequisite is missing, and guessing at it would produce a plausible lie."""


def projections_archived(processed_dir=PROCESSED_DIR) -> bool:
    """Whether a projection capture has actually landed in the table.

    Deliberately not `projection_source_available()`, which asks whether this
    machine could FETCH projections. Those are different questions, and answering
    the second one here made the blocker permanent on every machine without the
    API key -- including this one, and including every rebuild from the committed
    archive, which is the point of committing it. What S19.3 needs is projected
    points on disk; where they were fetched from, and by whom, does not matter.
    """
    path = processed_dir / "projection_snapshot.parquet"
    if not path.exists():
        return False
    return pl.scan_parquet(path).select(pl.len()).collect().item() > 0


def blockers(processed_dir=PROCESSED_DIR) -> list[str]:
    """Everything standing between here and a tier board."""
    out = []
    if not real_profiles():
        out.append(
            "no league profile is marked `real: true` in config/league_profiles.yaml "
            "-- replacement level is undefined without teams and starter counts (S14, S19.4)."
        )
    if not projections_archived(processed_dir):
        detail = (
            "run `research snapshot --sources projections` then `research build-tables "
            "--tables projection_snapshot` (S13)"
            if projection_source_available()
            else (
                "and no source is configured to capture one: set FANTASYPROS_API_KEY "
                "(S11 option 1) or declare a manual export under `projection_providers` "
                "in config/sources.yaml (S11 option 1B)"
            )
        )
        out.append(
            "no projection capture has landed -- S19.3's tier metric is "
            f"projected_points - replacement_points, and there are no projected points; "
            f"{detail}."
        )
    return out


# -- the board -------------------------------------------------------------


def board(
    profile: dict[str, Any],
    *,
    processed_dir=PROCESSED_DIR,
    frame: pl.DataFrame | None = None,
    season: int | None = None,
) -> pl.DataFrame:
    """Every projected player, priced under one league profile.

    Points come from ``pipeline.scoring.score_frame``, so the profile's own
    scoring rules produce them and a reception is worth what the league says it
    is worth. The projection columns are already the stat names the scorer
    expects, so no second scoring path exists to drift from the first.
    """
    if frame is None:
        path = processed_dir / "projection_snapshot.parquet"
        if not path.exists():
            raise BlockedError(
                "data/processed/projection_snapshot.parquet has not been built. "
                "Run `research snapshot --sources projections` then "
                "`research build-tables --tables projection_snapshot` (S13)."
            )
        frame = pl.read_parquet(path)

    frame = projection_table.latest(frame, season=season)
    if not frame.height:
        raise BlockedError(
            "projection_snapshot is empty, so there are no projected points to build a "
            "board from (S19.3). The table exists; no capture has landed in it."
        )

    frame = frame.filter(pl.col("position").is_in(list(POSITIONS)))
    frame = score_frame(frame, profile, alias="projected_points")
    return frame.filter(pl.col("projected_points") > 0).sort("projected_points", descending=True)


def market_price(
    profile: dict[str, Any], *, processed_dir=PROCESSED_DIR
) -> tuple[pl.DataFrame | None, dict[str, Any]]:
    """The latest archived ADP for this league with S31.3's movement attached.

    ``survival.priced_board`` already resolves the profile's ADP format, filters to
    the newest capture, refuses a stale-but-plausible mixture of seasons and
    measures the move since the prior capture; it is reused rather than
    reimplemented so the tier board and the survival blocks cannot quote the price
    as of different days.

    The failure is swallowed on purpose. S84's archive and S11's projections are
    separate failure domains, and this module is gated on the projections. A
    morning when the capture job did not run degrades the board to an unpriced
    one; it does not withhold it. ``blockers()`` keeps its two gates and gains no
    third.
    """
    try:
        return survival_mod.priced_board(
            profile, processed_dir=processed_dir, season=draft_season(profile)
        )
    except survival_mod.BlockedError as exc:
        return None, price_movement.unavailable(str(exc))


def _name_key() -> pl.Expr:
    """S12's weaker join key, for rows one side or the other never matched."""
    return (
        pl.struct(["source_player_name", "position"])
        .map_elements(
            lambda s: name_position_key(s["source_player_name"], s["position"]),
            return_dtype=pl.String,
        )
        .alias("_name_key")
    )


def _adp_coverage(
    total: int, priced: int, snapshot_date: str | None, market_rows: int = 0
) -> dict[str, Any]:
    """How much of the board the market actually priced.

    Reported for the same reason ``projections.coverage`` is: a join that half
    fails renders as a column of dashes, which reads on the sheet like a fringe
    nobody drafts rather than like a broken join.

    ``priced_share`` alone cannot tell those apart. A provider projects several
    hundred players and the ADP source prices about two hundred, so most of the
    unpriced tail is a market that never quoted them rather than a join that lost
    them. ``market_rows_in_scope`` is the denominator that answers the question
    actually being asked -- of the players the market did price, how many landed.
    """
    return {
        "board_rows": total,
        "priced": priced,
        "unpriced": total - priced,
        "priced_share": round(priced / total, 4) if total else None,
        "market_rows_in_scope": market_rows,
        "matched_share_of_market": round(priced / market_rows, 4) if market_rows else None,
        "adp_snapshot_date": snapshot_date,
    }


# What rides across from the market capture onto the board, and as what.
#
# One dict rather than three parallel lists of column names: the join below
# resolves each column twice, by id and by name, and coalesces the two. Written
# out per column, a new one arrives null for exactly the players the id join
# missed -- which looks like a fringe nobody prices rather than like a column
# that was forgotten in one of the three places.
CARRIED_PRICE_COLUMNS: dict[str, Any] = {
    "adp": pl.Float64,
    "position_adp": pl.Int64,
    "adp_delta": pl.Float64,   # S31.3, negative = going earlier
}


def attach_adp(
    board_frame: pl.DataFrame, adp: pl.DataFrame | None
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Carry the market price onto the board (S83, S19.3).

    Joined on ``player_id`` first -- ``adp_history`` and ``projection_snapshot``
    both carry S12's ``gsis_id`` under that name, so it is the same crosswalk on
    both sides -- then on normalized name and position for whatever is left. An
    ambiguous name prices nobody, which is ``match_external``'s own rule for its
    loose key: two players sharing a name and a position cannot be told apart, and
    guessing puts one man's price on another man's row.

    A player who matches neither keeps his row with a null price. Dropping him
    would change how many players sit above replacement, and the whole board is
    measured from there.
    """
    unpriced = tuple(
        pl.lit(None, dtype=dtype).alias(col) for col, dtype in CARRIED_PRICE_COLUMNS.items()
    )
    if adp is None or not adp.height or not board_frame.height:
        return (
            board_frame.with_columns(*unpriced),
            _adp_coverage(board_frame.height, 0, None),
        )

    captured = str(adp["snapshot_date"].max()) if "snapshot_date" in adp.columns else None
    # Kickers and defences are drafted and priced; no S88 module scores them and
    # the board never carries them. Counting them as unmatched would make the
    # join look broken every single day.
    adp = adp.filter(pl.col("position").is_in(list(POSITIONS)))
    # A caller may hand over a capture with no S31.3 movement attached -- an
    # archive one day old has none to attach. The column is added empty rather
    # than special-cased in the join below, so every carried column takes exactly
    # the same path and none can be quietly dropped on the way through.
    for col, dtype in CARRIED_PRICE_COLUMNS.items():
        if col not in adp.columns:
            adp = adp.with_columns(pl.lit(None, dtype=dtype).alias(col))
    price = adp.select(
        "player_id", "source_player_name", "position", *CARRIED_PRICE_COLUMNS
    ).with_columns(_name_key())

    by_id = (
        price.filter(pl.col("player_id").is_not_null())
        .sort("adp")
        .select(
            pl.col("player_id"),
            *(pl.col(col).alias(f"_id_{col}") for col in CARRIED_PRICE_COLUMNS),
        )
        .unique(subset="player_id", keep="first")
    )
    by_name = (
        price.select(
            pl.col("_name_key"),
            *(pl.col(col).alias(f"_name_{col}") for col in CARRIED_PRICE_COLUMNS),
        )
        # keep="none": an ambiguous key resolves to nothing (S12).
        .unique(subset="_name_key", keep="none")
    )

    joined = (
        board_frame.with_columns(_name_key())
        # The loose key has to be unambiguous on *both* sides. `match_external`
        # dedups only the crosswalk, because there each source row is one row;
        # here the board itself can carry two players sharing a name and a
        # position, and a single ADP row would then price both of them.
        .with_columns(pl.len().over("_name_key").alias("_name_key_rows"))
        .join(by_id, on="player_id", how="left")
        .join(by_name, on="_name_key", how="left")
        .with_columns(
            *(
                pl.when(pl.col("_name_key_rows") > 1)
                .then(None)
                .otherwise(pl.col(f"_name_{col}"))
                .alias(f"_name_{col}")
                for col in CARRIED_PRICE_COLUMNS
            )
        )
        .with_columns(
            *(
                pl.coalesce(f"_id_{col}", f"_name_{col}").cast(dtype).alias(col)
                for col, dtype in CARRIED_PRICE_COLUMNS.items()
            )
        )
        .drop(
            "_name_key",
            "_name_key_rows",
            *(f"_id_{col}" for col in CARRIED_PRICE_COLUMNS),
            *(f"_name_{col}" for col in CARRIED_PRICE_COLUMNS),
        )
    )
    priced = joined.filter(pl.col("adp").is_not_null()).height
    return joined, _adp_coverage(joined.height, priced, captured, adp.height)


def chosen_provider(frame: pl.DataFrame) -> str:
    """Which provider the board is drawn from, when several are archived.

    S38.1 forbids averaging providers into a consensus row, because the spread
    between them is the only proxy for projection uncertainty we have. So one is
    picked by name and the spread is reported alongside, rather than a mean being
    computed and the spread thrown away.
    """
    providers = sorted(frame["provider_id"].unique().drop_nulls().to_list())
    return providers[0] if providers else "unknown"


def provider_dispersion(frame: pl.DataFrame) -> dict[str, Any]:
    """Cross-provider spread on projected points (S38.1).

    The number S38.1 exists to preserve. With one provider archived there is no
    spread to report, and that absence is itself worth stating: a board built on
    a single provider carries none of its own uncertainty.
    """
    providers = sorted(frame["provider_id"].unique().drop_nulls().to_list())
    if len(providers) < 2:
        return {
            "providers": providers,
            "measurable": False,
            "note": (
                "one provider archived, so projection uncertainty cannot be estimated "
                "from cross-provider spread (S38.1). The board carries no error bar."
            ),
        }
    spread = (
        frame.group_by(["source_player_name", "position"])
        .agg(pl.col("projected_points").std().alias("sd"))
        .filter(pl.col("sd").is_not_null())
    )
    return {
        "providers": providers,
        "measurable": True,
        "median_sd_points": round(float(spread["sd"].median() or 0.0), 2),
        "players_compared": spread.height,
    }


# -- replacement level (S19.4) ---------------------------------------------


def positional_demand(profile: dict[str, Any], ranked: dict[str, list[float]]) -> dict[str, int]:
    """How many players at each position a league actually starts (S19.4).

    Base demand is ``teams x starters[position]``. FLEX is the part that cannot
    be read off the profile: a flex slot belongs to whichever position supplies
    the better player, so it is allocated rather than assumed. Every flex-eligible
    player ranked below their own base cutoff competes for the ``teams x flex``
    slots, best points first, and where those slots land is the allocation.

    Doing it the lazy way -- splitting flex evenly, or charging it all to RB --
    moves the replacement baseline for two positions at once, and every value on
    the board is measured from that baseline.
    """
    teams = int(profile["teams"])
    starters = profile.get("starters") or {}
    demand = {pos: teams * int(starters.get(pos, 0)) for pos in POSITIONS}

    flex_slots = teams * int(starters.get("FLEX", 0))
    if flex_slots <= 0:
        return demand

    eligible = [p for p in (profile.get("flex_eligible") or []) if p in POSITIONS]
    pool: list[tuple[float, str]] = []
    for pos in eligible:
        for points in ranked.get(pos, [])[demand[pos] :]:
            pool.append((points, pos))
    pool.sort(key=lambda item: item[0], reverse=True)
    for _points, pos in pool[:flex_slots]:
        demand[pos] += 1
    return demand


def replacement_points(
    ranked: dict[str, list[float]], demand: dict[str, int]
) -> dict[str, dict[str, Any]]:
    """The first player at each position nobody has to start.

    Rank ``demand + 1``: with 12 teams starting 2 RBs, RB25 is the back available
    to a manager who took none, so the value of RB1 is what he scores above RB25.
    Where the board runs out before demand is met, the last player is used and
    the shortfall is recorded -- silently substituting the last available player
    would understate every value at that position.
    """
    out: dict[str, dict[str, Any]] = {}
    for pos in POSITIONS:
        points = ranked.get(pos, [])
        need = demand.get(pos, 0)
        if not points or need <= 0:
            out[pos] = {"points": None, "rank": None, "truncated": bool(need > 0)}
            continue
        index = min(need, len(points) - 1)
        out[pos] = {
            "points": round(float(points[index]), 2),
            "rank": index + 1,
            "demand": need,
            "truncated": need >= len(points),
        }
    return out


# -- tier breaks (S19.3) ---------------------------------------------------


def assign_tiers(values: list[float], multiple: float = TIER_BREAK_MULTIPLE) -> list[int]:
    """Tier number per player, from adjacent value gaps.

    S19.3 permits adjacent gaps, change-point detection or hierarchical
    clustering. Adjacent gaps are chosen because the sheet has to be readable at
    a live pick: a drafter can check a gap against the board in front of them,
    and cannot check a dendrogram.

    A short list gets one tier. With five players every gap is either the median
    or twice it, and the tiers would describe the sample size.
    """
    if not values:
        return []
    if len(values) < MIN_PLAYERS_FOR_BREAKS:
        return [1] * len(values)
    gaps = [values[i] - values[i + 1] for i in range(len(values) - 1)]
    median_gap = statistics.median(gaps)
    threshold = median_gap * multiple
    tiers = [1]
    current = 1
    for gap in gaps:
        # A degenerate board (every gap identical, median 0) gets one tier rather
        # than one tier per player.
        if threshold > 0 and gap >= threshold:
            current += 1
        tiers.append(current)
    return tiers


def compute(
    frame: pl.DataFrame,
    profile: dict[str, Any],
    *,
    adp: pl.DataFrame | None = None,
    movement: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider = chosen_provider(frame)
    single = frame.filter(pl.col("provider_id") == provider)
    single, adp_coverage = attach_adp(single, adp)

    ranked = {
        pos: single.filter(pl.col("position") == pos)["projected_points"].to_list()
        for pos in POSITIONS
    }
    demand = positional_demand(profile, ranked)
    replacement = replacement_points(ranked, demand)

    positions: dict[str, Any] = {}
    for pos in POSITIONS:
        rows = single.filter(pl.col("position") == pos)
        base = replacement[pos]["points"]
        if base is None or not rows.height:
            positions[pos] = {"players": [], "replacement": replacement[pos]}
            continue
        values = [round(float(p) - base, 2) for p in rows["projected_points"].to_list()]
        tiers = assign_tiers(values)
        positions[pos] = {
            "replacement": replacement[pos],
            "tier_count": max(tiers) if tiers else 0,
            "players": [
                {
                    "player": name,
                    "team": team,
                    "tier": tier,
                    "projected_points": round(float(points), 1),
                    "value_over_replacement": value,
                    "adp": None if price is None else round(float(price), 1),
                    "position_adp": None if pos_rank is None else int(pos_rank),
                    # S31.3. Negative means the market is taking him earlier than
                    # it was at the prior capture; read it through
                    # price_movement.direction(), never by testing the sign.
                    "adp_delta": None if delta is None else round(float(delta), 1),
                }
                for name, team, points, value, tier, price, pos_rank, delta in zip(
                    rows["source_player_name"].to_list(),
                    rows["team"].to_list(),
                    rows["projected_points"].to_list(),
                    values,
                    tiers,
                    rows["adp"].to_list(),
                    rows["position_adp"].to_list(),
                    rows["adp_delta"].to_list(),
                    strict=True,
                )
            ],
        }

    return {
        "profile_id": profile.get("id"),
        "profile_label": profile.get("label"),
        "teams": profile.get("teams"),
        "provider": provider,
        "tier_break_multiple": TIER_BREAK_MULTIPLE,
        "positional_demand": demand,
        "replacement_points": replacement,
        "positions": positions,
        "provider_dispersion": provider_dispersion(frame),
        "coverage": projection_table.coverage(frame),
        "adp_coverage": adp_coverage,
        "price_movement": movement or price_movement.unavailable("movement was not computed"),
        "n": single.height,
    }


def export(results: dict[str, Any], profile: dict[str, Any]) -> MethodArtifact:
    return MethodArtifact(
        method_id=f"{METHOD_ID}__{profile['id']}",
        title=f"Tiers and value over replacement -- {profile.get('label')}",
        version=VERSION,
        claim_type="DESCRIPTIVE",
        spec_sections=["S19.3", "S19.4", "S14", "S38.1"],
        population={
            "registry_id": METHOD_ID,
            "profile_id": profile.get("id"),
            "teams": profile.get("teams"),
            "starters": profile.get("starters"),
            "flex_eligible": profile.get("flex_eligible"),
            "provider": results.get("provider"),
            "adp_snapshot_date": (results.get("adp_coverage") or {}).get("adp_snapshot_date"),
            "price_movement_since": (results.get("price_movement") or {}).get(
                "prior_snapshot_date"
            ),
        },
        outcome=None,  # construction, not a hypothesis test
        sample_size=results.get("n", 0),
        primary_results=results,
        limitations=[
            "DESCRIPTIVE (S88). A tier board is a construction from somebody else's "
            "projections, not a finding: it inherits every error in them and adds the "
            "replacement level as an assumption.",
            "Provider projections are unvalidated here. S66 asks that a projection source "
            "be backtested before it is trusted; that is the September build (S79 Step 0), "
            "and no accuracy check has run.",
            "Tier breaks come from adjacent value gaps at a threshold committed in code "
            f"({TIER_BREAK_MULTIPLE}x the median gap). A different rule gives different "
            "boundaries, and no evaluation of within- against across-tier outcome "
            "separation has been run (S19.3 asks for one).",
            "Replacement level assumes every manager starts a full lineup every week and "
            "that flex slots go to the best available flex-eligible player. Bye weeks and "
            "injuries (S85.1) are not modelled.",
            "Players with no ID match leave the board; they skew fringe, so replacement "
            "level sits slightly high and every value is slightly compressed (see coverage).",
            price_movement.ROLLING_WINDOW_LIMITATION,
            "ADP is carried beside the value, never inside it: the S19.3 metric is computed "
            "as if the price column were absent. It is the Fantasy Football Calculator "
            "mock-draft population rather than the league being drafted (S10B), and it is a "
            "rolling-window average rather than a same-day price (S31.3). Players the price "
            "join could not reach keep their row unpriced -- see adp_coverage.",
        ],
        sources=[
            "FantasyPros projections (S11)",
            "Fantasy Football Calculator ADP archive (S84)",
            "league profile (S14)",
        ],
    )


def run(processed_dir=PROCESSED_DIR) -> list[tuple[dict[str, Any], MethodArtifact]]:
    """One board per real league profile (S83 generates the sheet per profile too)."""
    problems = blockers(processed_dir)
    if problems:
        raise BlockedError(
            f"{METHOD_ID} is blocked, not killed (S88 Week 2):\n  - " + "\n  - ".join(problems)
        )
    out = []
    for profile in real_profiles():
        # Same rule as survival: a board must be one season's board.
        frame = board(profile, processed_dir=processed_dir, season=draft_season(profile))
        adp, movement = market_price(profile, processed_dir=processed_dir)
        results = compute(frame, profile, adp=adp, movement=movement)
        out.append((results, export(results, profile)))
    return out
