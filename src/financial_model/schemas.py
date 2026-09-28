"""Validated household inputs and portable calculation documents."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

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
MODEL_REVISION = "1"
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
    savings_rate: Rate = Decimal("0.10")
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
    def employee_savings(self) -> Decimal:
        """Cash-funded saving, including the employee 401(k) contribution."""
        return max(dollars(self.savings_rate * self.wages), self.employee_401k)

    @property
    def employer_retirement(self) -> Decimal:
        """Estimated employer contribution, additional to employee saving."""
        return dollars(self.employer_retirement_rate * self.cash_pay)


class DependentInputs(ValueModel):
    """A qualifying child with no income who lives with the family all year."""

    age: Annotated[int, Field(strict=True, ge=0, le=17)]


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


class Scenario(ValueModel):
    """Complete inputs for the supported full-year, joint-filing household."""

    name: Annotated[str, Field(max_length=80)] = ""
    tax_year: Year
    state: State
    adults: tuple[PersonInputs, PersonInputs]
    dependents: Annotated[tuple[DependentInputs, ...], Field(max_length=10)] = ()
    living_costs: LivingCosts = Field(default_factory=LivingCosts)
    notes: Annotated[str, Field(max_length=2000)] = ""
    filing_status: Literal["married_filing_jointly"] = "married_filing_jointly"
    deduction_method: Literal["standard"] = "standard"
    full_year_resident: Literal[True] = True
    assumptions_confirmed: Literal[True]

    @model_validator(mode="after")
    def validate_supported_facts(self) -> Self:
        """Reject assumptions outside the app's tested contribution/credit scope."""
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
    extra_savings: Money
    employer_retirement: Money
    option_cost: Money
    option_value: Money
    living_cost: Money
    rsu_tax_funding: Money
    retained_rsus: Money
    cash_funded_tax: Amount
    annual_remaining_cash: Amount
    monthly_remaining_cash: Amount
    total_savings_and_assets: Amount

    @property
    def cash_added(self) -> Decimal:
        """Net cash retained, including planned cash saving and any cash deficit."""
        return self.extra_savings + self.annual_remaining_cash

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
        return self.total_savings_and_assets - self.option_value + self.option_cost

    @property
    def effective_tax_rate(self) -> Decimal | None:
        """Income and employee payroll taxes / cash and RSU wages; None at zero."""
        wages = self.gross_cash + self.gross_rsus
        return self.taxes.total / wages if wages else None

    @model_validator(mode="after")
    def validate_accounting(self) -> Self:
        """Require the stored result to satisfy both cash and asset identities."""
        cash = (
            self.gross_cash
            - self.cash_funded_tax
            - self.employee_401k
            - self.extra_savings
            - self.option_cost
            - self.living_cost
        )
        assets = (
            cash
            + self.retained_rsus
            + self.employee_401k
            + self.extra_savings
            + self.employer_retirement
            + self.option_value
        )
        if (
            cash != self.annual_remaining_cash
            or dollars(cash / 12) != self.monthly_remaining_cash
            or assets != self.total_savings_and_assets
            or self.retained_rsus + self.rsu_tax_funding != self.gross_rsus
            or self.cash_funded_tax + self.rsu_tax_funding != self.taxes.total
        ):
            message = (
                "Calculation snapshot does not reconcile. Recalculate the scenario."
            )
            raise ValueError(message)
        return self


class SavedScenario(ValueModel):
    """Versioned document used by PostgreSQL and JSON backups."""

    schema_version: Literal[1] = 1
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
        ):
            message = (
                "Saved result does not match its scenario inputs. Recalculate first."
            )
            raise ValueError(message)
        return self
