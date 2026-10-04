"""Reusable planning inputs and reviewed combinations, without financial projections."""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Self, TypeVar
from uuid import UUID, uuid4

import numpy as np
from pydantic import AwareDatetime, Field, TypeAdapter, model_validator

from financial_model.schemas import (
    AllocationPlan,
    Household,
    ISOExercise,
    Money,
    OptionAward,
    Rate,
    SavedScenario,
    ValueModel,
)
from financial_model.wealth import AssetMix, Label, Notes, SavedWealthSnapshot

PlanningYear = Annotated[int, Field(strict=True, ge=1900, le=9999)]
Age = Annotated[int, Field(strict=True, ge=18, le=130)]
Growth = Annotated[Decimal, Field(gt=-1, allow_inf_nan=False)]
Nonnegative = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
Slot = Literal[1, 2]
Kind = Literal["work", "spending", "retirement", "investments", "economy", "plan"]
PROFILE_KINDS: tuple[
    Literal["work", "spending", "retirement", "investments", "economy"], ...
] = (
    "work",
    "spending",
    "retirement",
    "investments",
    "economy",
)
T = TypeVar("T", int, Decimal)


class Exploration[T: (int, Decimal)](ValueModel):
    """A central assumption and optional paired alternatives, with no probability law."""

    central: T | None = None
    lower: T | None = None
    upper: T | None = None

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        """Require complete bounds around a known central value."""
        if self.lower is not None or self.upper is not None:
            if self.central is None or self.lower is None or self.upper is None:
                msg = "Enter a central value and both exploration bounds."
                raise ValueError(msg)
            if not self.lower <= self.central <= self.upper:
                msg = "Require lower ≤ central ≤ upper."
                raise ValueError(msg)
        return self


class TimeAnchor(ValueModel):
    """An annual boundary tied to a year, person's age or retirement landmark."""

    kind: Literal["year", "adult_1", "adult_2", "both", "age", "survivor"] = "year"
    year: PlanningYear | None = None
    offset: Annotated[int, Field(strict=True, ge=-100, le=100)] = 0
    person: str | None = None
    age: Annotated[int, Field(strict=True, ge=0, le=130)] | None = None

    @model_validator(mode="after")
    def validate_kind(self) -> Self:
        """Reject ambiguous fixed and relative dates."""
        if self.kind in {"year", "age"} and self.offset != 0:
            msg = "Calendar years and ages cannot have a retirement offset."
            raise ValueError(msg)
        if self.kind != "year" and self.year is not None:
            msg = "Relative anchors cannot also specify a calendar year."
            raise ValueError(msg)
        if self.kind != "age" and (self.person is not None or self.age is not None):
            msg = "Only age anchors can specify a person and age."
            raise ValueError(msg)
        return self

    def resolve(
        self,
        retirements: tuple[int | None, int | None],
        household: PlanningHousehold | None = None,
        end_years: tuple[int | None, int | None] | None = None,
    ) -> int | None:
        """Resolve a boundary, leaving missing dates or references unknown."""
        if self.kind == "year":
            return self.year
        if self.kind == "age":
            return self._age_year(household)
        if self.kind in {"both", "survivor"}:
            first, second = (
                (end_years or (None, None)) if self.kind == "survivor" else retirements
            )
            year = (
                (
                    min(first, second) + 1
                    if self.kind == "survivor"
                    else max(first, second)
                )
                if first is not None and second is not None
                else None
            )
        else:
            year = retirements[0 if self.kind == "adult_1" else 1]
        return year + self.offset if year is not None else None

    def _age_year(self, household: PlanningHousehold | None) -> int | None:
        """Resolve ages from household facts without relying on display names."""
        if household is None or self.age is None or self.person is None:
            return None
        if self.person in {"adult_1", "adult_2"}:
            adult = household.adults[0 if self.person == "adult_1" else 1]
            if adult.age is None or household.reference_year is None:
                return None
            return household.reference_year - adult.age + self.age
        child = next((c for c in household.children if str(c.id) == self.person), None)
        return (
            child.birth_year + self.age
            if child and child.birth_year is not None
            else None
        )


class Source(ValueModel):
    """Optional evidence for an assumption, including dated publication details."""

    description: Notes = ""
    url: Annotated[str, Field(max_length=2000)] = ""
    as_of: date | None = None
    version: Annotated[str, Field(max_length=120)] = ""


class AnnualSource(ValueModel):
    """Provenance for an explicit copy of annual inputs, never calculated outputs."""

    id: UUID
    revision: Annotated[int, Field(strict=True, ge=1)]
    tax_year: PlanningYear
    input_digest: str


class Assumptions(ValueModel):
    """Common identity-free contents of a reusable input profile."""

    name: Label
    notes: Notes = ""
    source: Source = Field(default_factory=Source)
    annual_source: AnnualSource | None = None


class MonetaryAssumptions(Assumptions):
    """Money expressed in a specified year's USD purchasing power."""

    currency: Literal["USD"] = "USD"
    money_year: PlanningYear | None = None


class WorkPhase(ValueModel):
    """A nonoverlapping employment phase; end year is exclusive."""

    start: PlanningYear | None = None
    end: PlanningYear | None = None
    status: Literal["working", "not_working", "unknown"] = "unknown"
    employer: Label | None = None
    cash_pay: Money | None = None
    real_growth: Exploration[Growth] = Field(default_factory=Exploration[Growth])
    employee_401k: Money | None = None
    contribution_basis: Literal["real", "nominal", "pay_fraction"] = "real"
    contribution_rate: Rate | None = None
    employer_retirement_rate: Rate | None = None
    notes: Notes = ""

    @model_validator(mode="after")
    def validate_phase(self) -> Self:
        """Keep entered intervals and contribution conventions consistent."""
        if self.start is not None and self.end is not None and self.end <= self.start:
            msg = "A phase ends after its start; end year is exclusive."
            raise ValueError(msg)
        if self.status == "not_working" and (
            self.employer
            or self.cash_pay
            or self.employee_401k
            or self.contribution_rate
            or self.employer_retirement_rate
        ):
            msg = "A not-working phase cannot contain employment pay or contributions."
            raise ValueError(msg)
        if self.contribution_basis == "pay_fraction" and self.employee_401k is not None:
            msg = "Use a percentage, not a dollar contribution, for pay fraction."
            raise ValueError(msg)
        if (
            self.contribution_basis != "pay_fraction"
            and self.contribution_rate is not None
        ):
            msg = "A dollar contribution cannot also specify a pay fraction."
            raise ValueError(msg)
        return self


class Vesting(ValueModel):
    """Expected annual vesting, distinct from cash pay and already-owned shares."""

    start: PlanningYear | None = None
    end: PlanningYear | None = None
    amount: Nonnegative | None = None
    basis: Literal["nominal", "real", "shares"] = "nominal"
    notes: Notes = ""

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        """Reject reversed entered schedules."""
        if self.start is not None and self.end is not None and self.end <= self.start:
            msg = "Vesting ends after its start; end year is exclusive."
            raise ValueError(msg)
        if self.amount is not None and self.basis != "shares":
            TypeAdapter(Money).validate_python(self.amount)
        return self


class OptionEvent(ValueModel):
    """One dated award or exercise, without inventing recurring future grants."""

    year: PlanningYear | None = None
    award: OptionAward | None = None
    exercise: ISOExercise | None = None
    notes: Notes = ""

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        """Require an award or exercise for an entered event."""
        if self.award is None and self.exercise is None:
            msg = "An option event needs an award or an exercise."
            raise ValueError(msg)
        return self


class AdultWork(ValueModel):
    """One adult's compensation schedule, reusable with different retirement dates."""

    slot: Slot
    name: Label
    phases: tuple[WorkPhase, ...] = ()
    vesting: tuple[Vesting, ...] = ()
    options: tuple[OptionEvent, ...] = ()

    @model_validator(mode="after")
    def validate_timeline(self) -> Self:
        """Reject overlapping employment phases."""
        phases = sorted(
            (p for p in self.phases if p.start is not None), key=lambda p: p.start or 0
        )
        for previous, following in itertools.pairwise(phases):
            end = previous.end
            if following.start is not None and (end is None or end > following.start):
                msg = "Employment phases must not overlap; close the earlier phase."
                raise ValueError(msg)
        return self


class WorkAssumptions(MonetaryAssumptions):
    """Two adults' future employment choices and compensation assumptions."""

    kind: Literal["work"] = "work"
    adults: tuple[AdultWork, AdultWork] = (
        AdultWork(slot=1, name="Adult 1"),
        AdultWork(slot=2, name="Adult 2"),
    )
    rsu_taxes_from_shares: bool | None = None

    @model_validator(mode="after")
    def validate_slots(self) -> Self:
        """Keep two stable, ordered adult slots."""
        if tuple(a.slot for a in self.adults) != (1, 2):
            msg = "Enter adult slots 1 and 2, in that order."
            raise ValueError(msg)
        return self


class ExpenseLevel(ValueModel):
    """An annual amount from an inclusive boundary, in base-year dollars."""

    when: TimeAnchor = Field(default_factory=TimeAnchor)
    amount: Exploration[Money] = Field(default_factory=Exploration[Money])
    label: Notes = ""


class ExpenseSchedule(ValueModel):
    """One independently dated expense, without household-wide overrides."""

    id: UUID = Field(default_factory=uuid4)
    label: Label
    category: Literal["ordinary", "children", "healthcare", "care", "other"] = (
        "ordinary"
    )
    frequency: Literal["recurring", "once"] = "recurring"
    levels: tuple[ExpenseLevel, ...] = Field(default_factory=lambda: (ExpenseLevel(),))
    end: TimeAnchor | None = None
    real_growth: Growth = Decimal(0)
    notes: Notes = ""

    @model_validator(mode="after")
    def validate_schedule(self) -> Self:
        """Reject ambiguous one-time entries and known reversed boundaries."""
        if self.frequency == "once" and (self.end is not None or len(self.levels) != 1):
            msg = "One-time spending has one amount and date, without an end."
            raise ValueError(msg)
        anchors = [level.when for level in self.levels]
        if self.end:
            anchors.append(self.end)
        for first, second in itertools.pairwise(anchors):
            if first.kind != second.kind or first.person != second.person:
                continue
            field = (
                "year"
                if first.kind == "year"
                else "age"
                if first.kind == "age"
                else "offset"
            )
            a, b = getattr(first, field), getattr(second, field)
            if a is not None and b is not None and b <= a:
                msg = f"{self.label}: expense boundaries must increase in time."
                raise ValueError(msg)
        return self


class SpendingAssumptions(MonetaryAssumptions):
    """Named expenses whose active amounts add to planned consumption."""

    kind: Literal["spending"] = "spending"
    expenses: tuple[ExpenseSchedule, ...] = Field(
        default_factory=lambda: (ExpenseSchedule(label="Ordinary living"),)
    )

    @model_validator(mode="after")
    def validate_identities(self) -> Self:
        """Keep expense references stable and unique inside a profile."""
        if len({item.id for item in self.expenses}) != len(self.expenses):
            msg = "Expense identities must be unique."
            raise ValueError(msg)
        return self


class AllocationCheckpoint(ValueModel):
    """A target at an entered time; missing mix remains explicitly unknown."""

    when: TimeAnchor = Field(default_factory=TimeAnchor)
    mix: AssetMix | None = None


class InvestmentAssumptions(MonetaryAssumptions):
    """Saving by purpose and a separately specified investment policy."""

    kind: Literal["investments"] = "investments"
    saving: AllocationPlan | None = None
    policy: Literal["constant", "gradual", "undecided"] = "undecided"
    checkpoints: tuple[AllocationCheckpoint, ...] = ()
    interpolation: Literal[
        "linear_then_hold", "log_then_hold", "exponential_then_hold"
    ] = "linear_then_hold"
    rebalancing: Literal["annual", "flows_only", "undecided"] = "undecided"
    reserved_cash: Money | None = None

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        """Reject contradictory policies while allowing incomplete collection."""
        if self.policy == "constant" and len(self.checkpoints) > 1:
            msg = "A constant allocation has one checkpoint."
            raise ValueError(msg)
        if self.policy == "undecided" and self.checkpoints:
            msg = "Choose constant or gradual allocation for checkpoints."
            raise ValueError(msg)
        years = [c.when.year for c in self.checkpoints]
        if all(y is not None for y in years) and any(
            a is not None and b is not None and b <= a
            for a, b in itertools.pairwise(years)
        ):
            msg = "Allocation checkpoints must have increasing dates."
            raise ValueError(msg)
        return self


# Rounded annual volatility assumptions; see docs/concepts/forecasting.md for sources.
DEFAULT_ECONOMIC_VOLATILITY = (
    Decimal("0.18"),
    Decimal("0.06"),
    Decimal("0.01"),
    Decimal("0.02"),
)
# JPM 2026 USD relationships adapted to log gross rates at the documented outlook.
# Order: equities, fixed income, cash, inflation. These remain editable assumptions.
DEFAULT_ECONOMIC_CORRELATION = (
    (Decimal(1), Decimal("0.302"), Decimal("0.010"), Decimal("-0.010")),
    (Decimal("0.302"), Decimal(1), Decimal("0.100"), Decimal("-0.240")),
    (Decimal("0.010"), Decimal("0.100"), Decimal(1), Decimal("-0.030")),
    (Decimal("-0.010"), Decimal("-0.240"), Decimal("-0.030"), Decimal(1)),
)


class EconomicAssumptions(Assumptions):
    """Nominal compound outlooks with editable annual simulation defaults."""

    kind: Literal["economy"] = "economy"
    inflation: Exploration[Growth] = Field(default_factory=Exploration[Growth])
    equities: Exploration[Growth] = Field(default_factory=Exploration[Growth])
    fixed_income: Exploration[Growth] = Field(default_factory=Exploration[Growth])
    cash: Exploration[Growth] = Field(default_factory=Exploration[Growth])
    stock_dividend_yield: Rate | None = None
    bond_interest_yield: Rate | None = None
    return_basis: Literal["nominal_compound_after_fees_before_tax"] = (
        "nominal_compound_after_fees_before_tax"
    )
    # Order is equities, fixed income, cash, inflation throughout.
    volatility: tuple[
        Nonnegative | None, Nonnegative | None, Nonnegative | None, Nonnegative | None
    ] = DEFAULT_ECONOMIC_VOLATILITY
    correlation: (
        tuple[
            tuple[Decimal, Decimal, Decimal, Decimal],
            tuple[Decimal, Decimal, Decimal, Decimal],
            tuple[Decimal, Decimal, Decimal, Decimal],
            tuple[Decimal, Decimal, Decimal, Decimal],
        ]
        | None
    ) = DEFAULT_ECONOMIC_CORRELATION

    @model_validator(mode="after")
    def validate_correlation(self) -> Self:
        """Validate a complete correlation matrix without filling unknown relationships."""
        if self.correlation is not None:
            matrix = np.array(self.correlation, dtype=float)
            if not np.isfinite(matrix).all() or (np.abs(matrix) > 1).any():
                msg = "Correlations must be finite and between -1 and 1."
                raise ValueError(msg)
            if not np.array_equal(matrix, matrix.T) or not np.array_equal(
                np.diag(matrix), np.ones(4)
            ):
                msg = "Correlation matrix must be symmetric with diagonal 1."
                raise ValueError(msg)
            if np.linalg.eigvalsh(matrix).min() < -1e-10:
                msg = "Correlation matrix must be positive semidefinite."
                raise ValueError(msg)
        return self


class AdultRetirement(ValueModel):
    """One adult's retirement timing, benefits, and lifespan."""

    slot: Slot
    name: Label
    retirement: Exploration[PlanningYear] = Field(
        default_factory=Exploration[PlanningYear]
    )
    age: Exploration[Age] = Field(default_factory=Exploration[Age])
    table_classification: Annotated[str, Field(max_length=120)] = ""
    social_security: Money | None = None
    claiming_age: Annotated[int, Field(ge=62, le=70)] = 67
    pension: Money | None = None
    pension_start_age: Annotated[int, Field(ge=18, le=100)] = 65
    pension_survivor_fraction: Rate | None = None


class RetirementAssumptions(MonetaryAssumptions):
    """Retirement timing, benefits, and lifespans in one scenario."""

    kind: Literal["retirement"] = "retirement"
    mode: Literal["ages", "sample"] = "ages"
    adults: tuple[AdultRetirement, AdultRetirement] = (
        AdultRetirement(slot=1, name="Adult 1"),
        AdultRetirement(slot=2, name="Adult 2"),
    )
    table_basis: Literal["period", "cohort", "unknown"] = "unknown"

    @model_validator(mode="after")
    def validate_mode(self) -> Self:
        """Keep adult identities and horizon modes unambiguous."""
        if tuple(a.slot for a in self.adults) != (1, 2):
            msg = "Enter adult slots 1 and 2, in that order."
            raise ValueError(msg)
        if self.mode == "sample" and any(
            a.age.central is not None for a in self.adults
        ):
            msg = "Sampling intent cannot also prescribe fixed planning ages."
            raise ValueError(msg)
        return self


class PlanningAdult(ValueModel):
    """Copied family facts at the household's explicit reference-year end."""

    name: Label
    age: Age | None = None
    wa_cares_exempt: bool | None = None


class PlanningChild(ValueModel):
    """An existing or expected child; costs are entered separately in Spending."""

    id: UUID = Field(default_factory=uuid4)
    name: Label
    birth_year: PlanningYear | None = None


class Residence(ValueModel):
    """An explicit jurisdiction from an effective calendar year."""

    year: PlanningYear | None = None
    location: Label | None = None


class PlanningHousehold(ValueModel):
    """Plan-local demographics, kept independent from shared annual defaults."""

    reference_year: PlanningYear | None = None
    adults: tuple[PlanningAdult, PlanningAdult] = (
        PlanningAdult(name="Adult 1"),
        PlanningAdult(name="Adult 2"),
    )
    children: tuple[PlanningChild, ...] = ()
    residence: tuple[Residence, ...] = ()
    filing_status: Literal["married_filing_jointly"] | None = None

    @model_validator(mode="after")
    def validate_residence(self) -> Self:
        """Reject two conflicting residence instructions for one known year."""
        years = [r.year for r in self.residence if r.year is not None]
        if len(set(years)) != len(years):
            msg = "Enter one residence per effective year."
            raise ValueError(msg)
        if len({c.id for c in self.children}) != len(self.children):
            msg = "Child identities must be unique."
            raise ValueError(msg)
        return self


Profile = Annotated[
    WorkAssumptions
    | SpendingAssumptions
    | InvestmentAssumptions
    | EconomicAssumptions
    | RetirementAssumptions,
    Field(discriminator="kind"),
]


class ProfileSelection(ValueModel):
    """An exact reviewed copy with provenance, not a live database dependency."""

    source_id: UUID
    source_revision: Annotated[int, Field(strict=True, ge=1)]
    source_name: Label
    inputs: Profile
    adult_mapping: tuple[Slot, Slot] | None = None

    @model_validator(mode="after")
    def validate_mapping(self) -> Self:
        """Require a distinct household person for each scenario adult."""
        if self.adult_mapping is not None and (
            set(self.adult_mapping) != {1, 2}
            or self.inputs.kind not in {"work", "retirement"}
        ):
            msg = "Map each Work or Retirement adult to a different household adult."
            raise ValueError(msg)
        return self


class PlanningScenario(Assumptions):
    """A saved selection of inputs, without projected balances or success measures."""

    kind: Literal["plan"] = "plan"
    household: PlanningHousehold = Field(default_factory=PlanningHousehold)
    wealth: SavedWealthSnapshot | None = None
    components: tuple[ProfileSelection, ...] = ()

    @model_validator(mode="after")
    def validate_bindings(self) -> Self:
        """Reject repeated source selections."""
        identities = [c.source_id for c in self.components]
        if len(identities) != len(set(identities)):
            msg = "Choose each saved scenario once."
            raise ValueError(msg)
        return self

    @property
    def combination_count(self) -> int:
        """Count named-scenario combinations, without expanding numeric ranges."""
        return math.prod(
            sum(c.inputs.kind == kind for c in self.components)
            for kind in PROFILE_KINDS
        )

    def combinations(self) -> Iterator[PlanningScenario]:
        """Yield independent selections for later analysis, without forecasts."""
        choices = [
            tuple(c for c in self.components if c.inputs.kind == kind)
            for kind in PROFILE_KINDS
        ]
        for components in itertools.product(*choices):
            yield self.model_copy(update={"components": components})


PlanningInputs = Annotated[
    WorkAssumptions
    | SpendingAssumptions
    | InvestmentAssumptions
    | EconomicAssumptions
    | RetirementAssumptions
    | PlanningScenario,
    Field(discriminator="kind"),
]
INPUT_ADAPTER: TypeAdapter[PlanningInputs] = TypeAdapter(PlanningInputs)


class SavedPlanningDocument(ValueModel):
    """Versioned, portable input document shared by persistence and backups."""

    document_type: Literal["planning_document"] = "planning_document"
    schema_version: Literal[4] = 4
    id: UUID = Field(default_factory=uuid4)
    revision: Annotated[int, Field(strict=True, ge=1)] = 1
    updated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    inputs: PlanningInputs


def select_profile(document: SavedPlanningDocument) -> ProfileSelection:
    """Copy a reviewed library profile; Plans cannot contain other Plans."""
    return ProfileSelection(
        source_id=document.id,
        source_revision=document.revision,
        source_name=document.inputs.name,
        inputs=TypeAdapter[Profile](Profile).validate_python(document.inputs),
    )


def replace_profile(
    plan: PlanningScenario, document: SavedPlanningDocument
) -> PlanningScenario:
    """Explicitly replace one copy and invalidate affected participant alignment."""
    selection = select_profile(document)
    data = plan.model_dump()
    data["components"] = (
        *[c for c in plan.components if c.inputs.kind != document.inputs.kind],
        selection,
    )
    return PlanningScenario.model_validate(data)


def replace_wealth(
    plan: PlanningScenario, document: SavedWealthSnapshot | None
) -> PlanningScenario:
    """Replace the starting balances without changing future investment choices."""
    return PlanningScenario.model_validate(plan.model_dump() | {"wealth": document})


def household_from_defaults(household: Household) -> PlanningHousehold:
    """Copy known facts without inventing residence or personal tax elections."""
    first, second = household.adults
    return PlanningHousehold(
        reference_year=household.tax_year,
        adults=(
            PlanningAdult(name=first.name, age=first.age),
            PlanningAdult(name=second.name, age=second.age),
        ),
        children=tuple(
            PlanningChild(name=f"Child {i + 1}", birth_year=household.tax_year - c.age)
            for i, c in enumerate(household.dependents)
        ),
        filing_status=household.filing_status,
    )


def copy_annual_inputs(
    document: SavedScenario,
) -> tuple[WorkAssumptions, SpendingAssumptions, InvestmentAssumptions]:
    """Prepare unsaved profiles from annual inputs, excluding calculated cash and taxes."""
    source = document.inputs
    provenance = AnnualSource(
        id=document.id,
        revision=document.revision,
        tax_year=source.tax_year,
        input_digest=source.digest(),
    )
    adults = []
    slots: tuple[Slot, Slot] = (1, 2)
    for slot, adult in zip(slots, source.adults, strict=True):
        events: tuple[OptionEvent, ...] = ()
        if adult.option_award or adult.options.shares:
            events = (
                OptionEvent(
                    year=source.tax_year,
                    award=adult.option_award,
                    exercise=adult.options if adult.options.shares else None,
                ),
            )
        # The annual flag does not zero compensation; preserve amounts and ask for review.
        status: Literal["not_working", "working"] = (
            "not_working"
            if adult.not_working
            and not any(
                (adult.cash_pay, adult.employee_401k, adult.employer_retirement_rate)
            )
            else "working"
        )
        adults.append(
            AdultWork(
                slot=slot,
                name=adult.name,
                phases=(
                    WorkPhase(
                        start=source.tax_year,
                        end=source.tax_year + 1,
                        status=status,
                        employer=adult.employer,
                        cash_pay=adult.cash_pay,
                        real_growth=Exploration[Growth](central=Decimal(0)),
                        employee_401k=adult.employee_401k,
                        employer_retirement_rate=adult.employer_retirement_rate,
                        notes="Copied for one year; review employment status, dates and growth.",
                    ),
                ),
                vesting=(
                    Vesting(
                        start=source.tax_year,
                        end=source.tax_year + 1,
                        amount=adult.rsu_vesting,
                    ),
                ),
                options=events,
            )
        )
    name = source.name or "Annual plan"
    costs = source.living_costs
    return (
        WorkAssumptions(
            name=f"{name[:70]} work",
            adults=(adults[0], adults[1]),
            rsu_taxes_from_shares=source.rsu_taxes_from_shares,
            money_year=source.tax_year,
            annual_source=provenance,
        ),
        SpendingAssumptions(
            name=f"{name[:70]} spending",
            expenses=(
                ExpenseSchedule(
                    label="Ordinary living",
                    levels=(
                        ExpenseLevel(
                            when=TimeAnchor(year=source.tax_year),
                            amount=Exploration[Money](central=costs.annual_total),
                        ),
                    ),
                    notes=f"Includes monthly rent {costs.rent}, childcare {costs.childcare}, other {costs.other}, each times 12. Qualifying care {costs.eligible_care_expense} is included, not additional.",
                ),
            ),
            money_year=source.tax_year,
            annual_source=provenance,
        ),
        InvestmentAssumptions(
            name=f"{name[:68]} investments",
            saving=source.allocation_plan,
            money_year=source.tax_year,
            annual_source=provenance,
        ),
    )


def missing_details(inputs: PlanningInputs) -> tuple[str, ...]:
    """Describe capture completeness, never certify readiness for a projection."""
    details: list[str] = []
    if isinstance(inputs, MonetaryAssumptions) and inputs.money_year is None:
        details.append("Money reference year is unknown.")
    if isinstance(inputs, WorkAssumptions):
        if inputs.rsu_taxes_from_shares is None:
            details.append("Choose how RSU taxes are funded.")
        for adult in inputs.adults:
            if not adult.phases:
                details.append(f"{adult.name}: employment timeline is unknown.")
            for i, phase in enumerate(adult.phases, 1):
                if phase.start is None or phase.status == "unknown":
                    details.append(
                        f"{adult.name}, phase {i}: start or work status is unknown."
                    )
                if phase.status == "working" and any(
                    v is None
                    for v in (
                        phase.cash_pay,
                        phase.real_growth.central,
                        phase.employer_retirement_rate,
                        phase.contribution_rate
                        if phase.contribution_basis == "pay_fraction"
                        else phase.employee_401k,
                    )
                ):
                    details.append(
                        f"{adult.name}, phase {i}: pay, growth or contributions need input."
                    )
            phases = sorted(
                (p for p in adult.phases if p.start is not None),
                key=lambda p: p.start or 0,
            )
            if any(
                a.end is not None and b.start is not None and a.end < b.start
                for a, b in itertools.pairwise(phases)
            ):
                details.append(
                    f"{adult.name}: gaps in employment are unknown intervals."
                )
            if not adult.vesting:
                details.append(
                    f"{adult.name}: vesting schedule unknown; enter zero explicitly if none."
                )
            if any(
                v.start is None or v.end is None or v.amount is None
                for v in adult.vesting
            ):
                details.append(f"{adult.name}: vesting dates or amounts need input.")
            if any(e.year is None for e in adult.options):
                details.append(f"{adult.name}: option event year needs input.")
    elif isinstance(inputs, SpendingAssumptions):
        if not inputs.expenses:
            details.append("Enter an expense budget; use zero explicitly if none.")
        for item in inputs.expenses:
            if not item.levels or any(
                level.amount.central is None for level in item.levels
            ):
                details.append(f"{item.label}: expense amounts need input.")
        details.extend(_spending_timing(inputs, (None, None)))
    elif isinstance(inputs, InvestmentAssumptions):
        if inputs.saving is None:
            details.append("Saving instructions are unknown.")
        required = 2 if inputs.policy == "gradual" else 1
        if (
            inputs.policy == "undecided"
            or len(inputs.checkpoints) < required
            or any(c.mix is None for c in inputs.checkpoints)
        ):
            details.append("Portfolio policy or allocation checkpoints need input.")
        if inputs.rebalancing == "undecided":
            details.append("Rebalancing intention is undecided.")
        if inputs.reserved_cash is None:
            details.append("Cash reserve needs an amount; enter zero if none.")
        details.extend(_investment_timing(inputs, (None, None)))
    elif isinstance(inputs, EconomicAssumptions):
        for field in ("inflation", "equities", "fixed_income", "cash"):
            if getattr(inputs, field).central is None:
                details.append(
                    f"{field.replace('_', ' ').capitalize()} outlook is unknown."
                )
    elif isinstance(inputs, RetirementAssumptions):
        for retiree in inputs.adults:
            if retiree.retirement.central is None:
                details.append(f"{retiree.name}: retirement year needs input.")
        if inputs.mode == "ages":
            details.extend(
                f"{a.name}: planning age is unknown."
                for a in inputs.adults
                if a.age.central is None
            )
        else:
            if not (
                inputs.source.description
                and inputs.source.version
                and inputs.table_basis != "unknown"
            ):
                details.append(
                    "Mortality table, version and period/cohort basis need input."
                )
            details.extend(
                f"{a.name}: mortality classification is unknown."
                for a in inputs.adults
                if not a.table_classification
            )
    elif isinstance(inputs, PlanningScenario):
        choices = [
            tuple(c for c in inputs.components if c.inputs.kind == kind) or (None,)
            for kind in PROFILE_KINDS
        ]
        for selection in itertools.product(*choices):
            case = inputs.model_copy(
                update={"components": tuple(c for c in selection if c is not None)}
            )
            details.extend(_plan_details(case))
    return tuple(dict.fromkeys(details))


def _spending_timing(
    spending: SpendingAssumptions,
    retirements: tuple[int | None, int | None],
    household: PlanningHousehold | None = None,
    end_years: tuple[int | None, int | None] | None = None,
) -> list[str]:
    """Check expense order after resolving references to household facts."""
    details = []
    for item in spending.expenses:
        anchors = [level.when for level in item.levels]
        if item.end:
            anchors.append(item.end)
        years = [
            anchor.resolve(retirements, household, end_years) for anchor in anchors
        ]
        if any(year is None for year in years):
            details.append(
                f"Spending timing unresolved: {item.label}; date or person needs input."
            )
        if any(
            a is not None and b is not None and b <= a
            for a, b in itertools.pairwise(years)
        ):
            details.append(
                f"Timing conflict: {item.label} boundaries must increase in time."
            )
    return details


def _investment_timing(
    investments: InvestmentAssumptions,
    retirements: tuple[int | None, int | None],
    household: PlanningHousehold | None = None,
    end_years: tuple[int | None, int | None] | None = None,
) -> list[str]:
    """Check checkpoint order after resolving retirement anchors."""
    years = [
        c.when.resolve(retirements, household, end_years)
        for c in investments.checkpoints
    ]
    if any(y is None for y in years):
        return ["Investment timing unresolved: checkpoint dates need input."]
    if any(
        a is not None and b is not None and b <= a for a, b in itertools.pairwise(years)
    ):
        return ["Timing conflict: allocation checkpoints must increase in time."]
    return []


def plan_years(
    plan: PlanningScenario,
) -> tuple[tuple[int | None, int | None], tuple[int | None, int | None]]:
    """Return retirement and final-alive years in household adult order.

    Incomplete inputs retain unknown years for draft previews. Use one selected
    Retirement profile; multiple alternatives require choosing a combination.
    """
    selections = [c for c in plan.components if c.inputs.kind == "retirement"]
    retirements: list[int | None] = [None, None]
    ends: list[int | None] = [None, None]
    if len(selections) != 1 or selections[0].adult_mapping is None:
        return (None, None), (None, None)
    selection = selections[0]
    assert isinstance(selection.inputs, RetirementAssumptions)
    assert selection.adult_mapping is not None
    reference = plan.household.reference_year
    for retiree, slot in zip(
        selection.inputs.adults, selection.adult_mapping, strict=True
    ):
        retirements[slot - 1] = retiree.retirement.central
        age = plan.household.adults[slot - 1].age
        if (
            reference is not None
            and age is not None
            and retiree.age.central is not None
        ):
            ends[slot - 1] = reference + retiree.age.central - age
    return (retirements[0], retirements[1]), (ends[0], ends[1])


def _plan_details(plan: PlanningScenario) -> list[str]:
    """Check copied inputs together without consulting mutable source libraries."""
    details = []
    profiles = {c.inputs.kind: c.inputs for c in plan.components}
    for kind in PROFILE_KINDS:
        if kind not in profiles:
            details.append(f"Choose {kind} assumptions.")
        else:
            details.extend(
                f"{kind.capitalize()}: {d}"
                for d in missing_details(profiles[kind])
                if "timing unresolved" not in d.lower()
            )
    household = plan.household
    if household.reference_year is None or any(a.age is None for a in household.adults):
        details.append("Household ages and reference year need input.")
    if not household.residence or any(
        r.year is None or not r.location for r in household.residence
    ):
        details.append("Household residence needs input.")
    if household.filing_status is None or any(
        a.wa_cares_exempt is None for a in household.adults
    ):
        details.append("Household filing facts or personal tax elections need input.")
    if any(c.birth_year is None for c in household.children):
        details.append("Children's birth years need input.")
    years = {
        p.money_year
        for p in profiles.values()
        if isinstance(p, MonetaryAssumptions) and p.money_year is not None
    }
    if len(years) > 1:
        details.append("Money reference years differ; amounts have not been rebased.")
    selections = {c.inputs.kind: c for c in plan.components}
    retirements, end_years = plan_years(plan)
    retirement = profiles.get("retirement")
    if isinstance(retirement, RetirementAssumptions):
        mapping = selections["retirement"].adult_mapping
        if mapping is None:
            details.append("Match Retirement people in Household.")
        else:
            for adult, slot in zip(retirement.adults, mapping, strict=True):
                age = household.adults[slot - 1].age
                if (
                    age is not None
                    and plan.wealth
                    and household.reference_year is not None
                ):
                    age += plan.wealth.snapshot.as_of.year - household.reference_year
                if (
                    age is not None
                    and adult.age.central is not None
                    and adult.age.central < age
                ):
                    details.append(
                        f"Lifespan conflict: {adult.name}'s planning age precedes starting age."
                    )
    work = profiles.get("work")
    if isinstance(work, WorkAssumptions):
        mapping = selections["work"].adult_mapping
        if mapping is None:
            details.append("Match Work people in Household.")
        else:
            for worker, slot in zip(work.adults, mapping, strict=True):
                year = retirements[slot - 1]
                phases = sorted(
                    (
                        p
                        for p in worker.phases
                        if p.start is not None and (year is None or p.start < year)
                    ),
                    key=lambda p: p.start or 0,
                )
                if (
                    year is not None
                    and phases
                    and phases[-1].end is not None
                    and phases[-1].end < year
                ):
                    details.append(
                        f"{worker.name}: work schedule ends before retirement."
                    )
                if (
                    plan.wealth
                    and year is not None
                    and year > plan.wealth.snapshot.as_of.year
                    and not any(
                        p.start is not None
                        and p.start <= plan.wealth.snapshot.as_of.year
                        and (p.end is None or p.end > plan.wealth.snapshot.as_of.year)
                        for p in phases
                    )
                ):
                    details.append(
                        f"{worker.name}: employment at the starting date is unknown."
                    )
    retirement_pair = (retirements[0], retirements[1])
    spending = profiles.get("spending")
    if isinstance(spending, SpendingAssumptions):
        details.extend(
            _spending_timing(spending, retirement_pair, household, end_years)
        )
    investments = profiles.get("investments")
    if isinstance(investments, InvestmentAssumptions):
        details.extend(
            _investment_timing(investments, retirement_pair, household, end_years)
        )
    if plan.wealth is None:
        details.append("Choose a starting Wealth snapshot.")
    else:
        snapshot = plan.wealth.snapshot
        details.extend(f"Wealth: {detail}" for detail in snapshot.missing_details)
        if not snapshot.has_balances:
            details.append("Wealth: no balances entered.")
        if (
            isinstance(investments, InvestmentAssumptions)
            and investments.reserved_cash is not None
            and not any(
                a.balance is None or (a.balance and a.allocation is None)
                for a in snapshot.accounts
            )
            and investments.reserved_cash
            > snapshot.asset_classes.get("cash", Decimal(0))
        ):
            details.append("Investments: cash reserve exceeds recorded cash.")
    return details
