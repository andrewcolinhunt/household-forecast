"""Independently tabulated contribution limits and required distributions."""

from __future__ import annotations

import pytest

from financial_model.forecast_rules import (
    contribution_limits,
    rmd_divisor,
    rmd_start_age,
)


@pytest.mark.parametrize(
    ("birth_year", "age"), [(1950, 72), (1951, 73), (1959, 73), (1960, 75)]
)
def test_rmd_start_age_cohort_boundaries(birth_year, age):
    assert rmd_start_age(birth_year) == age


@pytest.mark.parametrize(
    ("age", "divisor"),
    [(72, 27.4), (75, 24.6), (95, 8.9), (96, 8.4), (97, 7.8), (120, 2), (130, 2)],
)
def test_rmd_divisor_irs_table(age, divisor):
    assert rmd_divisor(age) == divisor


def test_rmd_divisor_rejects_age_outside_table():
    with pytest.raises(ValueError, match="starts at age 72"):
        rmd_divisor(71)


def test_contribution_limits_do_not_invent_catch_up_contributions():
    assert contribution_limits() == (24500, 72000)
