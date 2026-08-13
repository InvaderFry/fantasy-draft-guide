"""FantasyPros projection adapter (S11 option 1, S38).

S11's recommended policy is to support the authenticated API when a key is
configured and to fall back to a manual provider export otherwise. This is the
API half; ``projections_csv.py`` is the fallback, and ``pipeline/cli.py`` picks
between them in the order S11 gives.

Two things shape this adapter, and both are consequences of where it runs.

**The key is the gate.** Without ``FANTASYPROS_API_KEY`` there is no request to
make. That is a skip, not a failure -- unlike ADP, a projection missed today can
be fetched tomorrow (S84 applies to the archive, not to this). The key is read
from the environment on every fetch, never stored, and never written into the
snapshot manifest: ``url_for`` returns the URL without it and the key travels in
a header.

**The response shape is unverified.** api.fantasypros.com is not reachable from
the development sandbox (403 at CONNECT), so this adapter cannot be written
against an observed payload the way ffc_adp.py was. Everything shape-dependent
-- the path, the query parameters, the container key, the stat column names --
is therefore configuration in ``config/sources.yaml`` rather than a literal in
this file, and the raw payload is persisted unmodified before anything parses
it. The first successful runner call answers `fantasypros_projection_shape` in
research/questions.yaml from the archive, and a wrong guess is corrected in YAML.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any

from pipeline.config import fantasypros_config
from pipeline.ingest.base import Fetched, http_get

SOURCE_NAME = "fantasypros_api"
KEY_ENV_VAR = "FANTASYPROS_API_KEY"

# The projection columns S13 declares. A provider mapping that produces none of
# them has not been mapped, and parse() says so rather than emitting nulls.
# These are player_week's stat names, so pipeline/scoring.py prices a projection
# with the same code it prices a season with (see schema.py for why).
CANONICAL_STATS = (
    "pass_attempts", "pass_completions", "passing_yards", "passing_tds", "interceptions",
    "carries", "rushing_yards", "rushing_tds",
    "targets", "receptions", "receiving_yards", "receiving_tds",
    # S11 names these `fantasy_points` and `games`; both collide with
    # schema.OUTCOME_COLUMNS, where they mean what actually happened. Prefixed
    # so a projection can never be joined in as though it were a result.
    "projected_fantasy_points", "projected_games",
)


class MissingKeyError(RuntimeError):
    """No API key is configured, so there is no request to make (S11)."""


class ResponseShapeError(ValueError):
    """The payload does not match the shape configured for it."""


def api_key() -> str | None:
    key = os.environ.get(KEY_ENV_VAR, "").strip()
    return key or None


class FantasyProsAdapter:
    """Fetches one projection payload per configured position."""

    source_name = SOURCE_NAME

    def __init__(self, season: int, config: dict[str, Any] | None = None) -> None:
        self.season = season
        self.config = config if config is not None else fantasypros_config()
        self.key = api_key()

    def positions(self) -> list[str]:
        return list(self.config.get("positions") or ["QB", "RB", "WR", "TE"])

    def url_for(self, position: str) -> str:
        """The request URL, with no credential in it.

        The key goes in a header. This URL is written into the snapshot manifest
        and committed to a public repository, so a key in the query string would
        be a key in the git history.
        """
        base = str(self.config.get("api_base", "")).rstrip("/")
        path = str(self.config.get("projections_path", "")).lstrip("/")
        if not base or not path:
            raise ResponseShapeError(
                "config/sources.yaml fantasypros_api is missing `api_base` or "
                "`projections_path`; the endpoint is configuration, not a literal "
                "in pipeline/ingest/fantasypros.py (S11)"
            )
        return f"{base}/{path.format(season=self.season, position=position)}"

    def fetch(self) -> list[Fetched]:
        if not self.key:
            raise MissingKeyError(
                f"{KEY_ENV_VAR} is not set. S11's fallback order is FantasyPros API -> "
                "manual projection CSV; configure the key as a repository secret or drop "
                "a provider export in data/raw/projections/."
            )
        out: list[Fetched] = []
        params = dict(self.config.get("params") or {})
        params.setdefault("week", 0)  # 0 = full-season/draft projections
        for position in self.positions():
            url = self.url_for(position)
            data = http_get(
                url,
                params={**params, "position": position},
                headers={self.config.get("key_header", "x-api-key"): self.key},
            )
            payload = json.loads(data)
            rows = _container(payload, self.config)
            if not rows:
                raise ResponseShapeError(
                    f"FantasyPros returned no rows for {position} under container key "
                    f"{self.config.get('container_key')!r}. Top-level keys present: "
                    f"{sorted(payload)[:20]}. Correct `container_key` in "
                    "config/sources.yaml rather than this module (S11)."
                )
            out.append(
                Fetched(
                    filename=f"fantasypros_projections_{position.lower()}_{self.season}.json",
                    data=data,  # unmodified bytes, exactly as served
                    url=url,    # credential-free by construction
                    source=SOURCE_NAME,
                    license=self.config.get("license", "provider_specific"),
                    notes=(
                        f"{len(rows)} {position} rows, season {self.season}. "
                        f"Row keys observed: {sorted(_observed_keys(rows))}. "
                        f"Stat keys observed: {sorted(_observed_stat_keys(rows, self.config))}"
                    ),
                    extra={
                        "provider_id": "fantasypros",
                        "transport": "api",
                        "position": position,
                        "season": self.season,
                        "row_count": len(rows),
                        # S11's edition manifest asks for the exact provider path used.
                        "endpoint_family": self.config.get("endpoint_family"),
                        "observed_row_keys": sorted(_observed_keys(rows)),
                        "observed_stat_keys": sorted(_observed_stat_keys(rows, self.config)),
                    },
                )
            )
        return out


def _container(payload: Any, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the row list out of a payload whose envelope is configuration."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    key = config.get("container_key") or "players"
    node: Any = payload
    for part in str(key).split("."):
        if not isinstance(node, dict):
            return []
        node = node.get(part)
    return [r for r in node if isinstance(r, dict)] if isinstance(node, list) else []


def _observed_keys(rows: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for row in rows[:50]:  # a field can be absent on an individual row
        keys.update(row)
    return keys


def _stats(raw: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """The sub-object the stat columns live in.

    FantasyPros nests them: identity is flat on the row (`name`, `team_id`) and
    every projected quantity sits under `stats`. Which key that is -- or whether
    there is one at all -- is configuration, like the rest of the envelope, so a
    provider serving a flat row still parses with `stat_container_key` unset.
    """
    key = config.get("stat_container_key")
    if not key:
        return raw
    nested = raw.get(str(key))
    return nested if isinstance(nested, dict) else {}


def _observed_stat_keys(rows: list[dict[str, Any]], config: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for row in rows[:50]:
        keys.update(_stats(row, config))
    return keys


def parse(
    payload_bytes: bytes,
    *,
    snapshot_date: dt.date,
    season: int,
    position: str | None = None,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Normalize a stored payload into projection_snapshot rows (S13).

    ``value_type`` is ``derived``: a projection is somebody's model output, not
    an observation, and S37's vocabulary has a word for that.
    """
    cfg = config if config is not None else fantasypros_config()
    payload = json.loads(payload_bytes)
    rows = _container(payload, cfg)
    stat_map: dict[str, str] = dict(cfg.get("stat_map") or {})
    if not rows:
        return []

    observed = _observed_stat_keys(rows, cfg)
    mapped = {dest for src, dest in stat_map.items() if src in observed}
    if not mapped & set(CANONICAL_STATS):
        raise ResponseShapeError(
            "no configured stat_map column matched the payload. Keys observed: "
            f"{sorted(observed)}. stat_map source columns: {sorted(stat_map)}. "
            "Fix the mapping in config/sources.yaml (S11) -- emitting a frame of "
            "nulls here would look like a provider with no projections."
        )

    out: list[dict[str, Any]] = []
    for raw in rows:
        row: dict[str, Any] = {
            "season": season,
            "snapshot_date": snapshot_date,
            "as_of": snapshot_date,
            "source_as_of": snapshot_date,
            "source": SOURCE_NAME,
            "provider_id": "fantasypros",
            "transport": "api",
            "source_player_id": _as_str(raw.get(cfg.get("id_col", "player_id"))),
            "source_player_name": raw.get(cfg.get("name_col", "name")),
            "team": raw.get(cfg.get("team_col", "team_id")),
            "position": (raw.get(cfg.get("position_col", "position_id")) or position),
            "value_type": "derived",
        }
        stats = _stats(raw, cfg)
        for src_col, dest_col in stat_map.items():
            row[dest_col] = _as_float(stats.get(src_col))
        out.append(row)
    return out


def _as_float(v: Any) -> float | None:
    if v in (None, "", "-"):
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def _as_str(v: Any) -> str | None:
    return None if v in (None, "") else str(v)
