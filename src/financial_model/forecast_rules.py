"""Contribution and withdrawal rules under the forecast's fixed 2026 policy."""

from __future__ import annotations

from financial_model.schemas import DEFERRAL_LIMIT, PLAN_LIMIT

# IRS Publication 590-B, Appendix B, Table III (effective since 2022).
# https://www.irs.gov/publications/p590b#en_US_2025_publink100090310
_UNIFORM_LIFETIME = (
    27.4,
    26.5,
    25.5,
    24.6,
    23.7,
    22.9,
    22.0,
    21.1,
    20.2,
    19.4,
    18.5,
    17.7,
    16.8,
    16.0,
    15.2,
    14.4,
    13.7,
    12.9,
    12.2,
    11.5,
    10.8,
    10.1,
    9.5,
    8.9,
    8.4,
    7.8,
    7.3,
    6.8,
    6.4,
    6.0,
    5.6,
    5.2,
    4.9,
    4.6,
    4.3,
    4.1,
    3.9,
    3.7,
    3.5,
    3.4,
    3.3,
    3.1,
    3.0,
    2.9,
    2.8,
    2.7,
    2.5,
    2.3,
    2.0,
)


def rmd_start_age(birth_year: int) -> int:
    """Return the applicable RMD threshold for projections from 2026 onward.

    Earlier cohorts already have an RMD obligation when this model starts;
    their historical first distribution date is outside the forecast.
    """
    if birth_year >= 1960:
        return 75
    return 73 if birth_year >= 1951 else 72


def rmd_divisor(age: int) -> float:
    """Return the Uniform Lifetime denominator for age 72 or older.

    This table excludes a sole-beneficiary spouse more than ten years younger.
    The caller must validate that condition before using it.
    """
    if age < 72:
        msg = "The Uniform Lifetime table starts at age 72."
        raise ValueError(msg)
    return _UNIFORM_LIFETIME[min(age, 120) - 72]


def contribution_limits() -> tuple[float, float]:
    """Return regular employee and total plan caps in 2026 dollars.

    Catch-up contributions are excluded at every age: the current inputs do
    not distinguish Roth catch-ups or the prior earnings that determine them.
    """
    return float(DEFERRAL_LIMIT[2026]), float(PLAN_LIMIT[2026])
