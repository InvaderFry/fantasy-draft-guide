"""The research-method contract and its exported artifact (S16, S49).

S49 asks for methods to be plug-ins rather than one-off notebooks. This is the
subset of that interface S88 Week 2 needs: a method declares who it studies,
computes a result, and exports it. `validate` and `grade_evidence` are
deliberately absent -- S88 permits DESCRIPTIVE claims only, and S3.1 requires
grades be assigned by code from thresholds committed before any analysis runs.
Adding a grading hook now would invite exactly the hand-assigned grade S80
prohibits.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pipeline.config import PROJECT_ROOT

ARTIFACT_DIR = PROJECT_ROOT / "artifacts"

# S2.2. Week 2 may use only the first.
CLAIM_TYPES = ("DESCRIPTIVE", "PREDICTIVE", "PRESCRIPTIVE", "HYPOTHESIS")


class ClaimTypeError(ValueError):
    """A method claimed more than its evidence supports (S2.2, S88)."""


@dataclass
class MethodArtifact:
    """The S16 JSON document every method exports.

    Field names follow S16's example. `evidence_grade` is not a field: an
    artifact with no grade is unambiguous, where a null one invites something
    to fill it in.
    """

    method_id: str
    title: str
    version: str
    claim_type: str
    population: dict[str, Any]
    outcome: str | None
    sample_size: int
    primary_results: dict[str, Any] = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    spec_sections: list[str] = field(default_factory=list)
    generated_on: dt.date = field(default_factory=lambda: dt.datetime.now(dt.UTC).date())

    def __post_init__(self) -> None:
        if self.claim_type not in CLAIM_TYPES:
            raise ClaimTypeError(
                f"{self.method_id}: claim_type {self.claim_type!r} is not one of "
                f"{list(CLAIM_TYPES)} (S2.2)"
            )

    def to_dict(self) -> dict[str, Any]:
        out = {
            "method_id": self.method_id,
            "title": self.title,
            "version": self.version,
            "claim_type": self.claim_type,
            "spec_sections": self.spec_sections,
            "generated_on": self.generated_on.isoformat(),
            "population": self.population,
            "outcome": self.outcome,
            "sample_size": self.sample_size,
            "primary_results": self.primary_results,
            "limitations": self.limitations,
            "sources": self.sources,
        }
        return out

    def write(self, edition: str, root: Path = ARTIFACT_DIR) -> Path:
        directory = root / edition / "methods"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.method_id}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str) + "\n")
        return path


class ResearchMethod(Protocol):
    """S49, reduced to what a descriptive Week 2 analysis needs."""

    method_id: str
    version: str

    def population(self) -> Any: ...

    def compute(self, population: Any) -> dict[str, Any]: ...

    def export(self, results: dict[str, Any]) -> MethodArtifact: ...


def default_edition(today: dt.date | None = None) -> str:
    """S9's edition directory name, e.g. 2026.08.13-r1."""
    day = today or dt.datetime.now(dt.UTC).date()
    return f"{day:%Y.%m.%d}-r1"
