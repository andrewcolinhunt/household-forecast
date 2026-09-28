"""Dated wealth inventories, independent of annual income and goal funding."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from financial_model.schemas import ZERO, Money, ValueModel

Label = Annotated[str, Field(min_length=1, max_length=80)]
Notes = Annotated[str, Field(max_length=4000)]
AccountType = Literal[
    "unknown", "checking", "savings", "brokerage", "ira", "401k", "403b", "hsa", "other"
]
TaxTreatment = Literal[
    "unknown", "taxable", "tax_deferred", "roth", "hsa", "mixed", "other"
]
InstrumentType = Literal[
    "unknown", "cash", "stock", "etf", "mutual_fund", "bond", "property", "other"
]
AssetClass = Literal[
    "unknown", "cash", "equities", "fixed_income", "real_estate", "mixed", "other"
]
DebtType = Literal["unknown", "credit_card", "mortgage", "loan", "other"]


class AssetPosition(ValueModel):
    """An individual holding or nonoverlapping group valued in dollars."""

    id: UUID = Field(default_factory=uuid4)
    label: Label
    instrument_type: InstrumentType = "unknown"
    asset_class: AssetClass = "unknown"
    value: Money | None = None
    security_id: Label | None = None
    source_notes: Notes = ""


class DirectAssetPosition(AssetPosition):
    """An asset outside accounts, with its own ownership and tax context."""

    owner: Label | None = None
    tax_treatment: TaxTreatment = "unknown"
    valuation_date: date | None = None


class AccountPosition(ValueModel):
    """A reported account balance and optional detail explaining that balance."""

    id: UUID = Field(default_factory=uuid4)
    label: Label
    institution: Label | None = None
    owner: Label | None = None
    account_type: AccountType = "unknown"
    tax_treatment: TaxTreatment = "unknown"
    reported_value: Money | None = None
    valuation_date: date | None = None
    source_notes: Notes = ""
    is_combined: Annotated[bool, Field(strict=True)] = False
    holdings: tuple[AssetPosition, ...] = ()

    @property
    def known_holdings(self) -> Decimal:
        """Sum only entered holding values, without treating missing values as zero."""
        return sum((h.value for h in self.holdings if h.value is not None), ZERO)

    @property
    def known_value(self) -> Decimal:
        """Count the reported total once, or known holdings when it is unavailable."""
        return (
            self.reported_value
            if self.reported_value is not None
            else self.known_holdings
        )

    @property
    def unitemized(self) -> Decimal | None:
        """Return the unexplained part of a known total, never an inferred cash value."""
        if self.reported_value is None:
            return None
        return self.reported_value - self.known_holdings

    @model_validator(mode="after")
    def validate_holdings(self) -> Self:
        """Reject contents that exceed the reported total or repeat a row identity."""
        if (
            self.reported_value is not None
            and self.known_holdings > self.reported_value
        ):
            message = f"{self.label}: holdings exceed the reported account value."
            raise ValueError(message)
        ids = [h.id for h in self.holdings]
        if len(ids) != len(set(ids)):
            message = "Each holding must have a distinct ID."
            raise ValueError(message)
        return self


class DebtPosition(ValueModel):
    """A positive amount owed, deducted once from the recorded asset subtotal."""

    id: UUID = Field(default_factory=uuid4)
    label: Label
    owner: Label | None = None
    debt_type: DebtType = "unknown"
    amount_owed: Money | None = None
    valuation_date: date | None = None
    source_notes: Notes = ""
    is_combined: Annotated[bool, Field(strict=True)] = False


class WealthSnapshot(ValueModel):
    """Reusable dated balances; missing information remains explicitly unknown."""

    name: Annotated[str, Field(max_length=80)] = ""
    as_of: date
    kind: Literal["actual", "hypothetical"] = "actual"
    currency: Literal["USD"] = "USD"
    coverage: Literal["partial", "complete_as_reported"] = "partial"
    source_notes: Notes = ""
    coverage_notes: Notes = ""
    accounts: tuple[AccountPosition, ...] = ()
    direct_assets: tuple[DirectAssetPosition, ...] = ()
    debts: tuple[DebtPosition, ...] = ()

    @property
    def title(self) -> str:
        """Use the financial date when no custom label was supplied."""
        return self.name or self.as_of.isoformat()

    @property
    def assets(self) -> Decimal:
        """Known asset subtotal, including partial accounts exactly once."""
        return sum((a.known_value for a in self.accounts), ZERO) + sum(
            (a.value for a in self.direct_assets if a.value is not None), ZERO
        )

    @property
    def liabilities(self) -> Decimal:
        """Known amounts owed, without claiming missing debts have zero balances."""
        return sum(
            (d.amount_owed for d in self.debts if d.amount_owed is not None), ZERO
        )

    @property
    def net_recorded(self) -> Decimal:
        """Known assets less known liabilities; not a claim of complete net worth."""
        return self.assets - self.liabilities

    @property
    def has_balances(self) -> bool:
        """Distinguish an empty inventory from explicitly entered zero balances."""
        return (
            any(a.reported_value is not None for a in self.accounts)
            or any(h.value is not None for a in self.accounts for h in a.holdings)
            or any(a.value is not None for a in self.direct_assets)
            or any(d.amount_owed is not None for d in self.debts)
        )

    @property
    def asset_classes(self) -> dict[str, Decimal]:
        """Group recorded asset values while retaining unknown contents separately."""
        groups: dict[str, Decimal] = {}
        holdings = (
            *self.direct_assets,
            *(h for a in self.accounts for h in a.holdings),
        )
        for holding in holdings:
            if holding.value is not None:
                key = holding.asset_class
                groups[key] = groups.get(key, ZERO) + holding.value
        for account in self.accounts:
            if account.unitemized:
                groups["unitemized"] = (
                    groups.get("unitemized", ZERO) + account.unitemized
                )
        return groups

    @property
    def account_types(self) -> dict[str, Decimal]:
        """Group account subtotals; directly held assets remain a separate group."""
        groups: dict[str, Decimal] = {}
        for account in self.accounts:
            key = account.account_type
            groups[key] = groups.get(key, ZERO) + account.known_value
        if self.direct_assets:
            groups["direct_assets"] = sum(
                (a.value for a in self.direct_assets if a.value is not None), ZERO
            )
        return groups

    @property
    def missing_details(self) -> tuple[str, ...]:
        """Describe incomplete coverage, amounts, dates, ownership, and contents."""
        details = (
            ["Household coverage is partial."] if self.coverage == "partial" else []
        )
        rows: tuple[AccountPosition | DirectAssetPosition | DebtPosition, ...] = (
            *self.accounts,
            *self.direct_assets,
            *self.debts,
        )
        for row in rows:
            if row.owner is None:
                details.append(f"{row.label}: owner unknown.")
            if row.valuation_date != self.as_of:
                details.append(f"{row.label}: valuation date differs or is unknown.")
            if isinstance(
                row, (AccountPosition, DirectAssetPosition)
            ) and row.tax_treatment in {"unknown", "mixed"}:
                details.append(f"{row.label}: tax treatment needs detail.")
        for account in self.accounts:
            if account.reported_value is None:
                details.append(
                    f"{account.label}: total unknown; known holdings are partial."
                )
            if account.unitemized:
                details.append(f"{account.label}: some contents are unitemized.")
            if account.account_type == "unknown" or account.is_combined:
                details.append(
                    f"{account.label}: account classification or breakdown missing."
                )
        for asset in (
            *self.direct_assets,
            *(h for a in self.accounts for h in a.holdings),
        ):
            if asset.value is None or "unknown" in (
                asset.asset_class,
                asset.instrument_type,
            ):
                details.append(f"{asset.label}: value or asset classification missing.")
        for debt in self.debts:
            if (
                debt.amount_owed is None
                or debt.debt_type == "unknown"
                or debt.is_combined
            ):
                details.append(
                    f"{debt.label}: debt amount, classification, or breakdown missing."
                )
        return tuple(details)

    @model_validator(mode="after")
    def validate_ids(self) -> Self:
        """Reject overlapping local identities across all accounts, holdings, and debts."""
        rows: tuple[AccountPosition | DirectAssetPosition | DebtPosition, ...] = (
            *self.accounts,
            *self.direct_assets,
            *self.debts,
        )
        ids = [r.id for r in rows] + [h.id for a in self.accounts for h in a.holdings]
        if len(ids) != len(set(ids)):
            message = (
                "Every account, holding, direct asset, and debt needs a distinct ID."
            )
            raise ValueError(message)
        return self


class SavedWealthSnapshot(ValueModel):
    """Versioned document shared by PostgreSQL storage and portable JSON backups."""

    document_type: Literal["wealth_snapshot"] = "wealth_snapshot"
    schema_version: Literal[1] = 1
    id: UUID = Field(default_factory=uuid4)
    revision: Annotated[int, Field(strict=True, ge=1)] = 1
    updated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    snapshot: WealthSnapshot
