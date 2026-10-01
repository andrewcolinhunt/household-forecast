"""Reusable planning inputs and reviewed combinations, without financial projections."""

from __future__ import annotations

import itertools
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Self, TypeVar
from uuid import UUID, uuid4

import numpy as np
from pydantic import AwareDatetime, Field, TypeAdapter, model_validator

from financial_model.schemas import (
    AllocationPlan,
    Amount,
    Household,
    ISOExercise,
    Money,
    OptionAward,
    Rate,
    SavedScenario,
    ValueModel,
)
from financial_model.wealth import Label, Notes, SavedWealthSnapshot

PlanningYear = Annotated[int, Field(strict=True, ge=1900, le=9999)]
Age = Annotated[int, Field(strict=True, ge=18, le=130)]
Growth = Annotated[Decimal, Field(gt=-1, allow_inf_nan=False)]
Nonnegative = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
Slot = Literal[1, 2]
Kind = Literal["work", "spending", "investments", "economy", "longevity", "plan"]
T = TypeVar("T", int, Decimal)


class Exploration[T: (int, Decimal)](ValueModel):
    """A central assumption and optional paired alternatives, with no probability law."""

    central: T | None = None
    lower: T | None = None
    upper: T | None = None
    notes: Notes = ""

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
    """A calendar year or offset from one adult's or the later retirement date."""

    kind: Literal["year", "adult_1", "adult_2", "both"] = "year"
    year: PlanningYear | None = None
    offset: Annotated[int, Field(strict=True, ge=-100, le=100)] = 0

    @model_validator(mode="after")
    def validate_kind(self) -> Self:
        """Reject ambiguous fixed and relative dates."""
        if self.kind == "year" and self.offset != 0:
            msg = "Calendar years cannot have a retirement offset."
            raise ValueError(msg)
        if self.kind != "year" and self.year is not None:
            msg = "Retirement anchors cannot also specify a calendar year."
            raise ValueError(msg)
        return self

    def resolve(self, retirements: tuple[int | None, int | None]) -> int | None:
        """Resolve input timing only; missing retirement dates remain unknown."""
        if self.kind == "year":
            return self.year
        if self.kind == "both":
            first, second = retirements
            year = (
                max(first, second) if first is not None and second is not None else None
            )
        else:
            year = retirements[0 if self.kind == "adult_1" else 1]
        return year + self.offset if year is not None else None


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
    """A copied adult label, chosen retirement timing, and explicit work phases."""

    slot: Slot
    name: Label
    retirement: Exploration[PlanningYear] = Field(
        default_factory=Exploration[PlanningYear]
    )
    phases: tuple[WorkPhase, ...] = ()
    vesting: tuple[Vesting, ...] = ()
    options: tuple[OptionEvent, ...] = ()

    @model_validator(mode="after")
    def validate_timeline(self) -> Self:
        """Reject known overlap and employment continuing past retirement."""
        phases = sorted(
            (p for p in self.phases if p.start is not None), key=lambda p: p.start or 0
        )
        for previous, following in itertools.pairwise(phases):
            end = previous.end or self.retirement.central
            if following.start is not None and (end is None or end > following.start):
                msg = "Employment phases must not overlap; close the earlier phase."
                raise ValueError(msg)
        if self.retirement.central is not None:
            for phase in phases:
                if (
                    phase.status == "working"
                    and phase.start is not None
                    and (
                        phase.start >= self.retirement.central
                        or (
                            phase.end is not None
                            and phase.end > self.retirement.central
                        )
                    )
                ):
                    msg = "Working phases must finish by the retirement year."
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


class Budget(ValueModel):
    """Annual household consumption, with optional essential spending and alternatives."""

    amount: Exploration[Money] = Field(default_factory=Exploration[Money])
    essential: Money | None = None
    notes: Notes = ""

    @model_validator(mode="after")
    def validate_essential(self) -> Self:
        """An essential portion cannot exceed an entered total."""
        if (
            self.essential is not None
            and self.amount.central is not None
            and self.essential > self.amount.central
        ):
            msg = "Essential spending cannot exceed the budget."
            raise ValueError(msg)
        return self


class BudgetReplacement(ValueModel):
    """A replacement baseline, leaving separately dated adjustments intact."""

    label: Label
    when: TimeAnchor = Field(default_factory=TimeAnchor)
    budget: Budget = Field(default_factory=Budget)


class SpendingAdjustment(ValueModel):
    """Additional or reduced spending, once or over a half-open interval."""

    label: Label
    amount: Exploration[Amount] = Field(default_factory=Exploration[Amount])
    start: TimeAnchor = Field(default_factory=TimeAnchor)
    end: TimeAnchor | None = None
    frequency: Literal["recurring", "once"] = "recurring"
    notes: Notes = ""

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        """Reject ambiguous one-time entries and known reversed intervals."""
        if self.frequency == "once" and self.end is not None:
            msg = "One-time spending has one date, not an end date."
            raise ValueError(msg)
        if self.end and self.start.kind == self.end.kind:
            start = self.start.year if self.start.kind == "year" else self.start.offset
            end = self.end.year if self.end.kind == "year" else self.end.offset
            if start is not None and end is not None and end <= start:
                msg = "Recurring spending ends after its start."
                raise ValueError(msg)
        return self


class SpendingAssumptions(MonetaryAssumptions):
    """A baseline, replacements, and bounded additions to household consumption."""

    kind: Literal["spending"] = "spending"
    baseline: Budget = Field(default_factory=Budget)
    replacements: tuple[BudgetReplacement, ...] = ()
    adjustments: tuple[SpendingAdjustment, ...] = ()
    survivor: Budget | None = None

    @model_validator(mode="after")
    def validate_replacements(self) -> Self:
        """Reject duplicate fully specified replacement anchors."""
        anchors = [
            r.when
            for r in self.replacements
            if r.when.kind != "year" or r.when.year is not None
        ]
        if len(anchors) != len(set(anchors)):
            msg = "Only one baseline replacement may use the same effective time."
            raise ValueError(msg)
        return self


class AssetMix(ValueModel):
    """A complete target allocation, independent of actual account holdings."""

    equities: Rate
    fixed_income: Rate
    cash: Rate

    @model_validator(mode="after")
    def validate_total(self) -> Self:
        """Require complete allocation without implicitly assigning a remainder."""
        if self.equities + self.fixed_income + self.cash != 1:
            msg = "Target equities, fixed income, and cash must total 100%."
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
    interpolation: Literal["linear_then_hold"] = "linear_then_hold"
    rebalancing: Literal["annual", "flows_only", "undecided"] = "undecided"
    employer_stock: Literal["keep", "diversify", "undecided"] = "undecided"
    diversify_start: PlanningYear | None = None
    diversify_end: PlanningYear | None = None

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
        if self.employer_stock != "diversify" and (
            self.diversify_start or self.diversify_end
        ):
            msg = "Diversification dates require a diversification intention."
            raise ValueError(msg)
        if (
            self.diversify_start
            and self.diversify_end
            and self.diversify_end <= self.diversify_start
        ):
            msg = "Diversification must end after it starts."
            raise ValueError(msg)
        return self


class EconomicAssumptions(Assumptions):
    """Nominal compound return outlooks, inflation, and optional statistical inputs."""

    kind: Literal["economy"] = "economy"
    inflation: Exploration[Growth] = Field(default_factory=Exploration[Growth])
    equities: Exploration[Growth] = Field(default_factory=Exploration[Growth])
    fixed_income: Exploration[Growth] = Field(default_factory=Exploration[Growth])
    cash: Exploration[Growth] = Field(default_factory=Exploration[Growth])
    return_basis: Literal["nominal_compound_before_fees_and_taxes"] = (
        "nominal_compound_before_fees_and_taxes"
    )
    forecast_years: Annotated[int, Field(strict=True, ge=1, le=150)] | None = None
    fees: Rate | None = None
    # Order is equities, fixed income, cash, inflation throughout.
    volatility: tuple[
        Nonnegative | None, Nonnegative | None, Nonnegative | None, Nonnegative | None
    ] = (None, None, None, None)
    correlation: (
        tuple[
            tuple[Decimal, Decimal, Decimal, Decimal],
            tuple[Decimal, Decimal, Decimal, Decimal],
            tuple[Decimal, Decimal, Decimal, Decimal],
            tuple[Decimal, Decimal, Decimal, Decimal],
        ]
        | None
    ) = None

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


class AdultLongevity(ValueModel):
    """One adult's horizon or classification for a later mortality model."""

    slot: Slot
    name: Label
    age: Exploration[Age] = Field(default_factory=Exploration[Age])
    table_classification: Annotated[str, Field(max_length=120)] = ""


class LongevityAssumptions(Assumptions):
    """Specified planning ages or a request to sample lifespans later."""

    kind: Literal["longevity"] = "longevity"
    mode: Literal["ages", "sample"] = "ages"
    adults: tuple[AdultLongevity, AdultLongevity] = (
        AdultLongevity(slot=1, name="Adult 1"),
        AdultLongevity(slot=2, name="Adult 2"),
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
        return self


Profile = Annotated[
    WorkAssumptions
    | SpendingAssumptions
    | InvestmentAssumptions
    | EconomicAssumptions
    | LongevityAssumptions,
    Field(discriminator="kind"),
]


class ProfileSelection(ValueModel):
    """An exact reviewed copy with provenance, not a live database dependency."""

    source_id: UUID
    source_revision: Annotated[int, Field(strict=True, ge=1)]
    source_name: Label
    inputs: Profile


class OwnerBinding(ValueModel):
    """A reviewed mapping of a specific Wealth row to household participants."""

    row_id: UUID
    owner: Literal["adult_1", "adult_2", "joint", "other", "unknown"] = "unknown"


class PlanningScenario(Assumptions):
    """A saved selection of inputs, without projected balances or success measures."""

    kind: Literal["plan"] = "plan"
    household: PlanningHousehold = Field(default_factory=PlanningHousehold)
    wealth: SavedWealthSnapshot | None = None
    components: tuple[ProfileSelection, ...] = ()
    work_adults: tuple[Slot, Slot] | None = None
    longevity_adults: tuple[Slot, Slot] | None = None
    owners: tuple[OwnerBinding, ...] = ()
    portfolio_accounts: tuple[UUID, ...] = ()
    reserved_cash: Money | None = None

    @model_validator(mode="after")
    def validate_bindings(self) -> Self:
        """Reject duplicate categories and bindings outside the embedded inventory."""
        kinds = [c.inputs.kind for c in self.components]
        if len(kinds) != len(set(kinds)):
            msg = "Choose at most one profile from each category."
            raise ValueError(msg)
        for mapping in (self.work_adults, self.longevity_adults):
            if mapping is not None and set(mapping) != {1, 2}:
                msg = "Map each profile adult to a different household adult."
                raise ValueError(msg)
        accounts = set()
        rows = set()
        if self.wealth:
            snapshot = self.wealth.snapshot
            accounts = {a.id for a in snapshot.accounts}
            rows = (
                accounts
                | {a.id for a in snapshot.direct_assets}
                | {d.id for d in snapshot.debts}
            )
        bindings = [b.row_id for b in self.owners]
        if len(set(bindings)) != len(bindings) or not set(bindings) <= rows:
            msg = "Owner bindings must uniquely identify rows in the selected Wealth snapshot."
            raise ValueError(msg)
        if (
            len(set(self.portfolio_accounts)) != len(self.portfolio_accounts)
            or not set(self.portfolio_accounts) <= accounts
        ):
            msg = "Portfolio selection must use distinct accounts from the selected Wealth snapshot."
            raise ValueError(msg)
        return self


PlanningInputs = Annotated[
    WorkAssumptions
    | SpendingAssumptions
    | InvestmentAssumptions
    | EconomicAssumptions
    | LongevityAssumptions
    | PlanningScenario,
    Field(discriminator="kind"),
]
INPUT_ADAPTER: TypeAdapter[PlanningInputs] = TypeAdapter(PlanningInputs)


class SavedPlanningDocument(ValueModel):
    """Versioned, portable input document shared by persistence and backups."""

    document_type: Literal["planning_document"] = "planning_document"
    schema_version: Literal[1] = 1
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
    if document.inputs.kind in {"work", "longevity"}:
        data[f"{document.inputs.kind}_adults"] = None
    return PlanningScenario.model_validate(data)


def replace_wealth(
    plan: PlanningScenario, document: SavedWealthSnapshot | None
) -> PlanningScenario:
    """Copy a selected inventory and clear all bindings requiring renewed review."""
    return PlanningScenario.model_validate(
        plan.model_dump()
        | {
            "wealth": document,
            "owners": (),
            "portfolio_accounts": (),
            "reserved_cash": None,
        }
    )


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
            baseline=Budget(
                amount=Exploration[Money](central=costs.annual_total),
                notes=f"Includes monthly rent {costs.rent}, childcare {costs.childcare}, other {costs.other}, each times 12. Qualifying care {costs.eligible_care_expense} is included, not additional.",
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
            if adult.retirement.central is None:
                details.append(f"{adult.name}: retirement year is unknown.")
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
            if (
                phases
                and phases[-1].end is not None
                and adult.retirement.central is not None
                and phases[-1].end < adult.retirement.central
            ):
                details.append(f"{adult.name}: timeline ends before retirement.")
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
        if inputs.baseline.amount.central is None:
            details.append("Baseline annual spending is unknown.")
        if inputs.survivor is None or inputs.survivor.amount.central is None:
            details.append("Survivor spending is unresolved.")
        if any(r.budget.amount.central is None for r in inputs.replacements):
            details.append("Replacement budgets need amounts.")
        if any(a.amount.central is None for a in inputs.adjustments):
            details.append("Additional spending needs amounts.")
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
        if inputs.employer_stock == "undecided":
            details.append("Employer-stock intention is undecided.")
        if inputs.employer_stock == "diversify" and (
            inputs.diversify_start is None or inputs.diversify_end is None
        ):
            details.append("Employer-stock diversification dates need input.")
        details.extend(_investment_timing(inputs, (None, None)))
    elif isinstance(inputs, EconomicAssumptions):
        for field in ("inflation", "equities", "fixed_income", "cash"):
            if getattr(inputs, field).central is None:
                details.append(
                    f"{field.replace('_', ' ').capitalize()} outlook is unknown."
                )
        if inputs.forecast_years is None:
            details.append("Forecast horizon is unknown.")
        if inputs.fees is None:
            details.append("Investment fees are unknown.")
    elif isinstance(inputs, LongevityAssumptions):
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
        details.extend(_plan_details(inputs))
    return tuple(dict.fromkeys(details))


def _spending_timing(
    spending: SpendingAssumptions, retirements: tuple[int | None, int | None]
) -> list[str]:
    """Check change boundaries without expanding a financial time series."""
    details = []
    replacements = [
        (r.when.resolve(retirements), r.budget.amount.central)
        for r in spending.replacements
    ]
    dates = [y for y, _ in replacements if y is not None]
    if len(dates) != len(set(dates)):
        details.append(
            "Timing conflict: baseline replacements resolve to the same year."
        )
    if any(y is None for y, _ in replacements):
        details.append("Spending timing unresolved: replacement date needs input.")
    adjustments = []
    for adjustment in spending.adjustments:
        start = adjustment.start.resolve(retirements)
        end = adjustment.end.resolve(retirements) if adjustment.end else None
        if start is None or (adjustment.end and end is None):
            details.append(f"Spending timing unresolved: {adjustment.label}.")
        if start is not None and end is not None and end <= start:
            details.append(
                f"Timing conflict: {adjustment.label} ends before or at its start."
            )
        adjustments.append((start, end, adjustment))
    # Only entered schedule boundaries need checking for a negative consumption total.
    boundaries = set(dates)
    for start, end, adjustment in adjustments:
        if start is not None:
            boundaries.update(
                (start, start + 1) if adjustment.frequency == "once" else (start,)
            )
        if end is not None:
            boundaries.add(end)
    for year in boundaries:
        budget = spending.baseline.amount.central
        for when, amount in sorted(
            ((y, v) for y, v in replacements if y is not None), key=lambda item: item[0]
        ):
            if when <= year:
                budget = amount
        active = [
            a.amount.central
            for start, end, a in adjustments
            if start is not None
            and (
                year == start
                if a.frequency == "once"
                else start <= year and (end is None or year < end)
            )
        ]
        if (
            budget is not None
            and all(v is not None for v in active)
            and budget + sum(v for v in active if v is not None) < 0
        ):
            details.append(f"Spending conflict: entered total is negative at {year}.")
    return details


def _investment_timing(
    investments: InvestmentAssumptions, retirements: tuple[int | None, int | None]
) -> list[str]:
    """Check checkpoint order after resolving retirement anchors."""
    years = [c.when.resolve(retirements) for c in investments.checkpoints]
    if any(y is None for y in years):
        return ["Investment timing unresolved: checkpoint dates need input."]
    if any(
        a is not None and b is not None and b <= a for a, b in itertools.pairwise(years)
    ):
        return ["Timing conflict: allocation checkpoints must increase in time."]
    return []


def _plan_details(plan: PlanningScenario) -> list[str]:
    """Check copied inputs together without consulting mutable source libraries."""
    details = []
    profiles = {c.inputs.kind: c.inputs for c in plan.components}
    for kind in ("work", "spending", "investments", "economy", "longevity"):
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
    retirements: list[int | None] = [None, None]
    work = profiles.get("work")
    if isinstance(work, WorkAssumptions):
        if plan.work_adults is None:
            details.append("Review Work adult alignment in Household.")
        else:
            for adult, slot in zip(work.adults, plan.work_adults, strict=True):
                retirements[slot - 1] = adult.retirement.central
                if (
                    plan.wealth
                    and adult.retirement.central is not None
                    and adult.retirement.central > plan.wealth.snapshot.as_of.year
                ):
                    phases = [p for p in adult.phases if p.start is not None]
                    if not any(
                        p.start is not None
                        and p.start <= plan.wealth.snapshot.as_of.year
                        and (p.end is None or p.end > plan.wealth.snapshot.as_of.year)
                        for p in phases
                    ):
                        details.append(
                            f"{adult.name}: employment at the starting date is unknown."
                        )
    retirement_pair = (retirements[0], retirements[1])
    spending = profiles.get("spending")
    if isinstance(spending, SpendingAssumptions):
        details.extend(_spending_timing(spending, retirement_pair))
    investments = profiles.get("investments")
    if isinstance(investments, InvestmentAssumptions):
        details.extend(_investment_timing(investments, retirement_pair))
    longevity = profiles.get("longevity")
    if isinstance(longevity, LongevityAssumptions):
        if plan.longevity_adults is None:
            details.append("Review Longevity adult alignment in Household.")
        elif longevity.mode == "ages":
            for lifespan, slot in zip(
                longevity.adults, plan.longevity_adults, strict=True
            ):
                age = household.adults[slot - 1].age
                if (
                    age is not None
                    and plan.wealth
                    and household.reference_year is not None
                ):
                    age += plan.wealth.snapshot.as_of.year - household.reference_year
                if (
                    age is not None
                    and lifespan.age.central is not None
                    and lifespan.age.central < age
                ):
                    details.append(
                        f"Longevity conflict: {lifespan.name}'s planning age precedes starting age."
                    )
    if plan.wealth is None:
        details.append("Choose a starting Wealth snapshot.")
    else:
        snapshot = plan.wealth.snapshot
        details.extend(f"Wealth: {detail}" for detail in snapshot.missing_details)
        if not snapshot.has_balances:
            details.append("Wealth: no balances entered.")
        known_owners = {b.row_id for b in plan.owners if b.owner != "unknown"}
        if any(
            row_id not in known_owners
            for row_id in [
                *(a.id for a in snapshot.accounts),
                *(a.id for a in snapshot.direct_assets),
                *(d.id for d in snapshot.debts),
            ]
        ):
            details.append("Review Wealth owner mappings.")
        if not plan.portfolio_accounts:
            details.append("Choose the accounts covered by the portfolio strategy.")
        if plan.reserved_cash is None:
            details.append("Reserved cash is unknown; enter zero if none.")
        accounts = [a for a in snapshot.accounts if a.id in plan.portfolio_accounts]
        if any(
            a.reported_value is None
            or a.unitemized
            or any(
                h.value is None
                or h.asset_class not in {"equities", "fixed_income", "cash"}
                for h in a.holdings
            )
            for a in accounts
        ):
            details.append("Selected portfolio contents are not fully classified.")
        known_cash = sum(
            h.value or Decimal(0)
            for a in accounts
            for h in a.holdings
            if h.asset_class == "cash"
        )
        if plan.reserved_cash is not None and plan.reserved_cash > known_cash:
            details.append(
                "Reserved cash exceeds identified cash in selected accounts."
            )
    return details
