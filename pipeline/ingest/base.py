"""Adapter protocol shared by every source (S11, S46).

The research code must not depend on one projection or ADP provider. An
adapter is responsible for three things and nothing else: fetching raw bytes,
describing itself for the snapshot manifest, and parsing its own payload.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

import requests

USER_AGENT = "fantasy-draft-guide/0.1 (research; contact via repo)"
DEFAULT_TIMEOUT = 30


@dataclass(frozen=True)
class Fetched:
    """One raw payload plus the metadata the snapshot manifest needs."""

    filename: str
    data: bytes
    url: str
    source: str
    license: str | None = None
    notes: str | None = None
    extra: dict[str, Any] | None = None


class SourceAdapter(Protocol):
    """Minimal contract. Implementations live beside this file."""

    source_name: str

    def fetch(self) -> list[Fetched]:
        """Retrieve raw payloads. Must not parse, must not mutate state."""
        ...


class FetchError(RuntimeError):
    """Raised when a source cannot be retrieved after retries."""


def http_get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = 4,
    session: requests.Session | None = None,
) -> bytes:
    """GET with exponential backoff.

    Network flakiness must not be indistinguishable from a source returning
    nothing: a genuine empty response is a lost capture day (S84) and has to
    surface as an error, so this raises rather than returning b"".

    ``headers`` carries per-source authentication. It is deliberately not
    echoed into the FetchError below: a key in an exception message is a key in
    a CI log (S11).
    """
    sess = session or requests.Session()
    delay = 2.0
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = sess.get(
                url,
                params=params,
                timeout=timeout,
                headers={"User-Agent": USER_AGENT, **(headers or {})},
            )
            resp.raise_for_status()
            if not resp.content:
                raise FetchError(f"{url} returned an empty body")
            return resp.content
        except Exception as exc:  # noqa: BLE001 - retried and re-raised below
            last = exc
            if attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
    raise FetchError(f"failed to fetch {url} after {retries} attempts: {last}") from last
