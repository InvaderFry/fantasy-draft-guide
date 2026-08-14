"""League draft logs -- the drafter's own picks, pasted in (S76, S10B).

S76 stores what was recommended against what happened, and S77 is the review
that consumes it. Both are worthless without the second half: the picks.

**Why pasted rather than fetched.** S10B option D looks at the Sleeper API and
concludes it is not a turnkey research dataset, and every platform in the spec --
Sleeper, ESPN -- answers 403 at CONNECT from the development sandbox, exactly as
FantasyPros and Fantasy Football Calculator do. An importer written against a
shape nobody can observe is the mistake this repository already made once and
resolved by archiving the payload and correcting the mapping in YAML. Draft
results are visible on every platform's own page, so a parser that accepts a
paste needs no credential, no API and no allowlist, and works on a platform this
project has never heard of.

**Why every pick and not just the drafter's.** S31.1 is resolved and the answer
is that it cannot be answered from the market: Fantasy Football Calculator
publishes a mean, a spread, a high and a low, and no percentiles, so S31.2's
survival number is a labelled normal approximation. S10B names the one corpus
that is exempt:

    the drafter's own historical league draft logs are a small but fully
    permitted corpus that requires no external access at all -- start
    exporting them (S84)

A full board is a real pick distribution. One draft settles nothing; the corpus
cannot begin until something writes the first one, and every draft that happens
before then is unrecoverable.

**Why it refuses.** The input is a paste from a page, not an export, so it is
malformed in ways an export never is -- a wrapped line, a header row, a bye-week
column. Dropping eight lines of a 180-pick draft yields a board that looks
complete, parses cleanly, and is wrong about who was available in a way nobody
will ever notice. So the parser counts what it produced against `teams x rounds`
and raises rather than returning a short frame.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from pipeline.normalize.names import SUFFIXES

SOURCE_NAME = "league_draft"

# Tried in order against each non-blank line. Every pattern must yield `player`;
# `pick`, `position` and `team` are taken when the shape carries them and
# derived from position in the file when it does not.
#
# Deliberately not one clever regex. Each entry is a shape somebody's draft-
# results page actually produces, and when a paste fails, the error names the
# line so a ninth shape is a two-line addition rather than an investigation.
# An optional trailing "RB ATL" / ", RB, ATL", shared by every shape below so a
# position and a team are recognised however the page delimits them. Without it
# the name group swallows them and the crosswalk is asked to match
# "Bijan Robinson RB ATL" against a player called Bijan Robinson.
_SUFFIX = (
    r"(?:[\s,;\t]+(?P<position>QB|RB|WR|TE|K|DEF|DST|PK|D/ST|FB))?"
    r"(?:[\s,;\t]+(?P<team>[A-Za-z]{2,3}))?\s*$"
)

# Tried in order against each non-blank line. Every pattern must yield `player`;
# `pick`, `position` and `team` are taken when the shape carries them and
# derived from position in the file when it does not.
#
# Deliberately not one clever regex. Each entry is a shape somebody's draft-
# results page actually produces, and when a paste fails, the error names the
# line so a ninth shape is a two-line addition rather than an investigation.
LINE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # 1.01 Bijan Robinson RB ATL      (round.pick, the most common export)
    (
        "round.pick",
        re.compile(
            r"^(?P<round>\d{1,2})\.(?P<in_round>\d{1,2})\s+(?P<player>.+?)" + _SUFFIX
        ),
    ),
    # Pick 1 - Bijan Robinson, RB, ATL     (en/em dashes and commas both seen)
    (
        "pick n",
        re.compile(
            r"^(?:Pick\s+)?(?P<pick>\d{1,3})\s*[-–—.)]\s*(?P<player>.+?)" + _SUFFIX
        ),
    ),
    # Tab or comma separated: 1<TAB>Bijan Robinson<TAB>RB<TAB>ATL
    (
        "delimited",
        re.compile(r"^(?P<pick>\d{1,3})[\t,;]\s*(?P<player>[^\t,;]+?)" + _SUFFIX),
    ),
    # Bare name, one per line, in pick order.
    ("bare name", re.compile(r"^(?P<player>[A-Za-z][A-Za-z.'’\- ]{2,40}?)" + _SUFFIX)),
)


# Words that are page furniture rather than any part of a pick. A line is
# skipped only when EVERY token on it is one of these -- a header row is noise
# whether it arrives as "Round 1" or as "Pick<TAB>Player<TAB>Pos<TAB>Team". A
# line with one furniture word and one real name is a pick, and is parsed.
#
# Skipping is deliberately this conservative: a header wrongly treated as a pick
# raises immediately and is obvious, while a pick wrongly treated as a header
# vanishes into a board that still looks complete.
FURNITURE = frozenset(
    "round pick picks player players pos position team nfl bye no # overall".split()
)
_TOKENS = re.compile(r"[\t,;|]+|\s{2,}|\s")


def _is_furniture(line: str) -> bool:
    tokens = [tok.strip(" .#") for tok in _TOKENS.split(line) if tok.strip(" .#")]
    if not tokens:
        return True
    return all(tok.lower() in FURNITURE or tok.isdigit() or set(tok) <= {"-", "="}
               for tok in tokens)


class DraftLogError(ValueError):
    """The paste does not describe a draft this league could have had."""


def _reclaim_name_suffix(found: dict[str, Any]) -> dict[str, Any]:
    """Give back a generational suffix the team group swallowed.

    "Kenneth Walker III" ends in three capitals and is not a player on a team
    called III. names.SUFFIXES is already the vocabulary the crosswalk strips, so
    the two agree on what is part of a name by construction rather than by two
    lists that drift.
    """
    team = found.get("team")
    if team and team.lower().strip(".") in SUFFIXES:
        found["player"] = f"{found.get('player', '')} {team}".strip()
        found["team"] = None
    return found


def _clean(text: str) -> list[str]:
    return [ln.strip() for ln in text.replace("\r\n", "\n").split("\n") if ln.strip()]


def parse_lines(text: str) -> list[dict[str, Any]]:
    """One row per pick, in the order given. Raises on a line it cannot read."""
    rows: list[dict[str, Any]] = []
    for number, raw in enumerate(_clean(text), start=1):
        if _is_furniture(raw):
            continue
        for shape, pattern in LINE_PATTERNS:
            match = pattern.match(raw)
            if not match:
                continue
            found = _reclaim_name_suffix(match.groupdict())
            player = (found.get("player") or "").strip(" ,\t-")
            if not player:
                continue
            rows.append(
                {
                    "overall_pick": _overall(found, len(rows) + 1),
                    "source_player_name": player,
                    "position": found.get("position"),
                    "team": found.get("team"),
                    "parsed_as": shape,
                }
            )
            break
        else:
            raise DraftLogError(
                f"line {number} does not look like any draft-result shape this parser "
                f"knows: {raw!r}. Known shapes: {[s for s, _ in LINE_PATTERNS]}. Fix the "
                "line or add the shape to LINE_PATTERNS -- a line silently skipped here "
                "is a pick missing from the board, and nothing downstream can tell."
            )
    return rows


def _overall(found: dict[str, Any], position_in_file: int) -> int | None:
    """The overall pick number, when the shape states one.

    None where the shape does not: the round.pick form needs the league's team
    count to resolve, which the parser does not have and will not assume.
    """
    if found.get("pick"):
        return int(found["pick"])
    return None if found.get("round") else position_in_file


def parse(
    text: str, *, teams: int, rounds: int | None = None, partial: bool = False
) -> list[dict[str, Any]]:
    """Parse a paste into picks, numbered and checked against the league's shape.

    ``rounds`` defaults to whatever makes the paste a whole number of rounds.
    ``partial`` accepts a short draft -- for a board that really was abandoned
    early, never as a way past a parse that dropped lines.
    """
    if teams < 2:
        raise DraftLogError(f"a {teams}-team league cannot have a draft")
    rows = parse_lines(text)
    if not rows:
        raise DraftLogError("no picks found in the paste (S76)")

    for index, row in enumerate(rows):
        resolved = row["overall_pick"]
        row["overall_pick"] = index + 1 if resolved is None else resolved
        row["round"] = (row["overall_pick"] - 1) // teams + 1
        row["slot"] = _slot_of(row["overall_pick"], teams)

    _assert_sequential(rows)
    expected = teams * (rounds if rounds is not None else -(-len(rows) // teams))
    if len(rows) != expected and not partial:
        raise DraftLogError(
            f"parsed {len(rows)} picks; a {teams}-team draft of "
            f"{expected // teams} rounds is {expected}. A paste that is short by a few "
            "picks is usually a line the parser skipped or a row the page wrapped, and "
            "a board missing picks is wrong about who was available at every pick after "
            "the gap. Re-paste, or pass partial=True if the draft really did end early."
        )
    return rows


def _slot_of(overall: int, teams: int) -> int:
    """Which seat made this pick, in a snake draft.

    The inverse of survival.held_picks(), and the reason the log can be checked
    against the sheet at all: odd rounds run 1..teams, even rounds run back.
    """
    index = (overall - 1) % teams
    return index + 1 if ((overall - 1) // teams) % 2 == 0 else teams - index


def _assert_sequential(rows: list[dict[str, Any]]) -> None:
    """Overall picks must be 1..n with no gap and no repeat.

    A duplicate or a hole means the paste lost or doubled a line, which is the
    failure this module exists to refuse.
    """
    seen = [r["overall_pick"] for r in rows]
    expected = list(range(1, len(seen) + 1))
    if seen != expected:
        missing = sorted(set(expected) - set(seen))
        duplicated = sorted({p for p in seen if seen.count(p) > 1})
        raise DraftLogError(
            "the picks are not a complete sequence: "
            + (f"missing {missing[:8]}; " if missing else "")
            + (f"duplicated {duplicated[:8]}; " if duplicated else "")
            + "every pick from 1 to the last must appear exactly once (S76)."
        )


# -- the stored payload ----------------------------------------------------

# The paste is stored verbatim, exactly as ffc_adp stores the raw JSON before
# anything reads it (S84). The parser above is young and will grow shapes; when
# it does, every draft ever recorded re-parses from the original text rather than
# from whatever a previous version of the parser made of it.
PAYLOAD_VERSION = 1


def payload(
    raw_text: str,
    *,
    profile_id: str,
    season: int,
    teams: int,
    draft_slot: int,
    draft_date: dt.date,
    rounds: int | None = None,
    partial: bool = False,
) -> dict[str, Any]:
    """The record of one league's draft, ready to be snapshotted.

    Parses first and raises on anything malformed, so a refusal happens before a
    file is written rather than after -- a half-written draft log is worse than
    none, because S84 will not let it be overwritten.
    """
    picks = parse(raw_text, teams=teams, rounds=rounds, partial=partial)
    if not 1 <= draft_slot <= teams:
        raise DraftLogError(
            f"draft_slot {draft_slot} is outside 1..{teams}. This is the seat that was "
            "actually drawn, and it is the one value nothing else in the repository "
            "records -- the sheets were rendered for all twelve (S31.2, S83)."
        )
    return {
        "payload_version": PAYLOAD_VERSION,
        "source": SOURCE_NAME,
        "profile_id": profile_id,
        "season": season,
        "teams": teams,
        "rounds": picks[-1]["round"],
        "draft_slot": draft_slot,
        "draft_date": draft_date.isoformat(),
        "pick_count": len(picks),
        "partial": partial,
        # Verbatim. Everything above is derived from it and can be re-derived.
        "raw_text": raw_text,
    }


FILENAME = "draft_{profile_id}_{season}.json"


def filename(profile_id: str, season: int) -> str:
    return FILENAME.format(profile_id=profile_id, season=season)
