"""Explicit, reviewed conversion from detailed inventories to account balances."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any

from financial_model.wealth import AccountPosition, SavedWealthSnapshot, WealthSnapshot


def _account(original: dict[str, Any], update: dict[str, Any]) -> AccountPosition:
    """Preserve balances and derive only fully documented investment mixes."""
    balance = original.get("reported_value")
    holdings = original.get("holdings", [])
    if balance is None and any(h.get("value") is not None for h in holdings):
        message = "Review an account with holdings but no total before migration."
        raise ValueError(message)
    data = {
        "id": original["id"],
        "label": original["label"],
        "balance": balance,
        "tax_treatment": original.get("tax_treatment", "unknown"),
        "invested_in": "unknown",
        "mix": None,
    }
    if data["tax_treatment"] not in {"taxable", "tax_deferred", "roth", "hsa"}:
        data["tax_treatment"] = "unknown"
    groups = dict.fromkeys(("equities", "fixed_income", "cash"), Decimal(0))
    complete = True
    for holding in holdings:
        category, value = holding.get("asset_class"), holding.get("value")
        if category not in groups or value is None:
            complete = False
        else:
            groups[category] += Decimal(str(value))
    if (
        complete
        and balance is not None
        and Decimal(str(balance)) > 0
        and sum(groups.values()) == Decimal(str(balance))
    ):
        nonzero = [k for k, v in groups.items() if v]
        if len(nonzero) == 1:
            data["invested_in"] = {
                "equities": "stocks",
                "fixed_income": "bonds",
                "cash": "cash",
            }[nonzero[0]]
        else:
            mix = dict.fromkeys(groups, Decimal(0))
            for category in nonzero[:-1]:
                mix[category] = groups[category] / Decimal(str(balance))
            mix[nonzero[-1]] = Decimal(1) - sum(mix.values(), Decimal(0))
            data.update(invested_in="mixed", mix=mix)
    if not set(update) <= {"tax_treatment", "invested_in", "mix"}:
        message = (
            "Reviewed account updates may only set tax treatment or investment mix."
        )
        raise ValueError(message)
    return AccountPosition.model_validate(data | update)


def convert_wealth_document(
    original: dict[str, Any],
    account_updates: dict[str, dict[str, Any]] | None = None,
) -> SavedWealthSnapshot:
    """Convert a version-one snapshot, preserving names, amounts, and identities.

    Args:
        original: Original document retained in the private backup.
        account_updates: Reviewed classifications keyed by account UUID.

    Returns:
        A schema-two snapshot with an incremented revision.

    Raises:
        ValueError: If the old version or asset structure needs manual review.
    """
    if (
        original.get("schema_version") != 1
        or original.get("document_type") != "wealth_snapshot"
    ):
        message = "Expected a version-one Wealth snapshot."
        raise ValueError(message)
    old = original["snapshot"]
    if old.get("direct_assets"):
        message = "Review directly held assets before migrating to named accounts."
        raise ValueError(message)
    updates = account_updates or {}
    return SavedWealthSnapshot(
        id=original["id"],
        revision=original["revision"] + 1,
        snapshot=WealthSnapshot.model_validate(
            {
                "name": old.get("name", ""),
                "as_of": old["as_of"],
                "currency": old.get("currency", "USD"),
                "notes": "",
                "accounts": [
                    _account(a, updates.get(a["id"], {}))
                    for a in old.get("accounts", [])
                ],
                "debts": [
                    {k: d[k] for k in ("id", "label", "amount_owed")}
                    for d in old.get("debts", [])
                ],
            }
        ),
    )


def convert_investment_inputs(original: dict[str, Any]) -> dict[str, Any]:
    """Remove company-share instructions that no longer have separate balances."""
    data = deepcopy(original)
    if (
        data.get("employer_stock") == "diversify"
        or data.get("diversify_start")
        or data.get("diversify_end")
    ):
        message = "Review a dated company-share sale strategy before pooling stocks."
        raise ValueError(message)
    for key in ("employer_stock", "diversify_start", "diversify_end"):
        data.pop(key, None)
    data.setdefault("reserved_cash", None)
    return data


def convert_plan_inputs(
    original: dict[str, Any],
    account_updates: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Preserve independent Plan copies and move cash reserve into Investments."""
    data = deepcopy(original)
    reserve = data.pop("reserved_cash", None)
    data.pop("owners", None)
    data.pop("portfolio_accounts", None)
    if data.get("wealth") is not None:
        old = data["wealth"]
        converted = convert_wealth_document(old, account_updates).model_dump(
            mode="json"
        )
        # Historical copies retain their source revision until matched to the library.
        converted.update(revision=old["revision"], updated_at=old["updated_at"])
        data["wealth"] = converted
    investments = [
        c for c in data["components"] if c["inputs"]["kind"] == "investments"
    ]
    if reserve is not None and not investments:
        message = "Choose an Investment scenario before moving the Plan's cash reserve."
        raise ValueError(message)
    for selection in investments:
        inputs = convert_investment_inputs(selection["inputs"])
        if reserve is not None:
            if inputs["reserved_cash"] is not None and Decimal(
                str(inputs["reserved_cash"])
            ) != Decimal(str(reserve)):
                message = "Plan and Investment cash reserves conflict."
                raise ValueError(message)
            inputs["reserved_cash"] = reserve
        selection["inputs"] = inputs
    return data


def _convert_wealth_and_planning_stage(
    wealth: list[dict[str, Any]],
    planning: list[dict[str, Any]],
    *,
    account_updates: dict[str, dict[str, Any]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Convert both stores while preserving historical source copies and revisions."""
    account_ids = {a["id"] for d in wealth for a in d["snapshot"]["accounts"]}
    account_ids.update(
        a["id"]
        for d in planning
        if d["inputs"]["kind"] == "plan" and d["inputs"].get("wealth")
        for a in d["inputs"]["wealth"]["snapshot"]["accounts"]
    )
    if not set(account_updates or {}) <= account_ids:
        message = "A reviewed account no longer exists."
        raise ValueError(message)
    converted_wealth = {
        d["id"]: convert_wealth_document(d, account_updates) for d in wealth
    }
    original_wealth = {d["id"]: d for d in wealth}
    converted = {}
    for old in planning:
        if old.get("schema_version") in {3, 4}:
            converted[old["id"]] = deepcopy(old)
            continue
        if (
            old.get("schema_version") != 2
            or old.get("document_type") != "planning_document"
        ):
            message = "Expected version-two Planning documents."
            raise ValueError(message)
        data = deepcopy(old["inputs"])
        if data["kind"] == "plan":
            continue
        if data["kind"] == "investments":
            data = convert_investment_inputs(data)
        converted[old["id"]] = old | {"schema_version": 3, "inputs": data}
    for old in planning:
        if old["inputs"]["kind"] != "plan" or old["schema_version"] in {3, 4}:
            continue
        data = convert_plan_inputs(old["inputs"], account_updates)
        source = old["inputs"].get("wealth")
        if source and original_wealth.get(source["id"]) == source:
            data["wealth"] = converted_wealth[source["id"]].model_dump(mode="json")
        for selection in data["components"]:
            current = converted.get(selection["source_id"])
            if current and current["inputs"] == selection["inputs"]:
                selection["source_revision"] = current["revision"] + 1
        converted[old["id"]] = old | {"schema_version": 3, "inputs": data}
    return {
        "wealth_snapshots": [
            d.model_dump(mode="json") for d in converted_wealth.values()
        ],
        "planning_documents": list(converted.values()),
    }


def convert_wealth_and_planning(
    wealth: list[dict[str, Any]],
    planning: list[dict[str, Any]],
    *,
    account_updates: dict[str, dict[str, Any]] | None = None,
    profile_contexts: dict[str, str | list[str]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Convert old Wealth and Planning through an explicit reviewed timeline stage."""
    from financial_model.planning_migration import convert_timeline_documents

    intermediate = _convert_wealth_and_planning_stage(
        wealth, planning, account_updates=account_updates
    )
    proposals = convert_timeline_documents(
        [d for d in intermediate["planning_documents"] if d["schema_version"] == 3],
        profile_contexts=profile_contexts,
    )
    return intermediate | {
        "planning_documents": [
            d for d in intermediate["planning_documents"] if d["schema_version"] == 4
        ]
        + [p.model_dump(mode="json") for p in proposals]
    }
