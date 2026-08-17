"""Sleeper projection adapter -- S38.1's second provider.

S38.1's whole treatment is a spread between providers, and a spread needs two.
This is the second, and the reason it is Sleeper rather than either of the two
S11 names for manual import is recorded in config/sources.yaml: FantasyPros'
consensus AGGREGATES ESPN and FFToday, so both are components of the board this
repository already draws from. Comparing an aggregate against one of its own
inputs measures how far a contributor sits from a mean containing it, which is
smaller by construction -- S38.1's own "share upstream inputs" failure, arriving
as a board where almost nothing is marked and the absence of a mark means
nothing.

Two things shape this adapter, and both are consequences of where it runs.

**There is no key.** Sleeper's API is free for non-commercial use and requires no
credential, so unlike FantasyPros there is no gate and no skip-because-unkeyed
path. What there is instead is a courtesy limit -- fewer than 1000 calls a
minute -- and one request per capture, which is not close to it. Nothing here
should ever loop per player.

**The response shape is unverified.** api.sleeper.app answers 403 at CONNECT from
the development sandbox, exactly as FantasyPros and FFC do, so this adapter
cannot be written against an observed payload the way ffc_adp.py was. Everything
shape-dependent -- host, path, query parameters, container keys, column names --
is configuration in ``config/sources.yaml``, and the raw payload is persisted
unmodified before anything parses it. The first successful runner call answers
`sleeper_projection_shape` in research/questions.yaml FROM THE ARCHIVE, and a
wrong guess is corrected in YAML and re-parsed against the same stored bytes.

That last property is what makes a blind adapter safe to schedule: a capture
taken under a wrong mapping is not a lost capture. It is the same bytes, waiting
for a better parser.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from pipeline.config import sleeper_config
from pipeline.ingest.base import Fetched, FetchError, http_get

SOURCE_NAME = "sleeper_api"
PROVIDER_ID = "sleeper"

# The projection columns S13 declares, in player_week's spelling so
# pipeline/scoring.py prices a projection with the same code it prices a season
# with. A mapping that produces none of these has not been mapped.
CANONICAL_STATS = (
    "pass_attempts", "pass_completions", "passing_yards", "passing_tds", "interceptions",
    "carries", "rushing_yards", "rushing_tds",
    "targets", "receptions", "receiving_yards", "receiving_tds",
    "fumbles_lost", "projected_fantasy_points", "projected_games",
)


class ResponseShapeError(ValueError):
    """The payload does not match the shape configured for it."""


class SleeperAdapter:
    """Fetches the season projection board in one request."""

    source_name = SOURCE_NAME

    def __init__(self, season: int, config: dict[str, Any] | None = None) -> None:
        self.season = season
        self.config = config if config is not None else sleeper_config()

    def hosts(self) -> list[str]:
        """Every host to try, in order.

        The endpoint is reported at both api.sleeper.com and api.sleeper.app and
        this adapter cannot reach either to settle it. Ordered candidates rather
        than a guess is the pattern pipeline/ingest/nflverse.py already uses for
        release locations, and it is why a 14-season build survived upstream
        moving weekly stats to a new release.
        """
        base = str(self.config.get("api_base", "")).rstrip("/")
        if not base:
            raise ResponseShapeError(
                "config/sources.yaml sleeper_api is missing `api_base`; the endpoint is "
                "configuration, not a literal in pipeline/ingest/sleeper.py (S11)."
            )
        out = [base]
        for extra in self.config.get("api_base_fallbacks") or []:
            candidate = str(extra).rstrip("/")
            if candidate and candidate not in out:
                out.append(candidate)
        return out

    def path(self) -> str:
        path = str(self.config.get("projections_path", "")).lstrip("/")
        if not path:
            raise ResponseShapeError(
                "config/sources.yaml sleeper_api is missing `projections_path` (S11)."
            )
        return path.format(season=self.season)

    def query(self) -> dict[str, Any]:
        params = dict(self.config.get("params") or {})
        key = str(self.config.get("position_param") or "position")
        params[key] = list(self.config.get("positions") or ["QB", "RB", "WR", "TE"])
        return params

    def fetch(self) -> list[Fetched]:
        params = self.query()
        path = self.path()
        attempts: list[str] = []
        data: bytes | None = None
        url = ""
        for base in self.hosts():
            url = f"{base}/{path}"
            try:
                data = http_get(url, params=params)
                break
            except FetchError as exc:
                attempts.append(f"{url}: {exc}")
        if data is None:
            raise FetchError(
                "every configured Sleeper host failed. Tried:\n  " + "\n  ".join(attempts)
                + "\nCorrect `api_base`/`api_base_fallbacks` in config/sources.yaml (S11)."
            )

        payload = json.loads(data)
        rows = _container(payload, self.config)
        if not rows:
            raise ResponseShapeError(
                f"Sleeper returned no rows under container key "
                f"{self.config.get('container_key')!r}. Payload is a "
                f"{type(payload).__name__}; top-level keys present: "
                f"{sorted(payload)[:20] if isinstance(payload, dict) else 'n/a (list)'}. "
                "Correct `container_key` in config/sources.yaml rather than this module "
                "(S11). The bytes are not lost -- nothing has been written yet, and the "
                "next run re-fetches."
            )

        return [
            Fetched(
                filename=f"sleeper_projections_{self.season}.json",
                data=data,  # unmodified bytes, exactly as served
                url=url,
                source=SOURCE_NAME,
                license=self.config.get("license", "provider_specific"),
                notes=(
                    f"{len(rows)} rows, season {self.season}. "
                    f"Row keys observed: {sorted(_observed_keys(rows))}. "
                    f"Stat keys observed: {sorted(_observed_stat_keys(rows, self.config))}. "
                    f"Player keys observed: {sorted(_observed_player_keys(rows, self.config))}"
                ),
                extra={
                    "provider_id": PROVIDER_ID,
                    "transport": "api",
                    "season": self.season,
                    "row_count": len(rows),
                    "host_used": url.split("/")[2] if "//" in url else url,
                    # The probe. These land in the snapshot manifest, which is
                    # committed, so the shape question is answered from the
                    # archive rather than from a CI log that ages out.
                    "observed_row_keys": sorted(_observed_keys(rows)),
                    "observed_stat_keys": sorted(_observed_stat_keys(rows, self.config)),
                    "observed_player_keys": sorted(_observed_player_keys(rows, self.config)),
                },
            )
        ]


def _container(payload: Any, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the row list out of a payload whose envelope is configuration."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    key = config.get("container_key")
    if not key:
        return []
    node: Any = payload
    for part in str(key).split("."):
        if not isinstance(node, dict):
            return []
        node = node.get(part)
    return [r for r in node if isinstance(r, dict)] if isinstance(node, list) else []


def _sub(raw: dict[str, Any], config: dict[str, Any], key_name: str) -> dict[str, Any]:
    """A configured sub-object of a row, or the row itself when unset."""
    key = config.get(key_name)
    if not key:
        return raw
    nested = raw.get(str(key))
    return nested if isinstance(nested, dict) else {}


def _observed_keys(rows: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for row in rows[:50]:  # a field can be absent on an individual row
        keys.update(row)
    return keys


def _observed_stat_keys(rows: list[dict[str, Any]], config: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for row in rows[:50]:
        keys.update(_sub(row, config, "stat_container_key"))
    return keys


def _observed_player_keys(rows: list[dict[str, Any]], config: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for row in rows[:50]:
        keys.update(_sub(row, config, "player_container_key"))
    return keys


def published_points(
    raw: dict[str, Any], config: dict[str, Any] | None = None, *, scoring: str = "half_ppr"
) -> float | None:
    """The provider's OWN scored total for one row, if it publishes one.

    Deliberately not mapped into the frame. This is the CHECK on `stat_map`, not
    an input to it: scoring the mapped stats under a league profile has to
    reproduce this number to the cent, which is the only way to tell a correct
    mapping from one that merely computes without erroring. Mapping it in would
    make the check compare a column to itself, which is how a stat map that is
    wrong in a way that still produces plausible totals survives review.
    """
    cfg = config if config is not None else sleeper_config()
    col = (cfg.get("published_points") or {}).get(scoring)
    if not col:
        return None
    return _as_float(_sub(raw, cfg, "stat_container_key").get(str(col)))


def parse(
    payload_bytes: bytes,
    *,
    snapshot_date: dt.date,
    season: int,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Normalize a stored payload into projection_snapshot rows (S13).

    ``value_type`` is ``derived``: a projection is somebody's model output, not
    an observation, and S37's vocabulary has a word for that.

    Identity is taken from the provider where the provider supplies it. Sleeper
    publishes cross-provider ids on its player objects, `gsis_id` among them, so
    when the configured column is present the row arrives already carrying S12's
    canonical id and needs no name match at all. That is strictly better than the
    FantasyPros path, which is name-matched -- and name matching is why
    `Travis Hunter` resolved to nothing across twelve captures.
    """
    cfg = config if config is not None else sleeper_config()
    payload = json.loads(payload_bytes)
    rows = _container(payload, cfg)
    stat_map: dict[str, str] = {k: v for k, v in (cfg.get("stat_map") or {}).items() if v}
    if not rows:
        return []

    observed = _observed_stat_keys(rows, cfg)
    mapped = {dest for src, dest in stat_map.items() if src in observed}
    if not mapped & set(CANONICAL_STATS):
        raise ResponseShapeError(
            "no configured stat_map column matched the Sleeper payload. Keys observed: "
            f"{sorted(observed)}. stat_map source columns: {sorted(stat_map)}. Fix the "
            "mapping in config/sources.yaml (S11) -- emitting a frame of nulls here "
            "would look like a provider with no projections. The stored bytes are "
            "unchanged, so a corrected mapping re-parses this same capture."
        )

    gsis_col = cfg.get("gsis_id_col")
    out: list[dict[str, Any]] = []
    for raw in rows:
        player = _sub(raw, cfg, "player_container_key")
        stats = _sub(raw, cfg, "stat_container_key")
        row: dict[str, Any] = {
            "season": season,
            "snapshot_date": snapshot_date,
            "as_of": snapshot_date,
            "source_as_of": snapshot_date,
            "source": SOURCE_NAME,
            "provider_id": PROVIDER_ID,
            "transport": "api",
            "source_player_id": _as_str(
                raw.get(cfg.get("id_col", "player_id")) or player.get("player_id")
            ),
            "source_player_name": _identity(raw, player, cfg, "name_col", "full_name"),
            "team": _identity(raw, player, cfg, "team_col", "team"),
            "position": _identity(raw, player, cfg, "position_col", "position"),
            "value_type": "derived",
        }
        if gsis_col:
            resolved = _as_str(player.get(str(gsis_col)) or raw.get(str(gsis_col)))
            if resolved:
                row["player_id"] = resolved
                row["match_method"] = "provider_gsis_id"
                row["match_confidence"] = 1.0
        _accumulate(row, stats, stat_map)
        out.append(row)
    return out


def _accumulate(
    row: dict[str, Any], stats: dict[str, Any], stat_map: dict[str, str]
) -> None:
    """Map the provider's stat columns onto ours, SUMMING repeated destinations.

    `fantasypros.parse` assigns 1:1, which is right for a provider publishing one
    column per quantity. Sleeper splits two-point conversions across `pass_2pt`,
    `rush_2pt` and `rec_2pt`, and an assignment loop would keep whichever came
    last in dict order -- a third of the real total, arriving as a number rather
    than as an error, on a column both league profiles score.

    A destination with no source present stays null rather than becoming zero.
    Absent and none-of-them are different facts, and S37 has words for both: a
    zero here would tell `populated_scored_stats` the provider publishes a column
    it does not, which is the one thing S38.1's comparability gate exists to
    catch.
    """
    for src_col, dest_col in stat_map.items():
        value = _as_float(stats.get(src_col))
        if value is None:
            row.setdefault(dest_col, None)
            continue
        existing = row.get(dest_col)
        row[dest_col] = value if existing is None else existing + value


def _identity(
    raw: dict[str, Any], player: dict[str, Any], cfg: dict[str, Any], key: str, default: str
) -> Any:
    """Identity fields, from the row or the nested player object.

    Which of the two carries them is exactly the thing this adapter cannot see,
    so it reads both rather than guessing -- and the manifest records which keys
    each actually held.
    """
    col = str(cfg.get(key, default))
    value = raw.get(col)
    if value in (None, ""):
        value = player.get(col)
    if value in (None, "") and col == "full_name":
        first, last = player.get("first_name"), player.get("last_name")
        if first and last:
            return f"{first} {last}"
    return value


def _as_float(v: Any) -> float | None:
    if v in (None, "", "-"):
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def _as_str(v: Any) -> str | None:
    return None if v in (None, "") else str(v)
