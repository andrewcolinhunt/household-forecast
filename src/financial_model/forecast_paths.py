"""Joint annual economic paths for household forecasting.

All arrays use equities, fixed income, cash, inflation, in that order. Values
are nominal simple annual rates (0.05 means 5%), after fees and before tax.
Central outlooks are compound rates, not arithmetic expected annual returns.
Volatility is the standard deviation of simple annual rates; correlation is
the Pearson correlation of log gross rates, before exponentiation.

History CSVs require the header ``year,equities,fixed_income,cash,inflation``
and consecutive annual observations. Supply a consistently constructed joint
series, with investment fees already deducted, and save its source, retrieval
date, and original rows with the forecast. No historical data or provenance is
invented here. Historical replay and bootstrap use supplied observations as-is;
they do not recenter them to an economic outlook.
"""

from __future__ import annotations

import csv
import io
import itertools
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from financial_model.planning import EconomicAssumptions

PathMode = Literal["fixed", "monte_carlo", "historical", "bootstrap", "stress"]
History = tuple[tuple[float, float, float, float], ...]
_FIELDS = ("equities", "fixed_income", "cash", "inflation")


def parse_history_csv(text: str) -> History:
    """Read complete joint annual observations with explicit consecutive years.

    Args:
        text: CSV contents using the header and rate units described above.

    Returns:
        Observations in chronological order, with all four rates kept together.

    Raises:
        ValueError: If columns, consecutive years, or rate values are invalid.
    """
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if reader.fieldnames != ["year", *_FIELDS]:
        msg = "History columns must be year,equities,fixed_income,cash,inflation."
        raise ValueError(msg)
    years: list[int] = []
    values: list[tuple[float, float, float, float]] = []
    try:
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                msg = "Every history row needs exactly five columns."
                raise ValueError(msg)
            years.append(int(row["year"]))
            stocks, bonds, cash, inflation = (float(row[field]) for field in _FIELDS)
            values.append((stocks, bonds, cash, inflation))
    except (TypeError, ValueError) as exc:
        msg = "History requires integer years and four numeric rates per row."
        raise ValueError(msg) from exc
    _validate_years(tuple(years))
    _history_array(tuple(values))
    return tuple(values)


def generate_paths(
    economy: EconomicAssumptions,
    years: tuple[int, ...],
    *,
    count: int = 10_000,
    seed: int = 42,
    mode: PathMode = "monte_carlo",
    history: History = (),
    stress: str = "early_loss",
    block_years: int = 5,
) -> NDArray[np.float64]:
    """Generate joint economic paths without consulting household decisions.

    Args:
        economy: Compound central rates, simple annual standard deviations, and
            correlations of log gross rates. Exploration bounds are unused.
        years: Nonempty, consecutive forecast calendar years.
        count: Number of Monte Carlo or bootstrap paths, at least one.
        seed: Local NumPy random seed. Matching settings share underlying shocks.
        mode: Fixed, Monte Carlo, overlapping historical windows, bootstrap, or
            a named stress. Fixed and stress return one path; historical returns
            every complete window, independently of count.
        history: Joint consecutive annual rates, required for historical modes.
        stress: ``early_loss`` sets stocks to -40%, -20%, 0% in the first three
            years. ``persistent_inflation`` sets a minimum of 8% inflation for
            ten years. Remaining rates retain their central outlooks. These are
            illustrative adverse scenarios with no assigned probabilities.
        block_years: Bootstrap block length. Blocks are drawn with replacement
            from complete consecutive windows, without wrapping history. The
            last block is truncated to the forecast horizon.

    Returns:
        Float array shaped (paths, years, 4), with the column order above.

    Raises:
        ValueError: If inputs are incomplete, invalid, or numerically unusable.
    """
    _validate_years(years)
    if mode not in ("fixed", "monte_carlo", "historical", "bootstrap", "stress"):
        msg = f"Unknown forecast path mode: {mode}."
        raise ValueError(msg)
    if type(count) is not int or count < 1:
        msg = "Path count must be a positive integer."
        raise ValueError(msg)
    match mode:
        case "historical" | "bootstrap":
            return _historical_paths(
                history, len(years), count, seed, mode, block_years
            )
    central = _central_rates(economy)
    if mode == "monte_carlo":
        return _monte_carlo(economy, central, len(years), count, seed)
    paths = np.broadcast_to(central, (1, len(years), 4)).copy()
    if mode == "stress":
        _apply_stress(paths, stress)
    return paths


def _validate_years(years: tuple[int, ...]) -> None:
    """Require consecutive annual observations rather than silently skipping gaps."""
    if not years or any(
        isinstance(year, bool) or not isinstance(year, int) for year in years
    ):
        msg = "Years must be a nonempty sequence of integers."
        raise ValueError(msg)
    if any(right != left + 1 for left, right in itertools.pairwise(years)):
        msg = "Years must be consecutive and in increasing order."
        raise ValueError(msg)


def _central_rates(economy: EconomicAssumptions) -> NDArray[np.float64]:
    """Keep unknown central rates distinct from explicit zero."""
    values = [getattr(economy, field).central for field in _FIELDS]
    if any(value is None for value in values):
        msg = "Equity, bond, cash, and inflation central outlooks are required."
        raise ValueError(msg)
    central = np.asarray(values, dtype=float)
    if not np.isfinite(central).all() or (central <= -1).any():
        msg = "Central rates must be finite and greater than -100%."
        raise ValueError(msg)
    return central


def _monte_carlo(
    economy: EconomicAssumptions,
    central: NDArray[np.float64],
    horizon: int,
    count: int,
    seed: int,
) -> NDArray[np.float64]:
    """Match geometric centers and simple-rate variance using lognormal gross rates."""
    if any(value is None for value in economy.volatility):
        msg = "Enter all four annual standard deviations; use zero if fixed."
        raise ValueError(msg)
    volatility = np.asarray(economy.volatility, dtype=float)
    if not np.isfinite(volatility).all() or (volatility < 0).any():
        msg = "Annual standard deviations must be finite and nonnegative."
        raise ValueError(msg)
    if not volatility.any():
        return np.broadcast_to(central, (count, horizon, 4)).copy()
    if economy.correlation is None:
        msg = "Monte Carlo requires an explicit correlation matrix of log gross rates."
        raise ValueError(msg)
    correlation = np.asarray(economy.correlation, dtype=float)
    eigenvalues, eigenvectors = np.linalg.eigh(correlation)
    # The symmetric square root also supports singular valid correlation matrices.
    transform = (eigenvectors * np.sqrt(eigenvalues.clip(min=0))) @ eigenvectors.T
    shocks = np.random.default_rng(seed).standard_normal((count, horizon, 4))
    # If a=1+geometric rate and q=exp(log variance), std²=a²*q*(q-1).
    ratio = volatility / (1 + central)
    log_variance = np.log1p(2 * ratio**2 / (np.hypot(1, 2 * ratio) + 1))
    log_rates = np.log1p(central) + (shocks @ transform) * np.sqrt(log_variance)
    with np.errstate(over="ignore", invalid="ignore"):
        paths = np.where(volatility == 0, central, np.expm1(log_rates))
    if not np.isfinite(paths).all() or (paths <= -1).any():
        msg = "Economic assumptions produce rates outside numerical precision."
        raise ValueError(msg)
    return paths


def _history_array(history: History) -> NDArray[np.float64]:
    """Validate historical observations without changing their relationships."""
    values = np.asarray(history, dtype=float)
    if values.ndim != 2 or values.shape[1] != 4 or not len(values):
        msg = "History needs at least one row with four joint annual rates."
        raise ValueError(msg)
    if not np.isfinite(values).all() or (values <= -1).any():
        msg = "Historical rates must be finite and greater than -100%."
        raise ValueError(msg)
    return values


def _historical_paths(
    history: History,
    horizon: int,
    count: int,
    seed: int,
    mode: Literal["historical", "bootstrap"],
    block_years: int,
) -> NDArray[np.float64]:
    """Preserve contemporaneous relationships and consecutive annual blocks."""
    values = _history_array(history)
    if mode == "historical":
        if len(values) < horizon:
            msg = "Historical replay needs at least as many observations as forecast years."
            raise ValueError(msg)
        starts = np.arange(len(values) - horizon + 1)
        return np.asarray(
            values[starts[:, None] + np.arange(horizon)], dtype=np.float64
        )
    if type(block_years) is not int or not 1 <= block_years <= len(values):
        msg = "Bootstrap block years must be positive and no longer than history."
        raise ValueError(msg)
    blocks = (horizon + block_years - 1) // block_years
    starts = np.random.default_rng(seed).integers(
        0, len(values) - block_years + 1, (count, blocks)
    )
    indices = (starts[:, :, None] + np.arange(block_years)).reshape(count, -1)
    return np.asarray(values[indices[:, :horizon]], dtype=np.float64)


def _apply_stress(paths: NDArray[np.float64], stress: str) -> None:
    """Apply explicit illustrative stresses without assigning probabilities."""
    if stress == "early_loss":
        years = min(paths.shape[1], 3)
        paths[0, :years, 0] = (-0.4, -0.2, 0.0)[:years]
    elif stress == "persistent_inflation":
        paths[0, :10, 3] = np.maximum(paths[0, :10, 3], 0.08)
    else:
        msg = f"Unknown stress: {stress}."
        raise ValueError(msg)
