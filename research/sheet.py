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

**Per league profile.** Tiers, replacement level and survival all depend on
scoring, team count and draft slot. S14 excludes non-real profiles from the sheet
entirely, so with no profile marked real the only sheet that can honestly be
produced is the profile-independent one: the S25 regression flags, and a
statement of what is missing and why.

Self-contained HTML with inline CSS and no external assets -- S8 requires the
output work offline, and a draft-day tool that needs a network is not one.
"""

from __future__ import annotations

import datetime as dt
import html
import json
from pathlib import Path
from typing import Any

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

# Cut lists so the page stays one page at arm's length.
MAX_REGRESSION_TEAMS = 8
MAX_TIER_PLAYERS = 24
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


def tiers_section(artifacts: dict[str, Any], profile: dict[str, Any] | None) -> str:
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
            rows.append(
                '<tr class="{cls}"><td>{tier}</td><td>{player}</td><td>{team}</td>'
                "<td>{vor}</td></tr>".format(
                    cls="tier-start" if new_tier else "",
                    tier=p["tier"],
                    player=html.escape(str(p["player"])),
                    team=html.escape(str(p["team"] or "")),
                    vor=p["value_over_replacement"],
                )
            )
        blocks.append(
            f'<div class="pos"><h4>{pos} <span class="sub">replacement '
            f"{replacement} pts</span></h4>"
            '<table><thead><tr><th>T</th><th>Player</th><th>Tm</th><th>VOR</th></tr></thead>'
            f"<tbody>{''.join(rows)}</tbody></table></div>"
        )
    return f'<div class="cols">{"".join(blocks)}</div>'


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


def survival_section(artifacts: dict[str, Any], profile: dict[str, Any] | None) -> str:
    if profile is None:
        return _blocked(survival_mod.blockers() or ["no league profile selected"])
    art = artifacts.get(f"{survival_mod.METHOD_ID}__{profile['id']}")
    if art is None:
        return _blocked(survival_mod.blockers() or ["survival artifact has not been generated"])

    results = art["primary_results"]
    slots = results.get("by_slot") or []
    if len(slots) > 1:
        return (
            '<p class="missing"><strong>Draft slot undrawn.</strong> Survival is computed for '
            f'all {results["teams"]} slots in the artifact; the sheet fills in once '
            "<code>draft_slot</code> is set in config/league_profiles.yaml (S31.2).</p>"
        )
    blocks = []
    for pick_block in slots[0]["picks"][:MAX_SURVIVAL_PICKS]:
        rows = "".join(
            '<tr><td>{player}</td><td>{pos}</td><td>{adp}</td><td>{p}</td></tr>'.format(
                player=html.escape(str(c["player"])),
                pos=html.escape(str(c["position"] or "")),
                adp=c["adp"],
                p="--" if c["p_available"] is None else f'{c["p_available"]:.0%}',
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
    return (
        '<p class="sub">On the board at each pick you hold, and P(still there at your '
        "next one). Normal approximation from ADP mean and spread -- FFC publishes no "
        "pick distribution (S19.4).</p>"
        f'<div class="cols">{"".join(blocks)}</div>'
    )


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
    artifacts: dict[str, Any] | None = None,
) -> str:
    arts = artifacts if artifacts is not None else load_artifacts(edition)
    bodies = {
        "TIERS": tiers_section(arts, profile),
        "TARGETS": _not_built("Targets", "a research section (S27) requiring graded evidence"),
        "AVOIDS": _not_built("Avoids", "a research section (S28) requiring graded evidence"),
        "REGRESSION": regression_section(arts),
        "DARTS": _not_built("Dart throws", "a research section (S29)"),
        "SURVIVAL": survival_section(arts, profile),
        "FALSE FRIENDS": _not_built(
            "False friends", "the current-player matching engine (S32, S34)"
        ),
    }
    title = profile["label"] if profile else "no league profile encoded"
    header_note = (
        ""
        if profile
        else (
            '<p class="missing"><strong>This is not a league sheet.</strong> S14 excludes '
            "profiles that are not marked <code>real: true</code> from the draft-day sheet, "
            "because tiers, replacement level and survival are all conditional on scoring, "
            "team count and draft slot. Only the sections that do not depend on a league are "
            "filled in below. Set <code>draft_date</code> and <code>draft_slot</code> in "
            "config/league_profiles.yaml to generate the real thing.</p>"
        )
    )
    sections_html = "".join(
        f'<section><h3>{name} <span class="spec">{spec}</span></h3>{bodies[name]}</section>'
        for name, spec in SECTIONS
    )
    page = _PAGE.format(
        title=html.escape(title),
        edition=html.escape(edition),
        generated=dt.datetime.now(dt.UTC).date().isoformat(),
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


def write(edition: str | None = None, root: Path = ARTIFACT_DIR) -> list[Path]:
    """One sheet per real profile (S83), or the profile-independent one if none is."""
    edition = edition or default_edition()
    arts = load_artifacts(edition, root)
    directory = root / edition / "sheets"
    directory.mkdir(parents=True, exist_ok=True)

    profiles = real_profiles()
    written = []
    for profile in profiles or [None]:
        name = f"{profile['id']}.html" if profile else "no_profile.html"
        path = directory / name
        path.write_text(render(edition, profile=profile, artifacts=arts))
        written.append(path)
    return written


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
  .missing {{ color: #777; font-size: 7.5pt; font-style: italic; }}
  ul.missing {{ margin: 2px 0 4px; padding-left: 14px; }}
  code {{ font-size: 7.5pt; }}
  footer {{ margin-top: 8px; color: #888; font-size: 7pt;
            border-top: 1px solid #ddd; padding-top: 3px; }}
</style>
<h1>Draft sheet <span class="sub">-- {title}</span></h1>
<p class="sub">Edition {edition} &middot; generated {generated} &middot; generated from the
S16 method artifacts, never edited by hand (S83).</p>
{header_note}
{sections}
<footer>
  Descriptive only (S2.2, S88). Nothing here is a prescriptive recommendation.
  ADP data courtesy of Fantasy Football Calculator. Statistics from nflverse (CC-BY-4.0).
</footer>
"""
