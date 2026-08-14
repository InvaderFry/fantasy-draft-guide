"""S83 -- the draft-day sheet.

The guide is read in July. The sheet is used during a two-hour event with a
90-second pick clock, where nobody opens a methodology chapter. r1 produced no
such output, which meant the whole system terminated in a browsable site rather
than in a decision aid. S78 makes the sheet an acceptance criterion and S88 makes
it the deliverable that survives if the schedule collapses.

Three rules from S83 are enforced here rather than documented:

**Generated from the artifacts, never by hand.** This module reads
``artifacts/<edition>/methods/*.json`` and formats them. It computes nothing. A
section whose artifact is missing says so; it does not fall back to a stale
number, and there is nowhere to type one in.

**Conclusions and prices only.** No evidence grades, no confidence intervals, no
sample sizes -- those belong in the guide. ``assert_sheet_constraints`` scans the
rendered page for them and raises, so the rule survives a future contributor who
adds a section without reading this docstring.

**Per league profile, and per draft slot.** Tiers, replacement level and survival
all depend on scoring, team count and draft slot. S14 excludes non-real profiles
from the sheet entirely, so with no profile marked real the only sheet that can
honestly be produced is the profile-independent one: the S25 regression flags,
and a statement of what is missing and why.

The slot is the awkward one: this drafter's is drawn about an hour before the
draft starts. Waiting for it would mean generating the sheet in that hour, on
whatever machine and network is to hand, which is precisely the situation S8 says
the output must not depend on. So every slot is rendered ahead of time -- S31.2
already computes all of them into one artifact -- and the draw becomes a matter of
opening the right file. `index.html` is the chooser, and it is a list of links
because a list of links needs no runtime.

Self-contained HTML with inline CSS and no external assets -- S8 requires the
output work offline, and a draft-day tool that needs a network is not one.
"""

from __future__ import annotations

import datetime as dt
import html
import json
from pathlib import Path
from typing import Any

from pipeline.config import draft_slot as profile_draft_slot
from pipeline.config import real_profiles
from research.foundations import survival as survival_mod
from research.foundations import tiers as tiers_mod
from research.method import ARTIFACT_DIR, default_edition

# Substrings S83 forbids on the sheet. Checked case-insensitively against the
# rendered page: the constraint is only real if something enforces it.
FORBIDDEN = (
    "confidence interval",
    "ci_low",
    "ci_high",
    "evidence grade",
    "sample size",
    "p-value",
    "p =",
)

# S83: teams whose touchdown total sits this far from the league mean are the
# ones worth flagging. S25 measured what happens next at exactly these cuts.
REGRESSION_Z = 1.0

# How far a position's hit rate has to fall below the position priced alongside it
# before the gap is worth printing as a band, in percentage points.
#
# Committed here with its reasoning rather than read off the answer: S80 prohibits
# choosing a threshold after seeing what it produces. Ten points is one drafted
# player in ten, which at these prices is about one pick a draft -- the smallest
# gap that changes what somebody does at the table. The band is then the run of
# adjacent buckets around the widest gap that all clear it, so a single extreme
# bucket does not become a "zone" on its own.
DEAD_ZONE_MIN_GAP_PP = 10.0

# Cut lists so the page stays one page at arm's length.
#
# MAX_TIER_PLAYERS is the one that binds, and it is measured rather than guessed.
# It has been re-measured twice, and both times the measurement is the page count
# of the rendered PDF rather than a pixel height -- pixel heights depend on which
# instrument took them and do not survive being compared across two of them.
#
#   24 players a position printed on two pages, and 16 fitted, once the S14 gate
#   opened and TIERS and SURVIVAL started carrying real content.
#   16 then printed on two pages again the moment the ADP column arrived: five
#   columns in a 171px cell wrap the longer names, and a wrapped name costs
#   height. Across all 26 sheets, 13 put two of them onto a second page and 12
#   put none -- and it has to be all 26, because the survival block is a
#   different height at different seats and a sweep of one slot says 13 is fine.
#
# So 12. Four rows a position is what the price costs, and it is worth paying:
# a ranked player with no price beside him cannot be acted on at a live pick,
# and the players given up are the 13th to 16th at a position, who by then are
# being read off the survival block anyway.
#
# The column that is really binding here is `Tm`. Dropping it clears all 26
# sheets at 14, so the team code is worth about two players a position -- kept,
# because it is what makes the S25 regression flags usable: you read FADE LA at
# the top and scan the board for LA.
#
# Re-measure before raising it (see README, "Checking it is still one page").
#
# MAX_SURVIVAL_PICKS costs no height -- the pick blocks are columns in one flex
# row, so more of them makes each narrower rather than the page longer.
MAX_REGRESSION_TEAMS = 8
MAX_TIER_PLAYERS = 12
MAX_SURVIVAL_PICKS = 8


class SheetConstraintError(AssertionError):
    """The rendered sheet carries something S83 keeps off it."""


def load_artifacts(edition: str, root: Path = ARTIFACT_DIR) -> dict[str, dict[str, Any]]:
    directory = root / edition / "methods"
    if not directory.exists():
        return {}
    out = {}
    for path in sorted(directory.glob("*.json")):
        # NaN is legal in this repo's artifacts (an interval on n=1) and illegal
        # in strict JSON; the writer emits it, so the reader accepts it.
        out[path.stem] = json.loads(path.read_text())
    return out


# -- sections ---------------------------------------------------------------


def _not_built(section: str, spec: str) -> str:
    """A section with no research behind it.

    Rendered explicitly rather than omitted. A blank space on a draft sheet is a
    space somebody fills in from memory at pick 43, which is the failure mode the
    sheet exists to prevent.
    """
    return (
        f'<p class="missing"><strong>NOT BUILT.</strong> {section} is {spec}, which is not '
        "in the S88 compressed build. Scheduled for the September-February build (S79).</p>"
    )


def _blocked(reasons: list[str]) -> str:
    items = "".join(f"<li>{html.escape(r)}</li>" for r in reasons)
    return f'<p class="missing"><strong>BLOCKED.</strong></p><ul class="missing">{items}</ul>'


def tiers_section(
    artifacts: dict[str, Any],
    profile: dict[str, Any] | None,
    band: dict[str, Any] | None = None,
) -> str:
    if profile is None:
        return _blocked(tiers_mod.blockers() or ["no league profile selected"])
    art = artifacts.get(f"{tiers_mod.METHOD_ID}__{profile['id']}")
    if art is None:
        return _blocked(tiers_mod.blockers() or ["tier artifact has not been generated"])

    results = art["primary_results"]
    blocks = []
    for pos, block in results.get("positions", {}).items():
        players = block.get("players") or []
        if not players:
            continue
        replacement = (block.get("replacement") or {}).get("points")
        rows = []
        last_tier = None
        for p in players[:MAX_TIER_PLAYERS]:
            new_tier = p["tier"] != last_tier
            last_tier = p["tier"]
            adp = p.get("adp")
            rows.append(
                '<tr class="{cls}"><td>{tier}</td><td>{player}</td><td>{team}</td>'
                '<td class="num">{vor}</td><td class="num{dz}">{adp}</td></tr>'.format(
                    cls="tier-start" if new_tier else "",
                    tier=p["tier"],
                    player=html.escape(str(p["player"])),
                    team=html.escape(str(p["team"] or "")),
                    vor=f'{p["value_over_replacement"]:.0f}',
                    # S83: "with ADP alongside". A value with no price cannot
                    # answer the question asked at a live pick.
                    adp="&mdash;" if adp is None else f"{adp:.0f}",
                    dz=" dz" if in_dead_zone(band, pos, adp) else "",
                )
            )
        blocks.append(
            f'<div class="pos"><h4>{pos} <span class="sub">replacement '
            f"{replacement} pts</span></h4>"
            "<table><thead><tr><th>T</th><th>Player</th><th>Tm</th>"
            '<th class="num">VOR</th><th class="num">ADP</th></tr></thead>'
            f"<tbody>{''.join(rows)}</tbody></table></div>"
        )
    return f'<div class="cols">{"".join(blocks)}</div>'


def dead_zone_band(artifacts: dict[str, Any]) -> dict[str, Any] | None:
    """S21.1's price band, read off the artifact rather than written down here.

    The finding is a run of ADP buckets where backs returned a top-12 season far
    less often than the receivers going at the same price. Which buckets those are
    is a property of the data and moves when the data does, so it is derived: the
    widest gap, extended outward through every adjacent bucket that also clears
    ``DEAD_ZONE_MIN_GAP_PP``. Hard-coding today's answer would leave a stale band
    printed over a refreshed artifact, and nothing would say so.

    Returns None when no artifact is present or nothing clears the threshold --
    "no band this year" is a real answer and must not render as one.
    """
    art = artifacts.get("rb_dead_zone_bucket_rates")
    if art is None:
        return None
    rows = (art.get("primary_results") or {}).get("rb_vs_wr") or []
    ordered = [r for r in sorted(rows, key=lambda r: r["bucket"])]
    scored = [r for r in ordered if r.get("absolute_difference_pp") is not None]
    if not scored:
        return None
    widest = min(scored, key=lambda r: r["absolute_difference_pp"])
    if widest["absolute_difference_pp"] > -DEAD_ZONE_MIN_GAP_PP:
        return None

    def clears(row: dict[str, Any]) -> bool:
        gap = row.get("absolute_difference_pp")
        return gap is not None and gap <= -DEAD_ZONE_MIN_GAP_PP

    start = end = ordered.index(widest)
    while start > 0 and clears(ordered[start - 1]):
        start -= 1
    while end + 1 < len(ordered) and clears(ordered[end + 1]):
        end += 1

    band = ordered[start : end + 1]
    low, high = _band_edges(band)
    return {"buckets": band, "widest": widest, "low": low, "high": high}


def _band_edges(band: list[dict[str, Any]]) -> tuple[int | None, int | None]:
    """First and last pick of the band, from the buckets' own labels ("25-36")."""
    try:
        return int(band[0]["bucket_label"].split("-")[0]), int(
            band[-1]["bucket_label"].split("-")[-1]
        )
    except (KeyError, IndexError, ValueError):
        return None, None


def in_dead_zone(band: dict[str, Any] | None, position: str, adp: float | None) -> bool:
    """Whether this player's price sits inside the band.

    S21.1 measured running backs, and the claim is about running backs. Applying
    the band to a position it was never computed on would be a new claim wearing
    an old one's evidence.
    """
    if band is None or adp is None or position != "RB":
        return False
    return band["low"] is not None and band["low"] <= adp <= band["high"]


def dead_zone_section(artifacts: dict[str, Any], band: dict[str, Any] | None) -> str:
    """S21.1 as an S83 avoid: a price band, and what was bought at that price.

    S83 requires a price trigger on every recommendation, and here the price range
    *is* the recommendation -- there is no player named, because the finding is
    about what a position costs rather than about anybody in particular.

    S83 also keeps sample sizes and intervals off the sheet, and this artifact
    carries both. Only four keys are read: the label, the two rates and the gap.
    """
    unbuilt = _not_built("Avoids", "a research section (S28) requiring graded evidence")
    if band is None:
        # Two different nothings, and they must not read alike: no artifact means
        # the analysis has not run, while an artifact with no qualifying band means
        # it ran and found none. The second is a result.
        if "rb_dead_zone_bucket_rates" not in artifacts:
            return unbuilt
        return unbuilt + (
            '<p class="missing">S21.1 ran and flags no price band wide enough to '
            "print this year.</p>"
        )

    rows = "".join(
        '<tr><td>{label}</td><td class="num">{rb:.0%}</td><td class="num">{wr:.0%}</td>'
        '<td class="num {cls}">{gap:+.0f} pp</td></tr>'.format(
            label=html.escape(str(b["bucket_label"])),
            rb=b["rb_high_end_rate"],
            wr=b["wr_high_end_rate"],
            gap=b["absolute_difference_pp"],
            cls="fade" if b is band["widest"] else "",
        )
        for b in band["buckets"]
    )
    return (
        '<div class="cols"><div class="pos" style="flex:2">'
        f'<p class="sub"><span class="tag fade">DEAD ZONE</span> '
        f'<strong>RB, picks {band["low"]}&ndash;{band["high"]}</strong> '
        "&mdash; how often a back drafted at each price returned a top-12 season, "
        "against the receivers going alongside him (2018&ndash;2025). Prices inside the "
        'band are <span class="dz">red</span> on the boards above and below.</p>'
        '<p class="sub">A positional price band, not a list of players: S28 avoids need '
        "graded evidence and are the September-February build (S79).</p>"
        '</div><div class="pos">'
        "<table><thead><tr><th>Picks</th><th>RB</th><th>WR</th><th>Gap</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        "</div></div>"
    )


def regression_section(artifacts: dict[str, Any]) -> str:
    """S25's flagged teams. The one section that needs no league profile.

    Regression to the mean is a property of the team, not of the scoring system,
    so it is the same on every sheet -- and it is the only section that survives
    when no profile is real.
    """
    art = artifacts.get("team_scoring_regression")
    if art is None:
        return _blocked(["team_scoring_regression artifact has not been generated (S25)"])

    results = art["primary_results"]
    extremes = results.get("current_extremes") or {}
    season = extremes.get("season")
    flagged = [
        e
        for e in extremes.get(f"abs_z_at_least_{REGRESSION_Z}", [])
        or extremes.get("abs_z_at_least_1.5", [])
        if e.get("metric") == "offensive_tds"
    ]
    if not flagged:
        return '<p class="missing">No team is flagged on offensive touchdowns this season.</p>'

    # The expected move, by z bucket, straight off S25's own table. No interval,
    # no n: S83 keeps both off the sheet.
    by_metric = results.get("regression_to_mean", [])
    buckets = next(
        (m["buckets"] for m in by_metric if m["metric"] == "offensive_tds"), []
    )

    rows = []
    for e in sorted(flagged, key=lambda x: -abs(x["z"]))[:MAX_REGRESSION_TEAMS]:
        move = _expected_move(buckets, e["z"])
        direction = "FADE" if e["z"] > 0 else "BUY"
        rows.append(
            f'<tr><td class="tag {direction.lower()}">{direction}</td>'
            f'<td>{html.escape(str(e["team"]))}</td>'
            f'<td>{e["value"]:.0f} TD</td><td>{e["z"]:+.1f}z</td>'
            f"<td>{move}</td></tr>"
        )
    return (
        f'<p class="sub">{season} offensive touchdowns against the league. '
        "Expected move is what teams at this distance have done the following year.</p>"
        "<table><thead><tr><th></th><th>Team</th><th>Last yr</th><th>z</th>"
        f"<th>Expected move</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def _expected_move(buckets: list[dict[str, Any]], z: float) -> str:
    for b in buckets:
        if b["z_from"] <= z < b["z_to"]:
            return f'{b["mean_next_change"]:+.0f} TD'
    return "--"


def survival_section(
    artifacts: dict[str, Any],
    profile: dict[str, Any] | None,
    slot: int | None = None,
    band: dict[str, Any] | None = None,
) -> str:
    if profile is None:
        return _blocked(survival_mod.blockers() or ["no league profile selected"])
    art = artifacts.get(f"{survival_mod.METHOD_ID}__{profile['id']}")
    if art is None:
        return _blocked(survival_mod.blockers() or ["survival artifact has not been generated"])

    results = art["primary_results"]
    slots = results.get("by_slot") or []
    if not slots:
        return _blocked(["the survival artifact carries no slots (S31.2)"])

    chosen = _slot_entry(slots, slot)
    if chosen is None:
        if slot is not None:
            return _blocked(
                [
                    f"slot {slot} is not in the survival artifact, which covers "
                    f"{', '.join(str(s['slot']) for s in slots)}. Re-run `make research` "
                    "if the league's team count changed (S31.2)."
                ]
            )
        return _undrawn_orientation(results, profile)

    blocks = []
    for pick_block in chosen["picks"][:MAX_SURVIVAL_PICKS]:
        rows = "".join(
            '<tr><td>{player}</td><td>{pos}</td><td class="{dz}">{adp}</td>'
            "<td>{p}</td></tr>".format(
                player=html.escape(str(c["player"])),
                pos=html.escape(str(c["position"] or "")),
                adp=c["adp"],
                p="--" if c["p_available"] is None else f'{c["p_available"]:.0%}',
                # This is where S21.1 actually bites. The band sits at picks
                # 25-60, which on a board sorted by value is the 12th to 22nd back
                # -- past the end of the tier list, but squarely in the candidates
                # at the middle picks, where the choice is being made.
                dz="dz" if in_dead_zone(band, c["position"], c["adp"]) else "",
            )
            for c in pick_block["candidates"][:6]
        )
        target = pick_block.get("survival_measured_at", pick_block["pick"])
        heading = (
            f'Pick {pick_block["pick"]} <span class="sub">&rarr; {target}</span>'
            if not pick_block.get("is_last_pick")
            else f'Pick {pick_block["pick"]} <span class="sub">last</span>'
        )
        blocks.append(
            f'<div class="pos"><h4>{heading}</h4>'
            "<table><thead><tr><th>Player</th><th>Pos</th><th>ADP</th><th>Back?</th>"
            f"</tr></thead><tbody>{rows}</tbody></table></div>"
        )
    held = ", ".join(str(p) for p in chosen["held_picks"][:MAX_SURVIVAL_PICKS])
    return (
        f'<p class="sub">Slot {chosen["slot"]} holds {held}. On the board at each, and '
        "P(still there at your next one). Normal approximation from ADP mean and spread "
        "-- FFC publishes no pick distribution (S19.4).</p>"
        f'<div class="cols">{"".join(blocks)}</div>'
    )


def _slot_entry(slots: list[dict[str, Any]], slot: int | None) -> dict[str, Any] | None:
    """The one slot this page is for, or None if the page is not for a slot yet."""
    if slot is not None:
        return next((s for s in slots if int(s["slot"]) == int(slot)), None)
    return slots[0] if len(slots) == 1 else None


# Enough rounds to see the shape of a slot -- where the turn falls, how long the
# wait is -- without turning the orientation block into a second sheet.
ORIENTATION_ROUNDS = 5


def _undrawn_orientation(results: dict[str, Any], profile: dict[str, Any]) -> str:
    """What to print when the order has not been drawn yet.

    Not BLOCKED. The slot being undrawn an hour before the draft is the expected
    state for this drafter, and a sheet that goes blank in the expected state is
    the failure S83 exists to prevent. Every slot has its own fully rendered page
    already; this says which one to open, and shows the shape of each seat so the
    wait between picks is not a surprise when the draw comes.
    """
    rows = []
    for entry in results.get("by_slot") or []:
        picks = ", ".join(str(p) for p in entry["held_picks"][:ORIENTATION_ROUNDS])
        rows.append(
            f'<tr><td class="tag">{entry["slot"]}</td><td>{picks}</td></tr>'
        )
    # Two columns. Twelve stacked rows push this page past one printed sheet,
    # which is the one thing S83 does not let a section do.
    half = -(-len(rows) // 2)
    table = (
        '<table><thead><tr><th>Slot</th><th>Picks</th></tr></thead>'
        "<tbody>{}</tbody></table>"
    )
    columns = "".join(
        f'<div class="pos">{table.format("".join(chunk))}</div>'
        for chunk in (rows[:half], rows[half:])
        if chunk
    )
    filename = f"{profile['id']}__slot&lt;NN&gt;.html"
    return (
        '<p class="missing"><strong>Draft order undrawn.</strong> No slot is guessed. '
        f"A complete sheet for every one of the {results['teams']} slots is already "
        f"rendered next to this one -- open <code>{filename}</code> for the seat you "
        "draw, or <code>index.html</code> and pick it from the list. Nothing needs to "
        "be rebuilt and nothing needs a network (S8, S31.2).</p>"
        '<p class="sub">First '
        f"{ORIENTATION_ROUNDS} picks each seat holds:</p>"
        f'<div class="cols">{columns}</div>'
    )


def adp_capture_date(artifacts: dict[str, Any], profile: dict[str, Any] | None) -> str | None:
    """The date of the ADP capture this sheet is priced from (S84).

    The one date on the page that can go stale. `generated` cannot: it is written
    by the run that writes the page, so it stays reassuringly current even when
    the pipeline has broken upstream of it and the board underneath is weeks old.
    A drafter reading a sheet at the table has no other way to tell.

    Read off the survival artifact, which already records it
    (research/foundations/survival.py). None when there is no survival artifact
    to read it from, which the header renders as unknown rather than as today.
    """
    if profile is None:
        return None
    art = artifacts.get(f"{survival_mod.METHOD_ID}__{profile['id']}")
    if art is None:
        return None
    captured = (art.get("primary_results") or {}).get("adp_snapshot_date")
    return str(captured) if captured else None


SECTIONS = (
    ("TIERS", "S19.3"),
    ("TARGETS", "S27"),
    ("AVOIDS", "S28"),
    ("REGRESSION", "S25"),
    ("DARTS", "S29"),
    ("SURVIVAL", "S31.2"),
    ("FALSE FRIENDS", "S34"),
)


def render(
    edition: str,
    *,
    profile: dict[str, Any] | None = None,
    slot: int | None = None,
    artifacts: dict[str, Any] | None = None,
) -> str:
    arts = artifacts if artifacts is not None else load_artifacts(edition)
    # Derived once and shared: the section states the band, and the board marks
    # the players standing in it. A finding printed eight lines away from the
    # prices it applies to is a finding somebody has to remember.
    band = dead_zone_band(arts)
    bodies = {
        "TIERS": tiers_section(arts, profile, band),
        "TARGETS": _not_built("Targets", "a research section (S27) requiring graded evidence"),
        "AVOIDS": dead_zone_section(arts, band),
        "REGRESSION": regression_section(arts),
        "DARTS": _not_built("Dart throws", "a research section (S29)"),
        "SURVIVAL": survival_section(arts, profile, slot, band),
        "FALSE FRIENDS": _not_built(
            "False friends", "the current-player matching engine (S32, S34)"
        ),
    }
    title = profile["label"] if profile else "no league profile encoded"
    if profile and slot is not None:
        title = f"{title} \u00b7 slot {slot}"
    generated = dt.datetime.now(dt.UTC).date().isoformat()
    captured = adp_capture_date(arts, profile)
    priced = (
        "ADP not priced"
        if captured is None
        else f"priced from the ADP capture of <strong>{html.escape(captured)}</strong>"
        + ("" if captured == generated else " &mdash; not today&rsquo;s")
    )
    header_note = (
        ""
        if profile
        else (
            '<p class="missing"><strong>This is not a league sheet.</strong> S14 excludes '
            "profiles that are not marked <code>real: true</code> from the draft-day sheet, "
            "because tiers, replacement level and survival are all conditional on scoring, "
            "team count and draft slot. Only the sections that do not depend on a league are "
            "filled in below. Encode the leagues in config/league_profiles.yaml and set "
            "<code>real: true</code> to generate the real thing.</p>"
        )
    )
    sections_html = "".join(
        f'<section><h3>{name} <span class="spec">{spec}</span></h3>{bodies[name]}</section>'
        for name, spec in SECTIONS
    )
    page = _PAGE.format(
        title=html.escape(title),
        edition=html.escape(edition),
        generated=generated,
        priced=priced,
        header_note=header_note,
        sections=sections_html,
    )
    assert_sheet_constraints(page)
    return page


def assert_sheet_constraints(page: str) -> None:
    """S83: conclusions and prices only.

    "No evidence grades, no confidence intervals, no sample sizes. Those belong
    in the guide; the sheet carries conclusions and prices only." A rule nothing
    checks is a rule the next section quietly breaks.
    """
    lowered = page.lower()
    found = [token for token in FORBIDDEN if token in lowered]
    if found:
        raise SheetConstraintError(
            f"the rendered sheet carries {found}, which S83 keeps off it: the sheet is "
            "conclusions and prices, and the evidence lives in the guide."
        )


def slot_filename(profile_id: str, slot: int) -> str:
    """Zero-padded so the files sort the way the slots are numbered."""
    return f"{profile_id}__slot{slot:02d}.html"


def slots_to_render(profile: dict[str, Any], slot: int | None = None) -> list[int]:
    """Which seats this profile needs a sheet for.

    A configured slot wins: the order is drawn and there is one answer. An
    explicit `slot` argument is the draft-hour override, for regenerating a
    single seat against a fresher ADP capture. Otherwise every seat, because the
    draw has not happened and the whole point is that it costs nothing when it
    does.
    """
    configured = profile_draft_slot(profile)
    if configured is not None:
        return [configured]
    if slot is not None:
        return [slot]
    return list(range(1, int(profile["teams"]) + 1))


def write(
    edition: str | None = None,
    root: Path = ARTIFACT_DIR,
    *,
    slot: int | None = None,
    profile_id: str | None = None,
) -> list[Path]:
    """Every sheet a real profile needs (S83), plus the chooser.

    A league whose order is drawn gets one sheet, named for the league. A league
    whose order is not gets one per seat plus a slot-agnostic page, so draft hour
    is a file open rather than a build. `slot` overrides a single seat -- the
    draft-hour regeneration path -- and writes only that seat's page, leaving the
    pre-rendered set alone.
    """
    edition = edition or default_edition()
    arts = load_artifacts(edition, root)
    directory = root / edition / "sheets"
    directory.mkdir(parents=True, exist_ok=True)

    profiles = real_profiles()
    if profile_id:
        profiles = [p for p in profiles if p["id"] == profile_id]
        if not profiles:
            raise ValueError(
                f"no real league profile with id {profile_id!r} in "
                "config/league_profiles.yaml (S14)"
            )

    written: list[Path] = []
    seats_by_profile: dict[str, list[int]] = {}
    for profile in profiles or [None]:
        if profile is None:
            path = directory / "no_profile.html"
            path.write_text(render(edition, profile=None, artifacts=arts))
            written.append(path)
            continue

        configured = profile_draft_slot(profile)
        if configured is not None:
            # One league, one seat. The league's sheet IS the seat's sheet, so it
            # keeps the league's name and no chooser is needed to find it.
            path = directory / f"{profile['id']}.html"
            path.write_text(render(edition, profile=profile, slot=configured, artifacts=arts))
            written.append(path)
            seats_by_profile[profile["id"]] = []
            continue

        seats = slots_to_render(profile, slot)
        for seat in seats:
            path = directory / slot_filename(profile["id"], seat)
            path.write_text(render(edition, profile=profile, slot=seat, artifacts=arts))
            written.append(path)

        if slot is None:
            # The page to read the week before, when the seat is still unknown:
            # tiers and regression do not depend on it.
            path = directory / f"{profile['id']}.html"
            path.write_text(render(edition, profile=profile, artifacts=arts))
            written.append(path)
            seats_by_profile[profile["id"]] = seats

    if profiles and any(p is not None for p in profiles):
        written.append(write_index(directory, edition, profiles, seats_by_profile))
    return written


def write_index(
    directory: Path,
    edition: str,
    profiles: list[dict[str, Any]],
    seats_by_profile: dict[str, list[int]],
) -> Path:
    """The draft-hour chooser: pick the seat you drew, open that sheet.

    A list of links, inline CSS, relative hrefs, no script. It has to work from a
    file:// URL on a phone with no signal (S8), and every mechanism fancier than
    an anchor tag is a mechanism that can fail in the one hour it is needed.
    """
    blocks = []
    for profile in profiles:
        seats = seats_by_profile.get(profile["id"])
        label = html.escape(str(profile["label"]))
        if seats:
            links = "".join(
                f'<a class="slot" href="{slot_filename(profile["id"], s)}">{s}</a>'
                for s in seats
            )
            body = (
                '<p class="sub">Draft slot -- tap the seat you drew.</p>'
                f'<div class="slots">{links}</div>'
                f'<p class="sub"><a href="{profile["id"]}.html">Slot-agnostic sheet</a> '
                "-- tiers and regression, which do not depend on the seat.</p>"
            )
        else:
            configured = profile_draft_slot(profile)
            seat_note = f" -- slot {configured}" if configured is not None else ""
            body = (
                f'<div class="slots"><a class="slot wide" href="{profile["id"]}.html">'
                f"Open sheet{seat_note}</a></div>"
            )
        blocks.append(f"<section><h3>{label}</h3>{body}</section>")

    generated = dt.datetime.now(dt.UTC).date().isoformat()
    arts = load_artifacts(edition, directory.parent.parent)
    captured = next(
        (d for d in (adp_capture_date(arts, p) for p in profiles) if d), None
    )
    priced = (
        "ADP not priced"
        if captured is None
        else f"priced from the ADP capture of <strong>{html.escape(captured)}</strong>"
    )
    stale = (
        ""
        if captured in (None, generated)
        else (
            '<p class="stale">These sheets are priced from an older capture than '
            "today. The daily refresh has not run, so the board below is not the "
            "board the market is on.</p>"
        )
    )
    page = _INDEX.format(
        edition=html.escape(edition),
        generated=generated,
        priced=priced,
        stale=stale,
        blocks="".join(blocks),
    )
    assert_sheet_constraints(page)
    path = directory / "index.html"
    path.write_text(page)
    return path


_PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Draft sheet -- {title}</title>
<style>
  /* One page, printable, legible at arm's length (S83). Inline and offline (S8). */
  @page {{ size: letter portrait; margin: 0.35in; }}
  * {{ box-sizing: border-box; }}
  body {{ font: 9pt/1.25 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
         color: #111; background: #fff; margin: 0; padding: 10px 14px; }}
  h1 {{ font-size: 14pt; margin: 0; }}
  h1 .sub {{ font-weight: 400; color: #555; font-size: 9pt; }}
  h3 {{ font-size: 9.5pt; letter-spacing: .09em; margin: 9px 0 3px;
        border-bottom: 1.5px solid #111; padding-bottom: 1px; }}
  h3 .spec {{ float: right; font-weight: 400; color: #888; letter-spacing: 0; }}
  h4 {{ font-size: 8.5pt; margin: 0 0 2px; }}
  h4 .sub, p.sub {{ font-weight: 400; color: #666; font-size: 7.5pt; }}
  p {{ margin: 2px 0 4px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 8pt; }}
  th {{ text-align: left; color: #666; font-weight: 600; border-bottom: 1px solid #bbb; }}
  td, th {{ padding: 0.5px 3px 0.5px 0; }}
  tr.tier-start td {{ border-top: 1px solid #ddd; }}
  .cols {{ display: flex; gap: 12px; align-items: flex-start; }}
  .pos {{ flex: 1; min-width: 0; }}
  .tag {{ font-weight: 700; font-size: 7pt; }}
  .fade {{ color: #a11; }} .buy {{ color: #161; }}
  td.num {{ text-align: right; }}
  /* A price inside S21.1's band. Colour rather than a marker: ADP is the
     narrowest column on the page and a two-character tag wraps the name beside
     it, and wrapping costs height, which is the budget that binds. */
  .dz {{ color: #a11; font-weight: 700; }}
  .missing {{ color: #777; font-size: 7.5pt; font-style: italic; }}
  ul.missing {{ margin: 2px 0 4px; padding-left: 14px; }}
  code {{ font-size: 7.5pt; }}
  footer {{ margin-top: 8px; color: #888; font-size: 7pt;
            border-top: 1px solid #ddd; padding-top: 3px; }}
</style>
<h1>Draft sheet <span class="sub">-- {title}</span></h1>
<p class="sub">Edition {edition} &middot; generated {generated} &middot; {priced}.
Generated from the S16 method artifacts, never edited by hand (S83).</p>
{header_note}
{sections}
<footer>
  Descriptive only (S2.2, S88). Nothing here is a prescriptive recommendation.
  ADP data courtesy of Fantasy Football Calculator. Statistics from nflverse (CC-BY-4.0).
</footer>
"""


_INDEX = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Draft sheets -- {edition}</title>
<style>
  /* Opened on a phone an hour before the draft, possibly with no signal (S8).
     Inline, no script, targets big enough to hit without aiming. */
  body {{ font: 15px/1.4 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
         color: #111; background: #fff; margin: 0; padding: 18px; max-width: 40rem; }}
  h1 {{ font-size: 19px; margin: 0 0 2px; }}
  h3 {{ font-size: 15px; margin: 20px 0 4px; border-bottom: 1.5px solid #111;
        padding-bottom: 2px; }}
  p {{ margin: 2px 0 8px; }}
  p.sub {{ color: #555; font-size: 13px; }}
  .slots {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  a.slot {{ display: block; min-width: 3rem; padding: 14px 0; text-align: center;
            box-sizing: border-box;
            border: 1.5px solid #111; border-radius: 6px; text-decoration: none;
            color: #111; font-weight: 700; font-size: 17px; }}
  a.slot.wide {{ width: 100%; }}
  p.stale {{ border: 1.5px solid #a11; color: #a11; border-radius: 6px;
             padding: 8px 10px; font-weight: 600; }}
  footer {{ margin-top: 22px; color: #888; font-size: 12px;
            border-top: 1px solid #ddd; padding-top: 6px; }}
</style>
<h1>Draft sheets</h1>
<p class="sub">Edition {edition} &middot; generated {generated} &middot; {priced}</p>
{stale}
<p>The draft order is drawn about an hour before the draft. Every seat already has
its own complete sheet, so there is nothing to run when it is: open yours.</p>
{blocks}
<footer>
  Descriptive only (S2.2, S88). Nothing here is a prescriptive recommendation.
  ADP data courtesy of Fantasy Football Calculator. Statistics from nflverse (CC-BY-4.0).
</footer>
"""
