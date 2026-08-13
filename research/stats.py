"""The small amount of statistics S88 Week 2 is allowed to use.

DESCRIPTIVE only (S2.2, S88): no p-values, no hypothesis tests, no evidence
grades. What is here is what S19.1 and S4 require a descriptive result to
report -- an effect, a confidence interval and a sample size -- plus the
ordinary-least-squares fit S25's residual model needs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Fit:
    """An OLS fit, reported by how much it explains rather than by a p-value."""

    coefficients: dict[str, float]
    intercept: float
    r_squared: float
    n: int

    def predict(self, design: np.ndarray) -> np.ndarray:
        beta = np.array(list(self.coefficients.values()))
        return design @ beta + self.intercept


def ols(design: np.ndarray, target: np.ndarray, names: list[str]) -> Fit:
    """Least squares with an intercept, via lstsq rather than a normal-equation inverse."""
    if design.ndim != 2:
        raise ValueError("design must be 2-d")
    rows = design.shape[0]
    if rows != target.shape[0]:
        raise ValueError(f"design has {rows} rows, target has {target.shape[0]}")
    padded = np.column_stack([design, np.ones(rows)])
    solution, *_ = np.linalg.lstsq(padded, target, rcond=None)
    fitted = padded @ solution
    residual = target - fitted
    total = target - target.mean()
    denominator = float(total @ total)
    r_squared = 1.0 - float(residual @ residual) / denominator if denominator else 0.0
    return Fit(
        coefficients=dict(zip(names, (float(c) for c in solution[:-1]), strict=True)),
        intercept=float(solution[-1]),
        r_squared=r_squared,
        n=rows,
    )


def r_squared_of(x: np.ndarray, y: np.ndarray) -> float:
    """Share of variance in y explained by a single predictor x."""
    if len(x) < 3:
        return 0.0
    return float(np.corrcoef(x, y)[0, 1] ** 2)


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    """Rank correlation -- S19.1 asks for the relationship, not a linear slope."""
    if len(x) < 3:
        return float("nan")
    return float(np.corrcoef(_ranks(x), _ranks(y))[0, 1])


def _ranks(values: np.ndarray) -> np.ndarray:
    order = values.argsort()
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = np.arange(len(values), dtype=float)
    return ranks


def mean_ci(values: np.ndarray, confidence: float = 0.95) -> tuple[float, float, float]:
    """Mean and a normal-approximation interval. Returns (mean, low, high)."""
    n = len(values)
    if n == 0:
        return (float("nan"),) * 3
    mean = float(values.mean())
    if n < 2:
        return mean, float("nan"), float("nan")
    stderr = float(values.std(ddof=1)) / math.sqrt(n)
    z = 1.959963984540054 if confidence == 0.95 else _z_for(confidence)
    return mean, mean - z * stderr, mean + z * stderr


def proportion_ci(hits: int, n: int, confidence: float = 0.95) -> tuple[float, float, float]:
    """Wilson interval -- S4 requires a CI on every rate, and the normal one
    misbehaves on the small per-bucket samples S5.1 warns about."""
    if n == 0:
        return (float("nan"),) * 3
    z = 1.959963984540054 if confidence == 0.95 else _z_for(confidence)
    p = hits / n
    denominator = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denominator
    margin = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denominator
    return p, max(0.0, centre - margin), min(1.0, centre + margin)


def _z_for(confidence: float) -> float:
    # Inverse normal CDF via bisection: adequate here and avoids a scipy dependency
    # for a single number.
    target = (1 + confidence) / 2
    low, high = 0.0, 10.0
    for _ in range(200):
        mid = (low + high) / 2
        if _normal_cdf(mid) < target:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def _normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))
