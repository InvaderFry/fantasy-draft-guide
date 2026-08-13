"""Deterministic name normalization for cross-source joins (S12).

Names are a last resort, used only where a source publishes no ID that maps to
``gsis_id`` -- Fantasy Football Calculator and manual projection exports both
do. Every row joined this way carries ``match_method`` and ``match_confidence``
so a downstream reader can see how a join was made, and unmatched rows are
reported rather than dropped.
"""

from __future__ import annotations

import re
import unicodedata

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

# Sources spell teams differently; normalize to the nflverse abbreviation.
TEAM_ALIASES = {
    "JAC": "JAX",
    "LA": "LAR",
    "STL": "LAR",
    "SD": "LAC",
    "OAK": "LV",
    "LVR": "LV",
    "WSH": "WAS",
    "ARZ": "ARI",
    "BLT": "BAL",
    "CLV": "CLE",
    "HST": "HOU",
    "SL": "LAR",
}

POSITION_ALIASES = {
    "PK": "K",
    "DST": "DEF",
    "D/ST": "DEF",
    "FB": "RB",
}


def normalize_name(name: str | None) -> str:
    """Casefold, strip accents, punctuation and generational suffixes."""
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z\s]", " ", text)
    parts = [p for p in text.split() if p]
    while parts and parts[-1] in SUFFIXES:
        parts.pop()
    return " ".join(parts)


def normalize_team(team: str | None) -> str | None:
    if not team:
        return None
    key = team.strip().upper()
    return TEAM_ALIASES.get(key, key)


def normalize_position(position: str | None) -> str | None:
    if not position:
        return None
    key = position.strip().upper()
    return POSITION_ALIASES.get(key, key)


def match_key(name: str | None, position: str | None, team: str | None) -> str:
    """The join key used when no shared ID exists."""
    return (
        f"{normalize_name(name)}|{normalize_position(position) or ''}"
        f"|{normalize_team(team) or ''}"
    )


def name_position_key(name: str | None, position: str | None) -> str:
    """Weaker fallback for players who changed team between snapshots."""
    return f"{normalize_name(name)}|{normalize_position(position) or ''}"
