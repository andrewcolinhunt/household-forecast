"""Wealth inventory reconciliation and honest handling of incomplete data."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from financial_model.wealth import (
    AccountPosition,
    AssetPosition,
    DebtPosition,
    DirectAssetPosition,
    SavedWealthSnapshot,
    WealthSnapshot,
)


def test_snapshot_reconciles_accounts_contents_direct_assets_and_debts() -> None:
    snapshot = WealthSnapshot(
        as_of=date(2026, 9, 1),
        accounts=(
            AccountPosition(
                label="Brokerage",
                account_type="brokerage",
                reported_value=100000,
                holdings=(
                    AssetPosition(
                        label="Equity fund",
                        instrument_type="etf",
                        asset_class="equities",
                        value=70000,
                    ),
                    AssetPosition(
                        label="Shares",
                        instrument_type="stock",
                        asset_class="equities",
                        value=20000,
                    ),
                ),
            ),
            AccountPosition(
                label="Partial",
                holdings=(AssetPosition(label="Cash", asset_class="cash", value=500),),
            ),
        ),
        direct_assets=(
            DirectAssetPosition(
                label="Property", asset_class="real_estate", value=200000
            ),
        ),
        debts=(
            DebtPosition(label="Mortgage", amount_owed=80000),
            DebtPosition(label="Unknown loan"),
        ),
    )
    assert snapshot.assets == 300500
    assert snapshot.liabilities == 80000
    assert snapshot.net_recorded == 220500
    assert snapshot.accounts[0].unitemized == 10000
    assert snapshot.accounts[1].unitemized is None
    assert snapshot.asset_classes == {
        "equities": 90000,
        "cash": 500,
        "real_estate": 200000,
        "unitemized": 10000,
    }
    assert sum(snapshot.asset_classes.values()) == snapshot.assets
    assert sum(snapshot.account_types.values()) == snapshot.assets
    assert any("Partial: total unknown" in d for d in snapshot.missing_details)
    assert any("Unknown loan: debt amount" in d for d in snapshot.missing_details)


def test_snapshot_zero_empty_and_unknown_remain_distinct() -> None:
    empty = WealthSnapshot(as_of=date(2026, 1, 1))
    unknown = WealthSnapshot(
        as_of=empty.as_of, accounts=(AccountPosition(label="Unknown"),)
    )
    zero = WealthSnapshot(
        as_of=empty.as_of, accounts=(AccountPosition(label="Empty", reported_value=0),)
    )
    assert not empty.has_balances
    assert not unknown.has_balances
    assert zero.has_balances
    assert zero.accounts[0].reported_value == 0
    assert unknown.accounts[0].reported_value is None
    assert '"reported_value":null' in unknown.model_dump_json()
    assert "unitemized" not in empty.asset_classes


def test_snapshot_classifications_do_not_infer_holdings_or_tax_splits() -> None:
    snapshot = WealthSnapshot(
        as_of=date(2026, 1, 1),
        accounts=(
            AccountPosition(label="Plan", account_type="401k", reported_value=1000),
            AccountPosition(
                label="Health",
                account_type="hsa",
                tax_treatment="hsa",
                reported_value=250,
            ),
        ),
    )
    assert snapshot.accounts[0].tax_treatment == "unknown"
    assert snapshot.asset_classes == {"unitemized": 1250}
    assert snapshot.account_types == {"401k": 1000, "hsa": 250}
    assert all(not a.holdings for a in snapshot.accounts)


def test_snapshot_known_total_with_unknown_holding_value() -> None:
    account = AccountPosition(
        label="Mixed",
        reported_value=500,
        holdings=(AssetPosition(label="Unknown amount"),),
    )
    snapshot = WealthSnapshot(as_of=date(2026, 1, 1), accounts=(account,))
    assert account.unitemized == 500
    assert snapshot.assets == 500
    assert any("Unknown amount" in d for d in snapshot.missing_details)


@pytest.mark.parametrize("value", ["-1", "0.001", "NaN", "Infinity", "not money"])
def test_position_rejects_invalid_money(value: str) -> None:
    with pytest.raises(ValidationError):
        AssetPosition(label="Asset", value=value)
    with pytest.raises(ValidationError):
        AccountPosition(label="Account", reported_value=value)
    with pytest.raises(ValidationError):
        DebtPosition(label="Debt", amount_owed=value)


def test_account_rejects_holdings_over_reported_total() -> None:
    with pytest.raises(ValidationError, match="holdings exceed"):
        AccountPosition(
            label="Account",
            reported_value=100,
            holdings=(AssetPosition(label="Shares", value=101),),
        )


def test_snapshot_rejects_repeated_ids_across_containers() -> None:
    identity = uuid4()
    with pytest.raises(ValidationError, match="distinct ID"):
        WealthSnapshot(
            as_of=date(2026, 1, 1),
            accounts=(
                AccountPosition(
                    label="Account",
                    holdings=(AssetPosition(id=identity, label="Shares"),),
                ),
            ),
            direct_assets=(DirectAssetPosition(id=identity, label="Shares twice"),),
        )
    holding = AssetPosition(label="One holding")
    with pytest.raises(ValidationError, match="distinct ID"):
        AccountPosition(label="Account", holdings=(holding, holding))


@pytest.mark.parametrize(
    "change",
    [{"as_of": "2026-02-30"}, {"kind": "guess"}, {"currency": "EUR"}, {"invented": 1}],
)
def test_snapshot_rejects_malformed_fields(change: dict) -> None:
    with pytest.raises(ValidationError):
        WealthSnapshot.model_validate({"as_of": "2026-01-01"} | change)


def test_snapshot_round_trip_retains_precision_and_all_metadata() -> None:
    snapshot = WealthSnapshot(
        name="A future starting point",
        as_of=date(2028, 7, 15),
        kind="hypothetical",
        coverage_notes="Property excluded",
        source_notes="Manual estimate",
        accounts=(
            AccountPosition(
                label="Savings group",
                owner="Joint",
                is_combined=True,
                reported_value=Decimal("12345678901234.56"),
            ),
        ),
        direct_assets=(
            DirectAssetPosition(
                label="Private shares",
                security_id="PrivateCo",
                source_notes="Sale restrictions unknown",
            ),
        ),
    )
    document = SavedWealthSnapshot(snapshot=snapshot)
    assert (
        SavedWealthSnapshot.model_validate_json(document.model_dump_json()) == document
    )
    assert '"12345678901234.56"' in document.model_dump_json()
    assert '"assets":' not in document.model_dump_json()
    assert snapshot.title == "A future starting point"


def test_snapshot_complete_reported_details_and_copy_independence() -> None:
    when = date(2026, 1, 1)
    snapshot = WealthSnapshot(
        as_of=when,
        coverage="complete_as_reported",
        accounts=(
            AccountPosition(
                label="Cash account",
                owner="Alex",
                account_type="checking",
                tax_treatment="taxable",
                valuation_date=when,
                reported_value=25,
                holdings=(
                    AssetPosition(
                        label="Deposit",
                        instrument_type="cash",
                        asset_class="cash",
                        value=25,
                    ),
                ),
            ),
        ),
    )
    assert not snapshot.missing_details
    assert snapshot.title == "2026-01-01"
    raw = snapshot.model_dump(mode="json")
    raw["accounts"][0]["holdings"][0]["value"] = "10.00"
    copied = WealthSnapshot.model_validate(raw)
    assert snapshot.accounts[0].holdings[0].value == 25
    assert copied.accounts[0].unitemized == 15
