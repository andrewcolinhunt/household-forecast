"""Frozen inputs and portable results for household scenario forecasts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Final, Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from financial_model.planning import PlanningScenario
from financial_model.schemas import Money, Rate, ValueModel

FORECAST_VERSION: Final = "1"
Finite = Annotated[float, Field(allow_inf_nan=False)]
Probability = Annotated[Finite, Field(ge=0, le=1)]
AnnualRate = Annotated[Finite, Field(gt=-1)]


class ForecastAccount(ValueModel):
    """Projection details without changing the simplified Wealth inventory."""

    account_id: UUID
    owner: Literal[1, 2] | None = None
    cost_basis: Money | None = None


class ForecastIncome(ValueModel):
    """Annual retirement income in the Plan's reference-year purchasing power."""

    owner: Literal[1, 2]
    social_security: Money | None = None
    claiming_age: Annotated[int, Field(ge=62, le=70)] = 67
    pension: Money | None = None
    pension_start_age: Annotated[int, Field(ge=18, le=100)] = 65
    pension_survivor_fraction: Rate | None = None
    health_before_65: Money | None = None
    health_from_65: Money | None = None
    hsa_eligible_before_65: Money | None = None
    hsa_eligible_from_65: Money | None = None

    @model_validator(mode="after")
    def validate_health(self) -> Self:
        """Keep explicitly eligible HSA expenses within the health budget."""
        for health, eligible in (
            (self.health_before_65, self.hsa_eligible_before_65),
            (self.health_from_65, self.hsa_eligible_from_65),
        ):
            if eligible is not None and health is not None and eligible > health:
                msg = (
                    "HSA-eligible expenses cannot exceed the additional health budget."
                )
                raise ValueError(msg)
        return self


class ForecastDebt(ValueModel):
    """Nominal debt terms; payment includes principal and interest."""

    debt_id: UUID
    annual_payment: Money | None = None
    interest_rate: Rate | None = None


class ForecastDetails(ValueModel):
    """Explicit account, benefit, and tax assumptions needed beyond saved Plans."""

    accounts: tuple[ForecastAccount, ...] = ()
    incomes: tuple[ForecastIncome, ForecastIncome] = (
        ForecastIncome(owner=1),
        ForecastIncome(owner=2),
    )
    debts: tuple[ForecastDebt, ...] = ()
    stock_dividend_yield: Rate | None = None
    bond_interest_yield: Rate | None = None
    first_year_events: Literal["remaining", "already_in_snapshot"] | None = None
    tax_policy: Literal["2026_rules_constant_purchasing_power"] = (
        "2026_rules_constant_purchasing_power"
    )
    assumptions_confirmed: bool = False

    @model_validator(mode="after")
    def validate_identities(self) -> Self:
        """Reject repeated metadata and mismatched household slots."""
        if tuple(i.owner for i in self.incomes) != (1, 2):
            msg = "Benefit inputs must use household owners 1 and 2, in order."
            raise ValueError(msg)
        for rows, field in ((self.accounts, "account_id"), (self.debts, "debt_id")):
            ids = [getattr(row, field) for row in rows]
            if len(ids) != len(set(ids)):
                msg = "Forecast details cannot contain duplicate accounts or debts."
                raise ValueError(msg)
        return self


class ForecastSettings(ValueModel):
    """Saved simulation choices; history is immutable jointly observed annual data."""

    mode: Literal["fixed", "monte_carlo", "historical", "bootstrap", "stress"] = (
        "monte_carlo"
    )
    count: Annotated[int, Field(strict=True, ge=1, le=10000)] = 10000
    seed: Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)] = 42
    history: tuple[tuple[AnnualRate, AnnualRate, AnnualRate, AnnualRate], ...] = ()
    history_source: str = ""
    history_start_year: Annotated[int, Field(strict=True, ge=1800, le=9999)] | None = (
        None
    )
    stress: Literal["early_loss", "persistent_inflation"] = "early_loss"
    block_years: Annotated[int, Field(strict=True, ge=1, le=30)] = 5

    @model_validator(mode="after")
    def validate_history(self) -> Self:
        """Require provenance for a run that substitutes observed economic rates."""
        if self.mode in {"historical", "bootstrap"} and (
            not self.history or not self.history_source
        ):
            msg = "Historical modes need annual observations and their source."
            raise ValueError(msg)
        return self


class ForecastRequest(ValueModel):
    """One to four explicit alternatives sharing their opening financial position."""

    name: Annotated[str, Field(min_length=1, max_length=120)] = "Scenario forecast"
    plans: Annotated[tuple[PlanningScenario, ...], Field(min_length=1, max_length=4)]
    details: ForecastDetails
    settings: ForecastSettings = Field(default_factory=ForecastSettings)

    @model_validator(mode="after")
    def validate_comparison(self) -> Self:
        """Require a common dated balance sheet and household identity mapping."""
        first = self.plans[0]
        if first.wealth is None:
            msg = "Select an opening Wealth snapshot."
            raise ValueError(msg)
        starting = first.wealth.snapshot.model_dump(exclude={"name", "notes"})
        for plan in self.plans:
            if (
                plan.wealth is None
                or plan.wealth.snapshot.model_dump(exclude={"name", "notes"})
                != starting
            ):
                msg = "Compared scenarios must use the same dated accounts and debts."
                raise ValueError(msg)
            if plan.household.model_dump(
                exclude={"residence"}
            ) != first.household.model_dump(exclude={"residence"}):
                msg = "Compare choices within one household and reference year."
                raise ValueError(msg)
        return self


class ForecastPoint(ValueModel):
    """Year-end outcome percentiles in the Plan's reference-year dollars."""

    year: int
    lower: Finite
    median: Finite
    upper: Finite
    accessible_median: Finite
    cash_median: Finite
    debt_median: Finite
    shortfall_probability: Probability
    reserve_breach_probability: Probability
    median_difference: Finite | None
    lower_difference: Finite | None
    upper_difference: Finite | None

    @model_validator(mode="after")
    def validate_bands(self) -> Self:
        """Require ordered percentiles for balances and paired differences."""
        differences = (
            self.lower_difference,
            self.median_difference,
            self.upper_difference,
        )
        known = tuple(value for value in differences if value is not None)
        if not self.lower <= self.median <= self.upper or (
            known and (len(known) != 3 or not known[0] <= known[1] <= known[2])
        ):
            msg = "Forecast percentile bands must be in increasing order."
            raise ValueError(msg)
        return self


class ForecastSummary(ValueModel):
    """A comparison curve, lifetime funding statistics, and reproducible path details."""

    name: str
    money_year: int
    retirement_years: tuple[int, int]
    points: Annotated[tuple[ForecastPoint, ...], Field(min_length=1)]
    funded_fraction: Probability
    first_shortfall_year: int | None
    mean_unfunded_total: Annotated[Finite, Field(ge=0)]
    mean_unfunded_years: Annotated[Finite, Field(ge=0)]
    paths: Annotated[int, Field(strict=True, ge=1)]
    example: tuple[dict[str, Finite | int], ...]

    @model_validator(mode="after")
    def validate_years(self) -> Self:
        """Keep each saved curve chronological and consecutive."""
        years = tuple(point.year for point in self.points)
        if years != tuple(range(years[0], years[0] + len(years))):
            msg = "Forecast points must use consecutive calendar years."
            raise ValueError(msg)
        return self


class SavedForecast(ValueModel):
    """An immutable run with inputs sufficient to regenerate every simulated path."""

    document_type: Literal["forecast"] = "forecast"
    schema_version: Literal[1] = 1
    model_version: Literal["1"] = FORECAST_VERSION
    id: UUID = Field(default_factory=uuid4)
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    numpy_version: str
    tax_engine_version: str
    request: ForecastRequest
    summaries: tuple[ForecastSummary, ...]

    @model_validator(mode="after")
    def validate_summaries(self) -> Self:
        """Require one result for every frozen scenario in the same order."""
        if len(self.summaries) != len(self.request.plans):
            msg = "A saved forecast needs one summary per selected scenario."
            raise ValueError(msg)
        for summary, plan in zip(self.summaries, self.request.plans, strict=True):
            if (
                summary.name != plan.name
                or summary.money_year != plan.household.reference_year
            ):
                msg = (
                    "Forecast summaries must match their saved scenario and money year."
                )
                raise ValueError(msg)
        return self
