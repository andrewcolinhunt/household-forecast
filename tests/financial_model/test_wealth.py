"""Minimal account snapshots, projection categories, and honest unknown values."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from financial_model.wealth import (
    AccountPosition,
    AssetMix,
    DebtPosition,
    SavedWealthSnapshot,
    WealthSnapshot,
)


def test_snapshot_reconciles_balances_mixed_accounts_and_debts() -> None:
    snapshot = WealthSnapshot(
        as_of=date(2026, 9, 1),
        accounts=(
            AccountPosition(
                label="Brokerage",
                balance=100000,
                tax_treatment="taxable",
                invested_in="mixed",
                mix=AssetMix(equities=".8", fixed_income=".15", cash=".05"),
            ),
            AccountPosition(label="Checking", balance=500, invested_in="cash"),
            AccountPosition(label="Unknown contents", balance=200),
            AccountPosition(label="Unknown balance"),
        ),
        debts=(
            DebtPosition(label="Card", amount_owed=1000),
            DebtPosition(label="Unknown loan"),
        ),
    )
    assert snapshot.assets == 100700
    assert snapshot.liabilities == 1000
    assert snapshot.net_recorded == 99700
    assert snapshot.asset_classes == {
        "equities": 80000,
        "fixed_income": 15000,
        "cash": 5500,
        "unknown": 200,
    }
    assert sum(snapshot.asset_classes.values()) == snapshot.assets
    assert "Unknown balance: balance needs input." in snapshot.missing_details
    assert "Unknown loan: amount owed needs input." in snapshot.missing_details


def test_snapshot_zero_empty_and_unknown_remain_distinct() -> None:
    empty = WealthSnapshot(as_of=date(2026, 1, 1))
    unknown = WealthSnapshot(
        as_of=empty.as_of, accounts=(AccountPosition(label="Unknown"),)
    )
    zero = WealthSnapshot(
        as_of=empty.as_of, accounts=(AccountPosition(label="Empty", balance=0),)
    )
    assert not empty.has_balances
    assert not unknown.has_balances
    assert zero.has_balances
    assert not zero.missing_details
    assert unknown.accounts[0].balance is None
    assert '"balance":null' in unknown.model_dump_json()


@pytest.mark.parametrize(
    ("choice", "fractions"),
    [("cash", (0, 0, 1)), ("stocks", (1, 0, 0)), ("bonds", (0, 1, 0))],
)
def test_account_projection_category_resolves_to_expected_return_mix(
    choice, fractions
) -> None:
    account = AccountPosition(label="Named account", balance=100, invested_in=choice)
    assert tuple(account.allocation.model_dump().values()) == fractions


def test_unknown_mix_is_not_inferred_from_name_or_tax_treatment() -> None:
    account = AccountPosition(
        label="Stock retirement fund", balance=100, tax_treatment="roth"
    )
    snapshot = WealthSnapshot(as_of=date(2026, 1, 1), accounts=(account,))
    assert account.allocation is None
    assert snapshot.asset_classes == {"unknown": 100}
    assert snapshot.missing_details == (
        "Stock retirement fund: choose how it is invested.",
    )
    incomplete = AccountPosition(label="Mixed", balance=50, invested_in="mixed")
    assert incomplete.allocation is None


@pytest.mark.parametrize(
    "mix",
    [
        {"equities": ".8", "fixed_income": ".3", "cash": 0},
        {"equities": "-.1", "fixed_income": ".5", "cash": ".6"},
        {"equities": "NaN", "fixed_income": 0, "cash": 0},
    ],
)
def test_mixed_account_rejects_invalid_fractions(mix) -> None:
    with pytest.raises(ValidationError):
        AccountPosition(label="Account", invested_in="mixed", mix=mix)


def test_simple_category_rejects_conflicting_percentages() -> None:
    with pytest.raises(ValidationError, match="Choose Mixed"):
        AccountPosition(
            label="Account",
            invested_in="cash",
            mix={"equities": 1, "fixed_income": 0, "cash": 0},
        )


@pytest.mark.parametrize("value", ["-1", "0.001", "NaN", "Infinity", "not money"])
def test_position_rejects_invalid_money(value: str) -> None:
    with pytest.raises(ValidationError):
        AccountPosition(label="Account", balance=value)
    with pytest.raises(ValidationError):
        DebtPosition(label="Debt", amount_owed=value)
    with pytest.raises(ValidationError):
        AccountPosition(label="Account", cost_basis=value)
    with pytest.raises(ValidationError):
        DebtPosition(label="Debt", annual_payment=value)


@pytest.mark.parametrize("value", ["-0.01", "1.01", "NaN", "Infinity"])
def test_debt_rejects_invalid_interest_rate(value: str) -> None:
    with pytest.raises(ValidationError):
        DebtPosition(label="Debt", interest_rate=value)


@pytest.mark.parametrize("owner", [0, 3, "Named owner"])
def test_account_rejects_unknown_household_slot(owner: object) -> None:
    with pytest.raises(ValidationError):
        AccountPosition.model_validate({"label": "Account", "owner": owner})


def test_existing_wealth_documents_leave_forecast_facts_unknown() -> None:
    saved = SavedWealthSnapshot.model_validate(
        {
            "schema_version": 2,
            "snapshot": {
                "as_of": "2026-01-01",
                "accounts": [{"label": "Adult 1 Roth", "balance": "100"}],
                "debts": [{"label": "Loan", "amount_owed": "200"}],
            },
        }
    )
    account, debt = saved.snapshot.accounts[0], saved.snapshot.debts[0]
    assert account.owner is None
    assert account.cost_basis is None
    assert debt.annual_payment is None
    assert debt.interest_rate is None
    assert SavedWealthSnapshot.model_validate_json(saved.model_dump_json()) == saved


def test_forecast_facts_round_trip_without_changing_recorded_wealth() -> None:
    saved = SavedWealthSnapshot(
        snapshot=WealthSnapshot(
            as_of=date(2026, 1, 1),
            accounts=(
                AccountPosition(
                    label="Account",
                    balance="100.01",
                    tax_treatment="taxable",
                    invested_in="stocks",
                    owner=2,
                    cost_basis="120.23",
                ),
            ),
            debts=(
                DebtPosition(
                    label="Loan",
                    amount_owed="30.04",
                    annual_payment="12.34",
                    interest_rate="0.0575",
                ),
            ),
        )
    )
    restored = SavedWealthSnapshot.model_validate_json(saved.model_dump_json())
    assert restored == saved
    assert restored.snapshot.net_recorded == Decimal("69.97")
    assert not restored.snapshot.missing_details


def test_snapshot_rejects_repeated_ids_across_accounts_and_debts() -> None:
    identity = uuid4()
    with pytest.raises(ValidationError, match="distinct ID"):
        WealthSnapshot(
            as_of=date(2026, 1, 1),
            accounts=(AccountPosition(id=identity, label="Account"),),
            debts=(DebtPosition(id=identity, label="Debt"),),
        )


@pytest.mark.parametrize(
    "change",
    [
        {"as_of": "2026-02-30"},
        {"currency": "EUR"},
        {"kind": "actual"},
        {"coverage": "partial"},
        {"direct_assets": []},
    ],
)
def test_snapshot_rejects_invalid_or_removed_fields(change: dict) -> None:
    with pytest.raises(ValidationError):
        WealthSnapshot.model_validate({"as_of": "2026-01-01"} | change)


def test_snapshot_round_trip_preserves_precision_and_independent_copies() -> None:
    original = SavedWealthSnapshot(
        snapshot=WealthSnapshot(
            name="Starting wealth",
            as_of=date(2026, 1, 1),
            notes="Manual balances",
            accounts=(
                AccountPosition(
                    label="Named account",
                    balance=Decimal("12345678901234.56"),
                    invested_in="stocks",
                    tax_treatment="taxable",
                ),
            ),
        )
    )
    assert (
        SavedWealthSnapshot.model_validate_json(original.model_dump_json()) == original
    )
    assert not original.snapshot.missing_details
    assert original.schema_version == 2
    raw = original.snapshot.model_dump(mode="json")
    raw["accounts"][0]["balance"] = "10.00"
    copied = WealthSnapshot.model_validate(raw)
    assert copied.assets == 10
    assert original.snapshot.assets == Decimal("12345678901234.56")
    assert original.snapshot.title == "Starting wealth"
    assert WealthSnapshot(as_of=date(2026, 1, 1)).title == "2026-01-01"
    with pytest.raises(ValidationError):
        SavedWealthSnapshot.model_validate(
            original.model_dump() | {"schema_version": 1}
        )
