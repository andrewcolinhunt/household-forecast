"""Validated household inputs and portable calculation documents."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal, Self
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

Amount = Annotated[Decimal, Field(allow_inf_nan=False, max_digits=16, decimal_places=2)]
Money = Annotated[Amount, Field(ge=0)]
Price = Annotated[
    Decimal, Field(ge=0, allow_inf_nan=False, max_digits=16, decimal_places=6)
]
Rate = Annotated[Decimal, Field(ge=0, le=1, allow_inf_nan=False)]
Year = Literal[2025, 2026]
State = Literal["CA", "WA"]
ZERO = Decimal("0.00")
MODEL_REVISION = "2"
DEFERRAL_LIMIT = {2025: Decimal(23500), 2026: Decimal(24500)}
PLAN_LIMIT = {2025: Decimal(70000), 2026: Decimal(72000)}


def dollars(value: Decimal | float | int) -> Decimal:
    """Round a final dollar amount to cents, using decimal half-up rounding."""
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class ValueModel(BaseModel):
    """Shared validation rules for submitted values and saved documents."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ISOExercise(ValueModel):
    """A qualifying ISO exercise held through year end, without a disposition."""

    treatment: Literal["iso_exercise_and_hold"] = "iso_exercise_and_hold"
    shares: Annotated[int, Field(strict=True, ge=0)] = 0
    strike: Price = ZERO
    fair_market_value: Price = ZERO

    @property
    def cost(self) -> Decimal:
        """Cash paid to acquire the shares."""
        return dollars(self.shares * self.strike)

    @property
    def value(self) -> Decimal:
        """Acquired shares valued at the exercise-date fair market value."""
        return dollars(self.shares * self.fair_market_value)

    @property
    def spread(self) -> Decimal:
        """Positive exercise spread for the supported AMT adjustment."""
        return dollars(self.shares * max(self.fair_market_value - self.strike, ZERO))


class OptionAward(ValueModel):
    """Annual option vesting information, separate from taxable exercise events."""

    kind: Literal["ISO", "NSO"] = "ISO"
    vesting_shares: Annotated[int, Field(strict=True, ge=0)] = 0
    strike: Price = ZERO
    assumed_share_value: Price | None = None

    @property
    def exercise_spread(self) -> Decimal | None:
        """Estimate vested options' value above exercise cost, before taxes."""
        if self.assumed_share_value is None:
            return None
        return dollars(
            self.vesting_shares * max(self.assumed_share_value - self.strike, ZERO)
        )


class PersonInputs(ValueModel):
    """Annual compensation and saving assumptions for one adult."""

    name: Annotated[str, Field(min_length=1, max_length=80)]
    age: Annotated[int, Field(strict=True, ge=18, le=64)]
    # Omit missing metadata so existing snapshots retain their input digests.
    employer: Annotated[str, Field(min_length=1, max_length=80)] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    not_working: Annotated[bool, Field(strict=True)] = Field(
        default=False, exclude_if=lambda value: not value
    )
    cash_pay: Money = ZERO
    rsu_vesting: Money = ZERO
    employee_401k: Money = ZERO
    employer_retirement_rate: Rate = ZERO
    wa_cares_exempt: Annotated[bool, Field(strict=True)] = False
    options: ISOExercise = Field(default_factory=ISOExercise)
    # Omit absent awards so existing saved inputs keep their exact digest.
    option_award: OptionAward | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @field_validator("employer", mode="before")
    @classmethod
    def normalize_employer(cls, value: object) -> object:
        """Treat empty employer text as unknown, not as an employment status."""
        return (value.strip() or None) if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_employment_details(self) -> Self:
        """Do not label a current employer and no current work simultaneously."""
        if self.not_working and self.employer is not None:
            message = "Remove the current employer when marking someone not working."
            raise ValueError(message)
        return self

    @property
    def wages(self) -> Decimal:
        """Wages before pretax retirement contributions."""
        return self.cash_pay + self.rsu_vesting

    @property
    def employer_retirement(self) -> Decimal:
        """Estimated employer contribution, additional to employee saving."""
        return dollars(self.employer_retirement_rate * self.cash_pay)


class DependentInputs(ValueModel):
    """A qualifying child with no income who lives with the family all year."""

    age: Annotated[int, Field(strict=True, ge=0, le=17)]


class HouseholdPerson(ValueModel):
    """Shared name and age at the household defaults' reference year end."""

    name: Annotated[str, Field(min_length=1, max_length=80)]
    age: Annotated[int, Field(strict=True, ge=18, le=64)]


class Household(ValueModel):
    """Defaults copied into new scenarios; existing scenario inputs stay independent."""

    schema_version: Literal[1] = 1
    revision: Annotated[int, Field(strict=True, ge=0)] = 0
    tax_year: Year
    adults: tuple[HouseholdPerson, HouseholdPerson]
    dependents: Annotated[tuple[DependentInputs, ...], Field(max_length=10)] = ()
    filing_status: Literal["married_filing_jointly"] = "married_filing_jointly"

    def scenario_defaults(self) -> dict[str, object]:
        """Return only family facts; confirmation, income, and allocations stay separate."""
        return self.model_dump(mode="json", exclude={"schema_version", "revision"})


class LivingCosts(ValueModel):
    """Recurring monthly expenses and annual credit-eligible care expenses."""

    rent: Money = ZERO
    childcare: Money = ZERO
    other: Money = ZERO
    eligible_care_expense: Money = ZERO
    ca_renter_credit_eligible: Annotated[bool, Field(strict=True)] = False

    @property
    def annual_total(self) -> Decimal:
        """Total living expenses for a twelve-month scenario."""
        return 12 * (self.rent + self.childcare + self.other)

    @model_validator(mode="after")
    def validate_care_expense(self) -> Self:
        """Keep qualifying care expense within actual care spending."""
        if self.eligible_care_expense > self.childcare * 12:
            message = (
                "Eligible annual care expense cannot exceed annual childcare spending."
            )
            raise ValueError(message)
        if self.ca_renter_credit_eligible and not self.rent:
            message = "California renter credit eligibility requires rent expense."
            raise ValueError(message)
        return self


class Goal(ValueModel):
    """An editable purpose, independent of accounts and asset types."""

    id: UUID = Field(default_factory=uuid4)
    name: Annotated[str, Field(min_length=1, max_length=80)]
    revision: Annotated[int, Field(strict=True, ge=1)] = 1


class GoalAllocation(ValueModel):
    """One goal's fixed annual cash amount or remainder, and retained RSU share."""

    goal: Goal
    cash_amount: Money | None = ZERO
    rsu_rate: Rate = ZERO


class AllocationPlan(ValueModel):
    """Complete source destinations with exactly one cash remainder."""

    rows: Annotated[tuple[GoalAllocation, ...], Field(min_length=1, max_length=30)]
    retirement_goal_id: UUID
    option_goal_id: UUID

    @model_validator(mode="after")
    def validate_destinations(self) -> Self:
        """Reject duplicate goals, missing destinations, and ambiguous remainders."""
        ids = {row.goal.id for row in self.rows}
        if len(ids) != len(self.rows):
            msg = "Each goal can appear only once in an allocation plan."
            raise ValueError(msg)
        if sum(row.cash_amount is None for row in self.rows) != 1:
            msg = "Choose exactly one cash remainder goal."
            raise ValueError(msg)
        if self.retirement_goal_id not in ids or self.option_goal_id not in ids:
            msg = "Contribution and option destinations must be in the plan."
            raise ValueError(msg)
        if sum((row.rsu_rate for row in self.rows), ZERO) > 1:
            msg = "RSU allocations cannot exceed 100%."
            raise ValueError(msg)
        return self

    @property
    def spending_goal_id(self) -> UUID:
        """Identify the stored remainder row used for automatic spending cash."""
        return next(row.goal.id for row in self.rows if row.cash_amount is None)

    @property
    def fixed_cash(self) -> Decimal:
        """Annual cash requested before allocating the remainder."""
        return sum((row.cash_amount or ZERO for row in self.rows), ZERO)


def default_allocation_plan() -> AllocationPlan:
    """Offer editable starting goals shared across new scenario templates."""
    retirement = Goal(
        id=uuid5(NAMESPACE_URL, "financial-model/retirement"), name="Retirement"
    )
    available = Goal(
        id=uuid5(NAMESPACE_URL, "financial-model/available"), name="Available to spend"
    )
    return AllocationPlan(
        rows=(
            GoalAllocation(goal=retirement, rsu_rate=Decimal(1)),
            GoalAllocation(goal=available, cash_amount=None),
        ),
        retirement_goal_id=retirement.id,
        option_goal_id=retirement.id,
    )


class GoalAddition(ValueModel):
    """Planned annual additions to one goal, preserving their asset forms."""

    goal_id: UUID
    cash: Money
    retirement: Money
    rsus: Money
    option_cost: Money

    @property
    def total(self) -> Decimal:
        """All planned additions, before the household funding gap."""
        return self.cash + self.retirement + self.rsus + self.option_cost


class GoalCashFunding(ValueModel):
    """Cash requested and available for one goal in the funding order."""

    goal_id: UUID
    planned: Money
    funded: Money

    @property
    def shortfall(self) -> Decimal:
        """Cash requested but unavailable after higher-priority goals."""
        return self.planned - self.funded


class CashFunding(ValueModel):
    """Funded fixed cash goals and the signed balance left after funding them."""

    rows: tuple[GoalCashFunding, ...]
    remaining: Amount


class AllocationResult(ValueModel):
    """Planned goal amounts and the cash funding needed to realize them."""

    rows: tuple[GoalAddition, ...]
    funding_gap: Money

    @property
    def planned_total(self) -> Decimal:
        """Sum of planned additions; this is not fully funded when a gap exists."""
        return sum((row.total for row in self.rows), ZERO)

    @property
    def net_total(self) -> Decimal:
        """Net additions after subtracting missing cash funding."""
        return self.planned_total - self.funding_gap


class Scenario(ValueModel):
    """Complete inputs for the supported full-year, joint-filing household."""

    name: Annotated[str, Field(max_length=80)] = ""
    tax_year: Year
    state: State
    adults: tuple[PersonInputs, PersonInputs]
    dependents: Annotated[tuple[DependentInputs, ...], Field(max_length=10)] = ()
    living_costs: LivingCosts = Field(default_factory=LivingCosts)
    allocation_plan: AllocationPlan = Field(default_factory=default_allocation_plan)
    # The existing share-funded default keeps saved input digests unchanged.
    rsu_taxes_from_shares: Annotated[bool, Field(strict=True)] = Field(
        default=True, exclude_if=lambda value: value
    )
    notes: Annotated[str, Field(max_length=2000)] = ""
    filing_status: Literal["married_filing_jointly"] = "married_filing_jointly"
    deduction_method: Literal["standard"] = "standard"
    full_year_resident: Literal[True] = True
    assumptions_confirmed: Literal[True]

    @model_validator(mode="after")
    def validate_supported_facts(self) -> Self:
        """Reject assumptions outside the app's tested contribution/credit scope."""
        if (
            any(a.rsu_vesting for a in self.adults)
            and sum((r.rsu_rate for r in self.allocation_plan.rows), ZERO) != 1
        ):
            msg = "Allocate 100% of retained RSUs across your goals."
            raise ValueError(msg)
        if self.state != "CA" and self.living_costs.ca_renter_credit_eligible:
            message = (
                "California renter credit eligibility applies only to CA scenarios."
            )
            raise ValueError(message)
        for adult in self.adults:
            if adult.employee_401k > min(DEFERRAL_LIMIT[self.tax_year], adult.cash_pay):
                message = (
                    "Pretax 401(k) exceeds cash pay or the supported base annual limit."
                )
                raise ValueError(message)
            plan_total = adult.employee_401k + adult.employer_retirement
            if plan_total > min(PLAN_LIMIT[self.tax_year], adult.wages):
                message = (
                    "Employee and employer retirement exceed the annual plan limit."
                )
                raise ValueError(message)
            if self.state != "WA" and adult.wa_cares_exempt:
                message = "WA Cares exemption applies only to Washington scenarios."
                raise ValueError(message)
        if self.living_costs.eligible_care_expense and not any(
            child.age < 13 for child in self.dependents
        ):
            message = "Eligible care expense requires a qualifying child under age 13."
            raise ValueError(message)
        return self

    def digest(self) -> str:
        """Identify the complete submitted inputs for matching results to saves."""
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class TaxResult(ValueModel):
    """Income taxes include AMT; detail fields must not be added a second time."""

    federal_income_tax: Amount
    state_income_tax: Amount
    social_security: Money
    medicare: Money
    additional_medicare: Money
    ca_sdi: Money
    wa_paid_leave: Money
    wa_cares: Money
    federal_amt: Money
    state_amt: Money
    federal_taxable_income: Money
    engine_version: str
    core_version: str
    correction_version: str
    rules_status: Literal["verified_parameters", "projected_ca_parameters"]

    @property
    def employee_payroll_tax(self) -> Decimal:
        """Employee-only federal and state payroll taxes."""
        return (
            self.social_security
            + self.medicare
            + self.additional_medicare
            + self.ca_sdi
            + self.wa_paid_leave
            + self.wa_cares
        )

    @property
    def total(self) -> Decimal:
        """Total tax liability, including refundable income-tax balances."""
        return (
            self.federal_income_tax + self.state_income_tax + self.employee_payroll_tax
        )


class ScenarioResult(ValueModel):
    """A portable annual calculation snapshot with explicit asset categories."""

    input_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    tax_year: Year
    state: State
    calculated_at: AwareDatetime
    model_revision: str = MODEL_REVISION
    app_version: str
    taxes: TaxResult
    gross_cash: Money
    gross_rsus: Money
    employee_401k: Money
    employer_retirement: Money
    option_cost: Money
    option_value: Money
    living_cost: Money
    rsu_tax_funding: Money
    retained_rsus: Money
    cash_funded_tax: Amount
    cash_added: Amount
    allocations: AllocationResult

    @property
    def investments_added(self) -> Decimal:
        """Retirement and retained RSUs plus option purchases at cost, without gains."""
        return (
            self.employee_401k
            + self.employer_retirement
            + self.retained_rsus
            + self.option_cost
        )

    @property
    def net_saved_from_income(self) -> Decimal:
        """Net annual saving, excluding value realized from existing option rights."""
        return self.cash_added + self.investments_added

    @property
    def effective_tax_rate(self) -> Decimal | None:
        """Income and employee payroll taxes / cash and RSU wages; None at zero."""
        wages = self.gross_cash + self.gross_rsus
        return self.taxes.total / wages if wages else None

    @model_validator(mode="after")
    def validate_accounting(self) -> Self:
        """Require cash, shares, contributions, and goal allocations to reconcile."""
        rows = self.allocations.rows
        cash = (
            self.gross_cash
            - self.cash_funded_tax
            - self.employee_401k
            - self.option_cost
            - self.living_cost
        )
        if (
            cash != self.cash_added
            or self.retained_rsus + self.rsu_tax_funding != self.gross_rsus
            or self.cash_funded_tax + self.rsu_tax_funding != self.taxes.total
            or sum((r.cash for r in rows), ZERO) - self.allocations.funding_gap != cash
            or sum((r.retirement for r in rows), ZERO)
            != self.employee_401k + self.employer_retirement
            or sum((r.rsus for r in rows), ZERO) != self.retained_rsus
            or sum((r.option_cost for r in rows), ZERO) != self.option_cost
            or self.allocations.net_total != self.net_saved_from_income
        ):
            msg = "Calculation snapshot does not reconcile. Recalculate the scenario."
            raise ValueError(msg)
        return self


class SavedScenario(ValueModel):
    """Versioned document used by SQLite and JSON backups."""

    schema_version: Literal[2] = 2
    id: UUID = Field(default_factory=uuid4)
    revision: Annotated[int, Field(strict=True, ge=1)] = 1
    updated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    inputs: Scenario
    result: ScenarioResult

    @model_validator(mode="after")
    def validate_matching_result(self) -> Self:
        """Prevent a result for other inputs from being persisted as this scenario."""
        if (
            self.result.input_digest != self.inputs.digest()
            or self.result.tax_year != self.inputs.tax_year
            or self.result.state != self.inputs.state
            or (not self.inputs.rsu_taxes_from_shares and self.result.rsu_tax_funding)
        ):
            message = (
                "Saved result does not match its scenario inputs. Recalculate first."
            )
            raise ValueError(message)
        from financial_model.allocations import allocate_sources

        expected = allocate_sources(
            self.inputs.allocation_plan,
            cash=self.result.cash_added,
            retirement=self.result.employee_401k + self.result.employer_retirement,
            rsus=self.result.retained_rsus,
            option_cost=self.result.option_cost,
        )
        if expected != self.result.allocations:
            msg = "Saved allocations do not match the scenario plan."
            raise ValueError(msg)
        return self
