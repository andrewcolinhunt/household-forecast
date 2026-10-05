"""Dated household balances and their treatment in a future projection."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from financial_model.schemas import ZERO, Money, Rate, ValueModel

Label = Annotated[str, Field(min_length=1, max_length=80)]
Notes = Annotated[str, Field(max_length=4000)]
TaxTreatment = Literal["unknown", "taxable", "tax_deferred", "roth", "hsa"]
InvestmentType = Literal["unknown", "cash", "stocks", "bonds", "mixed"]


class AssetMix(ValueModel):
    """Stock, bond, and cash fractions shared by balances and allocation targets."""

    equities: Rate
    fixed_income: Rate
    cash: Rate

    @model_validator(mode="after")
    def validate_total(self) -> Self:
        """Require a complete allocation without assigning an implicit remainder."""
        if self.equities + self.fixed_income + self.cash != 1:
            message = "Stocks, bonds, and cash must total 100%."
            raise ValueError(message)
        return self


class AccountPosition(ValueModel):
    """One named account, counted once and pooled for household planning."""

    id: UUID = Field(default_factory=uuid4)
    label: Label
    balance: Money | None = None
    tax_treatment: TaxTreatment = "unknown"
    invested_in: InvestmentType = "unknown"
    mix: AssetMix | None = None
    owner: Literal[1, 2] | None = None
    cost_basis: Money | None = None

    @model_validator(mode="after")
    def validate_mix(self) -> Self:
        """Only mixed accounts have an entered percentage split."""
        if self.invested_in != "mixed" and self.mix is not None:
            message = "Choose Mixed before entering investment percentages."
            raise ValueError(message)
        return self

    @property
    def allocation(self) -> AssetMix | None:
        """Resolve simple choices to fractions without guessing unknown contents."""
        if self.invested_in in {"unknown", "mixed"}:
            return self.mix
        return AssetMix(
            equities=Decimal(int(self.invested_in == "stocks")),
            fixed_income=Decimal(int(self.invested_in == "bonds")),
            cash=Decimal(int(self.invested_in == "cash")),
        )


class DebtPosition(ValueModel):
    """A named amount owed, deducted once from household assets."""

    id: UUID = Field(default_factory=uuid4)
    label: Label
    amount_owed: Money | None = None
    annual_payment: Money | None = None
    interest_rate: Rate | None = None
    payment_frequency: Literal["annual", "monthly"] = "annual"


class PropertyPosition(ValueModel):
    """Illiquid real estate and fixed nominal rental assumptions."""

    id: UUID = Field(default_factory=uuid4)
    label: Label
    market_value: Money | None = None
    annual_appreciation: Annotated[Decimal, Field(gt=-1, allow_inf_nan=False)] = ZERO
    monthly_rent: Money = ZERO
    monthly_expenses: Money = ZERO
    annual_taxable_rental_income: Money | None = None
    mortgage_id: UUID | None = None


class WealthSnapshot(ValueModel):
    """Named accounts, properties, and debts sharing one financial date."""

    name: Annotated[str, Field(max_length=80)] = ""
    as_of: date
    currency: Literal["USD"] = "USD"
    notes: Notes = ""
    accounts: tuple[AccountPosition, ...] = ()
    debts: tuple[DebtPosition, ...] = ()
    properties: tuple[PropertyPosition, ...] = ()

    @property
    def title(self) -> str:
        """Use the financial date when no custom label was supplied."""
        return self.name or self.as_of.isoformat()

    @property
    def assets(self) -> Decimal:
        """Sum entered balances, without treating unknown amounts as known zeros."""
        return sum(
            (a.balance for a in self.accounts if a.balance is not None), ZERO
        ) + sum(
            (p.market_value for p in self.properties if p.market_value is not None),
            ZERO,
        )

    @property
    def liabilities(self) -> Decimal:
        """Sum entered amounts owed."""
        return sum(
            (d.amount_owed for d in self.debts if d.amount_owed is not None), ZERO
        )

    @property
    def net_recorded(self) -> Decimal:
        """Subtract debts from the recorded asset total."""
        return self.assets - self.liabilities

    @property
    def has_balances(self) -> bool:
        """Distinguish an empty snapshot from explicitly entered zero balances."""
        return (
            any(a.balance is not None for a in self.accounts)
            or any(d.amount_owed is not None for d in self.debts)
            or any(p.market_value is not None for p in self.properties)
        )

    @property
    def asset_classes(self) -> dict[str, Decimal]:
        """Split balances by projection category, retaining unclassified amounts."""
        groups: dict[str, Decimal] = {}
        for account in self.accounts:
            if account.balance is None:
                continue
            mix = account.allocation
            fractions = mix.model_dump() if mix else {"unknown": Decimal(1)}
            for category, fraction in fractions.items():
                if fraction:
                    groups[category] = (
                        groups.get(category, ZERO) + account.balance * fraction
                    )
        if any(p.market_value is not None for p in self.properties):
            groups["real_estate"] = sum(
                (p.market_value for p in self.properties if p.market_value is not None),
                ZERO,
            )
        return groups

    @property
    def missing_details(self) -> tuple[str, ...]:
        """Identify missing balances, investment details, and rental tax assumptions."""
        details: list[str] = []
        for account in self.accounts:
            if account.balance is None:
                details.append(f"{account.label}: balance needs input.")
            if account.balance == 0:
                continue
            if account.tax_treatment == "unknown":
                details.append(f"{account.label}: choose tax treatment.")
            if account.allocation is None:
                details.append(f"{account.label}: choose how it is invested.")
        details.extend(
            f"{debt.label}: amount owed needs input."
            for debt in self.debts
            if debt.amount_owed is None
        )
        for property in self.properties:
            if property.market_value is None:
                details.append(f"{property.label}: market value needs input.")
            if property.monthly_rent and property.annual_taxable_rental_income is None:
                details.append(
                    f"{property.label}: enter annual taxable rental income, including an explicit zero."
                )
        return tuple(details)

    @model_validator(mode="after")
    def validate_ids(self) -> Self:
        """Require distinct positions and mortgage references within this snapshot."""
        ids = [a.id for a in self.accounts] + [d.id for d in self.debts]
        ids += [p.id for p in self.properties]
        if len(ids) != len(set(ids)):
            message = "Every account, debt, and property needs a distinct ID."
            raise ValueError(message)
        debt_ids = {d.id for d in self.debts}
        if any(p.mortgage_id not in debt_ids for p in self.properties if p.mortgage_id):
            message = "A property's mortgage must refer to a debt in this snapshot."
            raise ValueError(message)
        return self


class SavedWealthSnapshot(ValueModel):
    """Versioned snapshot shared by storage and portable JSON backups."""

    document_type: Literal["wealth_snapshot"] = "wealth_snapshot"
    schema_version: Literal[2] = 2
    id: UUID = Field(default_factory=uuid4)
    revision: Annotated[int, Field(strict=True, ge=1)] = 1
    updated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    snapshot: WealthSnapshot
