"""Configuration loading (S14, S46, S6.1, S15).

Configuration is read from ``config/`` and never duplicated in code. The one
piece of policy enforced here is S14's rule that research runs only for league
profiles marked ``real: true``.
"""

from __future__ import annotations

import datetime as dt
import functools
import os
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
PROCESSED_DIR = DATA_DIR / "processed"
RESEARCH_DIR = PROJECT_ROOT / "research"


class ConfigError(RuntimeError):
    """Raised when configuration is missing or blocks the requested action."""


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"missing config file: {path}")
    with path.open() as fh:
        return yaml.safe_load(fh) or {}


@functools.cache
def league_profiles() -> list[dict[str, Any]]:
    return load_yaml(CONFIG_DIR / "league_profiles.yaml").get("active_profiles", [])


def real_profiles() -> list[dict[str, Any]]:
    """Profiles the drafter actually plays (S14)."""
    return [p for p in league_profiles() if p.get("real") is True]


# A value the drafter has actually established is unknowable right now, as
# distinct from one nobody has got round to. `unknown` is an answer; TODO is the
# absence of one, and validate_profile() refuses to let a TODO through the S14
# gate. The difference is the whole reason a profile can be real without a draft
# date: a league whose slot is drawn an hour before the draft is not an
# incompletely configured league.
UNKNOWN = "unknown"
_PLACEHOLDER = "TODO"


def _is_unknown(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() == UNKNOWN)


def draft_slot(profile: dict[str, Any]) -> int | None:
    """The drafter's seat, or None if the order is undrawn (S31.2).

    None is a supported state, not a failure. Survival answers it by reporting
    every slot and S83 by pre-rendering a sheet per slot, so the hour between
    the draw and the first pick costs a file open rather than a rebuild.
    """
    value = profile.get("draft_slot")
    if _is_unknown(value):
        return None
    try:
        slot = int(value)
    except (TypeError, ValueError):
        raise ConfigError(
            f"profile {profile.get('id')!r} sets draft_slot {value!r}, which is neither a "
            f"seat number nor `{UNKNOWN}`. An unparseable slot must not fall through as "
            "undrawn -- that turns a typo into twelve confidently rendered sheets."
        ) from None
    teams = int(profile["teams"])
    if not 1 <= slot <= teams:
        raise ConfigError(
            f"profile {profile.get('id')!r} sets draft_slot {slot}, which is outside "
            f"1..{teams}. A slot outside the league is a typo, not a draft position."
        )
    return slot


def draft_date(profile: dict[str, Any]) -> dt.date | None:
    """When the league drafts, or None if that is not yet known.

    Nothing in the current build reads this: the sheet prices off the most recent
    archived ADP capture, which is the right rule when the draft is imminent and
    the only rule available when the date is unknown. S36.2's simulator will want
    a real date; until then an unknown one costs nothing and is recorded as a
    stated limitation rather than pretended away.
    """
    value = profile.get("draft_date")
    if _is_unknown(value):
        return None
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value))


def draft_season(profile: dict[str, Any]) -> int:
    """The season this league is drafting for (S6.1, S31.2).

    Not optional, and not defaultable to "whatever is in the table". The ADP
    archive backfilled 2018-2025 on the same capture day it took 2026, so every
    historical season shares a snapshot_date with the live one -- and a board
    built from "the newest capture" without a season filter interleaves nine
    drafts into one ranking. That shipped: Todd Gurley and Dalvin Cook were on
    the 2026 survival board, priced against Jahmyr Gibbs.

    The draft date's year when it is known; the current year when it is not,
    which is the same rule `research draft-record` applies to a recorded draft.
    """
    date = draft_date(profile)
    if date is not None:
        return date.year
    return dt.datetime.now(dt.UTC).date().year


def validate_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Reject a profile still carrying a placeholder (S14).

    A profile marked `real: true` with `draft_slot: "TODO"` in it would flow
    through as an undrawn slot and generate twelve sheets nobody asked for -- a
    plausible output from an unanswered question, which is the exact failure S14
    exists to prevent. `unknown` passes; TODO does not.
    """
    for field in ("draft_date", "draft_slot"):
        value = profile.get(field)
        if isinstance(value, str) and value.strip().upper() == _PLACEHOLDER:
            raise ConfigError(
                f"profile {profile.get('id')!r} still has {field}: {value!r} in "
                "config/league_profiles.yaml. Set the real value, or `unknown` if it "
                "genuinely is not known yet -- `unknown` is an answer the build handles "
                "(S31.2 reports every slot, S83 renders a sheet per slot) and TODO is not."
            )
    draft_slot(profile)
    draft_date(profile)
    return profile


def require_real_profiles() -> list[dict[str, Any]]:
    """Gate every research entry point (S14).

    Tiers, replacement level, opportunity cost and survival probability are all
    conditional on scoring, team count and draft slot. Running research against
    a placeholder profile produces numbers that look real and are not.
    """
    profiles = real_profiles()
    if not profiles:
        raise ConfigError(
            "no league profile is marked `real: true` in config/league_profiles.yaml. "
            "S14 requires the leagues actually being drafted to be encoded before any "
            "research runs -- every downstream conclusion is conditional on scoring and "
            "roster structure. Encode the leagues and set `real: true`."
        )
    return [validate_profile(p) for p in profiles]


class UnknownFormatError(ConfigError):
    """A profile's scoring does not map onto any ADP format we can capture."""


# Fantasy Football Calculator's format names, as used by `adp_capture` below and
# by pipeline/ingest/ffc_adp.py.
RECEPTION_TO_FORMAT = {0.0: "standard", 0.5: "half-ppr", 1.0: "ppr"}
SUPERFLEX_FORMAT = "2qb"


def profile_adp_format(profile: dict[str, Any]) -> str:
    """Which ADP format a league profile needs priced (S14, S84).

    Profiles declare scoring; `adp_capture` declares formats; nothing related
    the two, so a league could be encoded whose price history was never being
    archived -- and by the time anyone noticed, the days would be gone (S84).

    A second quarterback slot dominates the scoring question: superflex ADP is a
    different board, not a PPR board with a tweak.
    """
    starters = profile.get("starters") or {}
    if "SUPERFLEX" in starters or (starters.get("QB") or 0) > 1:
        return SUPERFLEX_FORMAT
    reception = (profile.get("scoring") or {}).get("reception")
    if reception is None:
        raise UnknownFormatError(
            f"profile {profile.get('id')!r} declares no `reception` value, so its ADP "
            "format cannot be derived (S14)"
        )
    try:
        return RECEPTION_TO_FORMAT[float(reception)]
    except KeyError:
        raise UnknownFormatError(
            f"profile {profile.get('id')!r} scores a reception at {reception}, which is "
            f"not one of {sorted(RECEPTION_TO_FORMAT)}. Fantasy Football Calculator "
            "publishes no ADP for it, so the league cannot be priced (S84)."
        ) from None


@functools.cache
def adp_capture_formats() -> list[dict[str, Any]]:
    """Formats the archival job captures (S84).

    Deliberately a superset of the real profiles: the value of a missed day is
    unrecoverable, so capture broadly until the real profiles are encoded.
    """
    formats = load_yaml(CONFIG_DIR / "league_profiles.yaml").get("adp_capture", [])
    if not formats:
        raise ConfigError("config/league_profiles.yaml defines no `adp_capture` formats")
    return formats


@functools.cache
def decision_dates() -> dict[int, dt.date]:
    """Per-season decision date -- the leakage cutoff (S6.1)."""
    raw = load_yaml(CONFIG_DIR / "decision_dates.yaml").get("decision_dates", {})
    out: dict[int, dt.date] = {}
    for season, value in raw.items():
        out[int(season)] = (
            value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value))
        )
    return out


def decision_date(season: int) -> dt.date:
    dates = decision_dates()
    if season not in dates:
        raise ConfigError(
            f"no decision date for season {season} in config/decision_dates.yaml. "
            "S6.1 requires one per season -- the leakage assertion cannot run without it."
        )
    return dates[season]


@functools.cache
def sources() -> dict[str, Any]:
    return load_yaml(CONFIG_DIR / "sources.yaml").get("sources", {})


@functools.cache
def projection_providers() -> dict[str, Any]:
    """Manual projection exports declared for import (S11 option 1B).

    Top-level in sources.yaml, a sibling of `sources:` rather than a member of
    it. Reading it off `sources()` returns None whatever is configured, which
    is how research/foundations/tiers.py came to report its projection blocker
    permanently -- including once a provider was configured. One accessor now,
    used by both the adapter and the blocker.
    """
    return load_yaml(CONFIG_DIR / "sources.yaml").get("projection_providers") or {}


DEFAULT_BOARD_PROVIDER = "fantasypros"


def board_provider() -> str:
    """Which archived provider S19.3 draws the tier board from (S38.1).

    Configuration rather than inference. The board used to be drawn from
    whichever provider_id sorted first, which is a rule that works exactly as
    long as there is only one of them -- and S38.1 exists to add a second.
    """
    return load_yaml(CONFIG_DIR / "sources.yaml").get("board_provider") or DEFAULT_BOARD_PROVIDER


def fantasypros_config() -> dict[str, Any]:
    """The FantasyPros API block (S11 option 1).

    Endpoint, envelope and column names live here rather than in the adapter:
    the API is unreachable from the development sandbox, so the response shape
    is unverified and a wrong guess has to be correctable in YAML.
    """
    return sources().get("fantasypros_api") or {}


def sleeper_config() -> dict[str, Any]:
    """The Sleeper API block (S38.1's second provider).

    Same reasoning as `fantasypros_config`: the host is unreachable from the
    development sandbox, so the response shape is a guess and a wrong guess has
    to be correctable in YAML rather than in the adapter.
    """
    return sources().get("sleeper_api") or {}


def projection_source_available() -> bool:
    """Whether any projection path is configured at all (S11, S19.3)."""
    if os.environ.get("FANTASYPROS_API_KEY", "").strip() and fantasypros_config().get("api_base"):
        return True
    return bool(projection_providers())


def source(name: str) -> dict[str, Any]:
    try:
        return sources()[name]
    except KeyError as exc:
        raise ConfigError(
            f"source '{name}' is not registered in config/sources.yaml (S46)"
        ) from exc


@functools.cache
def outcomes() -> dict[str, Any]:
    return load_yaml(CONFIG_DIR / "outcomes.yaml").get("outcomes", {})


@functools.cache
def manual_id_overrides() -> list[dict[str, Any]]:
    return load_yaml(CONFIG_DIR / "manual_id_overrides.yaml").get("overrides") or []
