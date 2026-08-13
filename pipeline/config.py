"""Configuration loading (S14, S46, S6.1, S15).

Configuration is read from ``config/`` and never duplicated in code. The one
piece of policy enforced here is S14's rule that research runs only for league
profiles marked ``real: true``.
"""

from __future__ import annotations

import datetime as dt
import functools
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
            "roster structure. Fill in the TODO values and set `real: true`."
        )
    return profiles


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
