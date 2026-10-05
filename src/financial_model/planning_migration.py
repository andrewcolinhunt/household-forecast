"""Explicit, reviewed conversion of legacy planning documents to expense schedules."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from financial_model.planning import (
    INPUT_ADAPTER,
    Exploration,
    PlanningScenario,
    RetirementAssumptions,
    SavedPlanningDocument,
    SpendingAssumptions,
)
from financial_model.schemas import Amount, Money
from financial_model.wealth_migration import (
    convert_investment_inputs,
    convert_plan_inputs,
)


def _retirement_defaults(name: str, money_year: int | None) -> dict[str, Any]:
    """Supply old structural defaults only within the explicit conversion stage."""
    return RetirementAssumptions(name=name, money_year=money_year).model_dump(
        mode="json"
    ) | {
        "only_adult_1": {"mode": "unknown", "budget": None},
        "only_adult_2": {"mode": "unknown", "budget": None},
        "both_retired": {"amount": {"central": None, "lower": None, "upper": None}},
        "survivor": None,
    }


def _without_range_notes(value: Any) -> Any:
    """Remove the superseded scalar notes without touching financial values."""
    if isinstance(value, list):
        return [_without_range_notes(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _without_range_notes(item)
            for key, item in value.items()
            if key != "notes" or not {"central", "lower", "upper"} <= value.keys()
        }
    return value


def _spending_parts(
    inputs: dict[str, Any], indices: tuple[int, ...]
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Separate explicitly identified retirement rows; reject ambiguous schedules."""
    retained, retired = [], None
    rows = inputs.get("replacements", [])
    if any(i < 0 or i >= len(rows) for i in indices):
        msg = "A reviewed retirement row no longer exists."
        raise ValueError(msg)
    for i, row in enumerate(rows):
        when = row["when"]
        if i not in indices and when["kind"] == "year":
            retained.append(row)
            continue
        if when.get("offset", 0) or when["kind"] not in {"year", "both"}:
            msg = "Review retirement spending timing before migration; it cannot be moved unchanged."
            raise ValueError(msg)
        if when["kind"] == "year" and when["year"] is not None:
            msg = "A dated replacement cannot be silently retimed to retirement."
            raise ValueError(msg)
        if retired is not None:
            msg = "Review multiple retirement budgets before migration."
            raise ValueError(msg)
        retired = row["budget"]
    return retained, retired


def _profile(
    original: dict[str, Any], indices: tuple[int, ...], money_year: int | None
) -> dict[str, Any]:
    """Convert one profile, leaving retirement merging to the caller."""
    data = _without_range_notes(deepcopy(original))
    kind = data["kind"]
    if kind == "work":
        for adult in data["adults"]:
            adult.pop("retirement")
    elif kind == "spending":
        data["replacements"], _ = _spending_parts(data, indices)
        data.pop("survivor", None)
    elif kind == "economy":
        fees = data.pop("fees", None)
        data.pop("forecast_years", None)
        if data["return_basis"] != "nominal_compound_before_fees_and_taxes":
            msg = "Expected version-one returns before fees and taxes."
            raise ValueError(msg)
        data["return_basis"] = "nominal_compound_after_fees_before_tax"
        if fees is not None:
            for field in ("equities", "fixed_income", "cash"):
                for bound in ("central", "lower", "upper"):
                    if data[field][bound] is not None:
                        data[field][bound] = str(
                            (1 + Decimal(str(data[field][bound])))
                            * (1 - Decimal(str(fees)))
                            - 1
                        )
        else:
            data["notes"] = " ".join(
                filter(
                    None,
                    (
                        data.get("notes"),
                        "Rates kept unchanged; now interpreted after fees. Review if needed.",
                    ),
                )
            )
    elif kind == "investments":
        data = convert_investment_inputs(data)
    elif kind == "longevity":
        data["kind"] = "retirement"
        data["money_year"] = money_year
    return cast(dict[str, Any], data)


def _meaning(value: Any) -> Any:
    """Compare financial contents while excluding descriptions and provenance."""
    if isinstance(value, dict):
        if (
            "amount" in value
            and value["amount"].get("central") is None
            and value.get("essential") is None
        ):
            return None
        return {
            k: _meaning(v)
            for k, v in value.items()
            if k not in {"name", "notes", "source", "annual_source"}
        }
    if isinstance(value, list):
        return [_meaning(v) for v in value]
    return value


def _convert_version_one(
    originals: list[dict[str, Any]],
    *,
    retirement_rows: dict[str, tuple[int, ...]] | None = None,
    money_year: int | None = None,
) -> tuple[dict[str, Any], ...]:
    """Convert a reviewed library and its independent Plan copies.

    Args:
        originals: Unmodified version-one documents from the database.
        retirement_rows: Source IDs and reviewed row indices for undated retirement
            budgets. Relative both-retired rows are recognized structurally.
        money_year: Reviewed dollar reference year for old lifespan-only profiles.

    Returns:
        Valid current-version records, including new Retirement scenarios as needed.

    Raises:
        ValueError: For unknown versions, ambiguous timing or unreviewed people.
    """
    retirement_rows = retirement_rows or {}
    if any(
        d.get("schema_version") != 1 or d.get("document_type") != "planning_document"
        for d in originals
    ):
        msg = "Migration requires version-one planning documents."
        raise ValueError(msg)
    converted: dict[str, dict[str, Any]] = {}
    for old in originals:
        if old["inputs"]["kind"] != "plan":
            inputs = _profile(
                old["inputs"], retirement_rows.get(old["id"], ()), money_year
            )
            converted[old["id"]] = old | {"schema_version": 3, "inputs": inputs}

    def keep_retirement(data: dict[str, Any], origin: dict[str, Any]) -> dict[str, Any]:
        candidate = data
        for record in converted.values():
            # Copied adult order must match even when financial values coincide.
            if (
                record["inputs"]["kind"] == "retirement"
                and _meaning(record["inputs"]) == _meaning(candidate)
                and tuple(a["name"] for a in record["inputs"]["adults"])
                == tuple(a["name"] for a in candidate["adults"])
            ):
                return record
        record = origin | {
            "id": str(uuid5(UUID(origin["id"]), "retirement-v2")),
            "schema_version": 3,
            "revision": 0,
            "inputs": candidate,
        }
        converted[record["id"]] = record
        return record

    # Standalone work/spending values need a new home even if no Plan chose them.
    for old in originals:
        raw = _without_range_notes(old["inputs"])
        kind = raw["kind"]
        if kind not in {"work", "spending"}:
            continue
        data = _retirement_defaults(
            f"Retirement — {raw['name']}"[:80], raw.get("money_year")
        )
        present = False
        if kind == "work":
            for target, source in zip(data["adults"], raw["adults"], strict=True):
                target["name"] = source["name"]
                target["retirement"] = source["retirement"]
                present |= source["retirement"]["central"] is not None
        else:
            _, budget = _spending_parts(raw, retirement_rows.get(old["id"], ()))
            if budget is not None:
                data["both_retired"] = budget
                present |= (
                    budget["amount"]["central"] is not None
                    or budget.get("essential") is not None
                )
            data["survivor"] = raw.get("survivor")
            present |= _meaning(data["survivor"]) is not None
        if present:
            keep_retirement(data, old)

    for old in originals:
        if old["inputs"]["kind"] != "plan":
            continue
        plan = _without_range_notes(deepcopy(old["inputs"]))
        sources = {c["inputs"]["kind"]: c for c in plan["components"]}
        work_mapping = plan.pop("work_adults")
        life_mapping = plan.pop("longevity_adults")
        work = sources.get("work")
        life = sources.get("longevity")
        spending = sources.get("spending")
        retirement = (
            _profile(life["inputs"], (), money_year)
            if life
            else _retirement_defaults(f"Retirement — {plan['name']}"[:80], money_year)
        )
        mapping = life_mapping if life else work_mapping
        if work:
            if not life:
                for target, source in zip(
                    retirement["adults"], work["inputs"]["adults"], strict=True
                ):
                    target["name"] = source["name"]
            for i, adult in enumerate(work["inputs"]["adults"]):
                timing = adult["retirement"]
                if timing["central"] is None:
                    continue
                if work_mapping is None or mapping is None:
                    msg = "Review person mappings before moving retirement dates."
                    raise ValueError(msg)
                retirement["adults"][mapping.index(work_mapping[i])]["retirement"] = (
                    timing
                )
        if spending:
            budget_source = spending["inputs"]
            _, budget = _spending_parts(
                budget_source, retirement_rows.get(spending["source_id"], ())
            )
            if budget is not None:
                retirement["both_retired"] = budget
            retirement["survivor"] = budget_source.get("survivor")
            retirement["money_year"] = budget_source.get("money_year")
        new_components = []
        for source in plan["components"]:
            kind = source["inputs"]["kind"]
            if kind == "longevity":
                continue
            inputs = _profile(
                source["inputs"],
                retirement_rows.get(source["source_id"], ()),
                money_year,
            )
            # Preserve historical source revisions when the embedded copy differs.
            current = converted.get(source["source_id"])
            same = current is not None and current["inputs"] == inputs
            new_components.append(
                source
                | {
                    "inputs": inputs,
                    "source_revision": current["revision"] + 1
                    if same and current is not None
                    else source["source_revision"],
                    "adult_mapping": work_mapping if kind == "work" else None,
                }
            )
        if life or work or spending:
            record = keep_retirement(retirement, old)
            new_components.append(
                {
                    "source_id": record["id"],
                    "source_revision": record["revision"] + 1,
                    "source_name": record["inputs"]["name"],
                    "inputs": retirement,
                    "adult_mapping": mapping,
                }
            )
        plan["components"] = new_components
        converted[old["id"]] = old | {
            "schema_version": 3,
            "inputs": convert_plan_inputs(plan),
        }
    return tuple(converted.values())


_RETIREMENT_BUDGETS = ("only_adult_1", "only_adult_2", "both_retired", "survivor")
TIMELINE_CONVERSION_NOTICE = (
    "Converted using the reviewed Plan's fixed calendar dates and planning horizon. "
    "Retirement and age changes will not move these dates; review links explicitly. "
    "These costs STOP at the old planning horizon; extend them when testing longer lives. "
    "Original budgets remain in the backup; descriptions and essential-spending "
    "estimates are also retained in the household spending notes."
)


def _plan_selections(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Require one reviewed selection per kind, never pick a Cartesian combination."""
    selections = {}
    for selection in plan.get("components", []):
        kind = selection["inputs"]["kind"]
        if kind in selections:
            msg = "Choose one reviewed profile combination before conversion."
            raise ValueError(msg)
        selections[kind] = selection
    if not {"spending", "retirement"} <= selections.keys():
        msg = "Conversion needs a Plan with Spending and Retirement selected."
        raise ValueError(msg)
    return selections


def _legacy_calendar(
    plan: dict[str, Any],
) -> tuple[int, int, tuple[int, int], tuple[int, int], tuple[int, int]]:
    """Resolve the same annual retirement, survival, and opening boundaries as v3."""
    selections = _plan_selections(plan)
    retirement = RetirementAssumptions.model_validate(
        _strip_retirement_budgets(selections["retirement"]["inputs"])
    ).model_dump(mode="python")
    mapping = selections["retirement"].get("adult_mapping")
    household = plan.get("household", {})
    reference = household.get("reference_year")
    wealth = plan.get("wealth")
    if retirement.get("mode", "ages") != "ages" or not wealth or reference is None:
        msg = "Conversion needs fixed lifespans, household ages and a Wealth date."
        raise ValueError(msg)
    if mapping is None or sorted(mapping) != [1, 2]:
        msg = "Review Retirement person mappings before conversion."
        raise ValueError(msg)
    retirements, ends = [0, 0], [0, 0]
    for adult, slot in zip(retirement["adults"], mapping, strict=True):
        age = household["adults"][slot - 1].get("age")
        horizon = adult.get("age", {}).get("central")
        year = adult.get("retirement", {}).get("central")
        if age is None or horizon is None or year is None:
            msg = "Conversion needs resolved retirement dates and planning ages."
            raise ValueError(msg)
        retirements[slot - 1], ends[slot - 1] = year, reference + horizon - age
    start = date.fromisoformat(wealth["snapshot"]["as_of"]).year
    if max(ends) < start:
        msg = "The reviewed Plan's horizon precedes its opening snapshot."
        raise ValueError(msg)
    return (
        start,
        max(ends) + 1,
        (retirements[0], retirements[1]),
        (ends[0], ends[1]),
        (mapping[0], mapping[1]),
    )


def _legacy_year(anchor: dict[str, Any], retirements: tuple[int, int]) -> int:
    """Resolve old supported anchors without inventing missing dates."""
    kind = anchor.get("kind", "year")
    if kind == "year":
        if anchor.get("year") is None:
            msg = "Review an unresolved expense date before conversion."
            raise ValueError(msg)
        return int(anchor["year"])
    if kind not in {"adult_1", "adult_2", "both"}:
        msg = f"Unsupported legacy date reference: {kind}."
        raise ValueError(msg)
    base = max(retirements) if kind == "both" else retirements[int(kind[-1]) - 1]
    return base + int(anchor.get("offset", 0))


def _sum_amounts(amounts: list[dict[str, Any]]) -> dict[str, str | None]:
    """Preserve central totals and entered additive bounds, rejecting negative costs."""
    if any(amount.get("central") is None for amount in amounts):
        msg = "Review unknown effective spending amounts before conversion."
        raise ValueError(msg)
    result: dict[str, str | None] = {}
    for bound in ("central", "lower", "upper"):
        if bound != "central" and not any(a.get(bound) is not None for a in amounts):
            result[bound] = None
            continue
        total = sum(
            (
                Decimal(str(a[bound] if a.get(bound) is not None else a["central"]))
                for a in amounts
            ),
            Decimal(0),
        )
        if total < 0:
            msg = "Negative net recurring or one-time costs need review before conversion."
            raise ValueError(msg)
        result[bound] = str(total)
    return result


def _legacy_spending(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Evaluate old precedence, keeping recurring and one-time amounts separate."""
    selections = _plan_selections(plan)
    spending, retirement = (selections[k]["inputs"] for k in ("spending", "retirement"))
    _validate_legacy_budgets(spending, retirement)
    start, end, retirements, ends, mapping = _legacy_calendar(plan)
    replacements = [
        (_legacy_year(r["when"], retirements), r["budget"])
        for r in spending.get("replacements", [])
    ]
    if len({year for year, _ in replacements}) != len(replacements):
        msg = "Conflicting budget replacements resolve to the same year; review them."
        raise ValueError(msg)
    replacements.sort(key=lambda item: item[0])
    adjustments = [
        (
            a,
            _legacy_year(a["start"], retirements),
            _legacy_year(a["end"], retirements) if a.get("end") else None,
        )
        for a in spending.get("adjustments", [])
    ]
    if any(stop is not None and stop <= begin for _, begin, stop in adjustments):
        msg = "An adjustment resolves to an empty or reversed interval."
        raise ValueError(msg)
    rows = []
    for year in range(start, end):
        budget = spending.get("baseline")
        for when, replacement in replacements:
            if when <= year:
                budget = replacement
        alive, retired = (
            [year <= last for last in ends],
            [year >= r for r in retirements],
        )
        if sum(alive) == 1:
            budget = retirement.get("survivor")
        elif all(retired):
            budget = retirement.get("both_retired")
        elif any(retired):
            slot = mapping.index(retired.index(True) + 1) + 1
            choice = retirement.get(f"only_adult_{slot}", {})
            if choice.get("mode") not in {"continue", "replace"}:
                msg = "Review the one-adult-retired budget before conversion."
                raise ValueError(msg)
            if choice["mode"] == "replace":
                budget = choice.get("budget")
        if not budget:
            msg = f"The effective household budget in {year} is unknown."
            raise ValueError(msg)
        recurring, once = [budget["amount"]], []
        for adjustment, begin, stop in adjustments:
            if adjustment.get("frequency", "recurring") == "once":
                if year == begin:
                    once.append(adjustment["amount"])
            elif begin <= year and (stop is None or year < stop):
                recurring.append(adjustment["amount"])
        rows.append(
            {
                "year": year,
                "recurring": _sum_amounts(recurring),
                "once": _sum_amounts(once),
            }
        )
    return rows


def _validate_legacy_budgets(
    spending: dict[str, Any], retirement: dict[str, Any]
) -> None:
    """Do not turn incomplete or invalid legacy budgets into apparently valid costs."""
    if spending.get("money_year") is None or spending.get(
        "money_year"
    ) != retirement.get("money_year"):
        msg = "Review the common Spending and Retirement dollar reference year."
        raise ValueError(msg)
    budgets = [
        spending.get("baseline"),
        retirement.get("both_retired"),
        retirement.get("survivor"),
    ]
    budgets.extend(row.get("budget") for row in spending.get("replacements", []))
    for key in ("only_adult_1", "only_adult_2"):
        choice = retirement.get(key, {})
        if choice.get("mode") not in {"continue", "replace"}:
            msg = "Review both one-adult-retired budget choices before conversion."
            raise ValueError(msg)
        if choice["mode"] == "replace":
            budgets.append(choice.get("budget"))
        elif choice.get("budget") is not None:
            msg = "A continuing retirement budget cannot contain a replacement."
            raise ValueError(msg)
    for budget in budgets:
        if not budget or budget.get("amount", {}).get("central") is None:
            msg = "Review all legacy budget amounts, including overridden budgets."
            raise ValueError(msg)
        amount = Exploration[Money].model_validate(budget["amount"])
        assert amount.central is not None
        if (
            budget.get("essential") is not None
            and not 0 <= Decimal(str(budget["essential"])) <= amount.central
        ):
            msg = "An old essential-spending estimate exceeds its household budget."
            raise ValueError(msg)
    for adjustment in spending.get("adjustments", []):
        amount = Exploration[Amount].model_validate(adjustment["amount"])
        if amount.central is None:
            msg = "Review all legacy adjustment amounts before conversion."
            raise ValueError(msg)


def _undated_adjustments(
    spending: dict[str, Any], identity: str
) -> list[dict[str, Any]]:
    """Retain narrowly supported unfinished additions outside the resolved budget."""
    retained, expenses = [], []
    for index, adjustment in enumerate(spending.get("adjustments", [])):
        anchors = (adjustment["start"], adjustment.get("end"))
        unknown = [
            bool(
                a is not None
                and a.get("kind", "year") == "year"
                and a.get("year") is None
            )
            for a in anchors
        ]
        if not any(unknown):
            retained.append(adjustment)
            continue
        amount = Exploration[Money].model_validate(adjustment["amount"])
        if (
            spending.get("replacements")
            or not all(unknown)
            or adjustment.get("frequency", "recurring") != "recurring"
            or amount.central is None
            or amount.central <= 0
        ):
            msg = "Only positive recurring additions with both calendar dates unknown and no budget replacements can remain undated."
            raise ValueError(msg)
        expenses.append(
            {
                "id": str(uuid5(UUID(identity), f"undated-adjustment-v4-{index}")),
                "label": adjustment["label"],
                "category": "other",
                "frequency": "recurring",
                "levels": [
                    {"when": adjustment["start"], "amount": adjustment["amount"]}
                ],
                "end": adjustment["end"],
                "notes": adjustment.get("notes", ""),
            }
        )
    spending["adjustments"] = retained
    return expenses


def _legacy_spending_details(
    spending: dict[str, Any], retirement: dict[str, Any]
) -> str:
    """Keep original budget explanations visible as historical, non-calculated context."""
    sections = [("Baseline budget", spending.get("baseline"))]
    sections.extend(
        (f"Replacement — {row['label']}", row["budget"])
        for row in spending.get("replacements", [])
    )
    sections.extend(
        (f"Adjustment — {row['label']}", row) for row in spending.get("adjustments", [])
    )
    sections.extend(
        (
            f"Retirement budget — only {adult['name']} retired",
            retirement.get(f"only_adult_{index}", {}).get("budget"),
        )
        for index, adult in enumerate(retirement["adults"], 1)
    )
    sections.extend(
        (label, retirement.get(key))
        for label, key in (
            ("Both-retired budget", "both_retired"),
            ("Survivor budget", "survivor"),
        )
    )
    lines = ["Historical budget context (before conversion; not additional costs):"]
    for label, source in sections:
        if source is None:
            continue
        lines.append(f"{label}:")
        if source.get("notes"):
            lines.append(source["notes"])
        if source.get("essential") is not None:
            lines.append(
                f"Essential annual spending ({spending['money_year']} dollars): {source['essential']}."
            )
    return "\n".join(lines)


def _converted_spending(
    plan: dict[str, Any], identity: str, *, preserve_undated: bool = False
) -> dict[str, Any]:
    """Create fixed dated levels that reproduce the reviewed Plan's old consumption."""
    plan = deepcopy(plan)
    selections = _plan_selections(plan)
    old = selections["spending"]["inputs"]
    details = _legacy_spending_details(old, selections["retirement"]["inputs"])
    undated = _undated_adjustments(old, identity) if preserve_undated else []
    rows = _legacy_spending(plan)
    levels, previous = [], None
    for row in rows:
        if row["recurring"] != previous:
            levels.append(
                {
                    "when": {"kind": "year", "year": row["year"]},
                    "amount": row["recurring"],
                }
            )
            previous = row["recurring"]
    expenses = [
        {
            "id": str(uuid5(UUID(identity), "recurring-expenses-v4")),
            "label": "Household spending (fixed dates)",
            "category": "ordinary",
            "frequency": "recurring",
            "levels": levels,
            "end": {"kind": "year", "year": rows[-1]["year"] + 1},
            "notes": TIMELINE_CONVERSION_NOTICE + "\n\n" + details,
        }
    ]
    for row in rows:
        if any(
            value is not None and Decimal(value) != 0 for value in row["once"].values()
        ):
            expenses.append(
                {
                    "id": str(uuid5(UUID(identity), f"once-expenses-v4-{row['year']}")),
                    "label": f"One-time spending {row['year']}",
                    "category": "other",
                    "frequency": "once",
                    "levels": [
                        {
                            "when": {"kind": "year", "year": row["year"]},
                            "amount": row["once"],
                        }
                    ],
                    "notes": TIMELINE_CONVERSION_NOTICE,
                }
            )
    return {
        k: v
        for k, v in old.items()
        if k not in {"baseline", "replacements", "adjustments"}
    } | {"expenses": expenses + undated}


def _context_spending(
    original: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    """Resolve a library profile within one explicitly reviewed Plan copy."""
    data = deepcopy(original["inputs"])
    plan = deepcopy(context["inputs"])
    selection = _plan_selections(plan)[data["kind"]]
    if data["kind"] == "retirement" and [
        a["name"] for a in selection["inputs"]["adults"]
    ] != [a["name"] for a in data["adults"]]:
        msg = "Retirement adult order differs from the Plan context; review its person mapping."
        raise ValueError(msg)
    selection["inputs"] = data
    return _converted_spending(
        plan, original["id"], preserve_undated=data["kind"] == "spending"
    )


def _retirement_expenses(
    original: dict[str, Any], contexts: list[dict[str, Any]]
) -> list[SavedPlanningDocument]:
    """Keep distinct full household alternatives with deterministic context identities."""
    alternatives: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for context in sorted(contexts, key=lambda c: c["id"]):
        spending = _context_spending(original, context)
        if not any(
            (previous["money_year"], _meaning(previous["expenses"]))
            == (spending["money_year"], _meaning(spending["expenses"]))
            for _, previous in alternatives
        ):
            alternatives.append((context, spending))
    converted = []
    for context, spending in alternatives:
        suffix = f"-{context['id']}" if len(alternatives) > 1 else ""
        identity = uuid5(UUID(original["id"]), f"retirement-expenses-v4{suffix}")
        name = original["inputs"]["name"]
        spending["name"] = f"Expenses — {name}"[:80]
        if len(alternatives) > 1:
            context_name = context["inputs"]["name"]
            spending["name"] = f"Household spending — {context_name}"[:80]
            spending["notes"] = "\n".join(
                filter(
                    None,
                    (
                        spending.get("notes"),
                        f"Full household spending alternative for Plan {context_name} ({context['id']}), using Retirement {name} ({original['id']}).",
                    ),
                )
            )
            for expense in spending["expenses"]:
                expense["id"] = str(uuid5(identity, expense["id"]))
        converted.append(
            SavedPlanningDocument(
                id=identity, inputs=INPUT_ADAPTER.validate_python(spending)
            )
        )
    return converted


def _has_retirement_budgets(inputs: dict[str, Any]) -> bool:
    """Detect amounts that must be preserved outside the new Retirement profile."""
    return any(
        _meaning(inputs.get(key))
        not in (
            None,
            {"mode": "unknown", "budget": None},
            {"mode": "continue", "budget": None},
        )
        for key in _RETIREMENT_BUDGETS
    )


def _strip_retirement_budgets(inputs: dict[str, Any]) -> dict[str, Any]:
    """Remove obsolete spending controls after their explicit conversion."""
    return {
        key: value for key, value in inputs.items() if key not in _RETIREMENT_BUDGETS
    }


def _child_identities(plan: dict[str, Any]) -> None:
    """Give equivalent legacy household copies reproducible child references."""
    occurrences: dict[str, int] = {}
    for child in plan.get("household", {}).get("children", []):
        key = f"{child.get('name')}:{child.get('birth_year')}"
        count = occurrences.get(key, 0)
        occurrences[key] = count + 1
        child.setdefault(
            "id",
            str(uuid5(NAMESPACE_URL, f"financial-model/legacy-child/{key}/{count}")),
        )


def convert_timeline_documents(
    originals: list[dict[str, Any]],
    *,
    profile_contexts: dict[str, str | list[str]] | None = None,
) -> tuple[SavedPlanningDocument, ...]:
    """Convert reviewed v3 documents; standalone spending needs an explicit Plan.

    Args:
        originals: Unchanged version-three documents retained in the backup.
        profile_contexts: Spending or Retirement document IDs mapped to a reviewed
            Plan ID whose other inputs supply the required timing and budgets.
            Retirement may instead name a list of reviewed Plan IDs to preserve
            each distinct full household spending alternative.

    Returns:
        Current documents, plus separate Spending profiles preserving retirement
        budgets. Plan copies stay independent from the converted library.

    Raises:
        ValueError: If context, effective amounts, person mappings or dates are
            unresolved, or a signed old amount cannot become a nonnegative cost.
    """
    if any(
        d.get("schema_version") != 3 or d.get("document_type") != "planning_document"
        for d in originals
    ):
        msg = "Timeline conversion requires version-three planning documents."
        raise ValueError(msg)
    contexts, by_id = profile_contexts or {}, {d["id"]: d for d in originals}
    if len(by_id) != len(originals) or not set(contexts) <= by_id.keys():
        msg = "Review duplicate document IDs or unknown profile context IDs."
        raise ValueError(msg)
    converted = []
    for original in originals:
        data = deepcopy(original["inputs"])
        kind = data["kind"]
        if kind == "plan":
            kinds = {c["inputs"]["kind"] for c in data.get("components", [])}
            if kinds & {"spending", "retirement"}:
                selections = _plan_selections(data)
                selections["spending"]["inputs"] = _converted_spending(
                    data, original["id"]
                )
                selections["retirement"]["inputs"] = _strip_retirement_budgets(
                    selections["retirement"]["inputs"]
                )
            _child_identities(data)
        elif kind == "spending" or (
            kind == "retirement" and _has_retirement_budgets(data)
        ):
            requested = contexts.get(original["id"], "")
            context_ids = requested if isinstance(requested, list) else [requested]
            if (
                not context_ids
                or len(context_ids) != len(set(context_ids))
                or any(
                    by_id.get(i, {}).get("inputs", {}).get("kind") != "plan"
                    for i in context_ids
                )
            ):
                msg = f"Review a Plan context for {kind} profile {original['id']} before conversion."
                raise ValueError(msg)
            if kind == "spending":
                if len(context_ids) != 1:
                    msg = "A Spending profile needs one reviewed Plan context."
                    raise ValueError(msg)
                data = _context_spending(original, by_id[context_ids[0]])
            else:
                converted.extend(
                    _retirement_expenses(original, [by_id[i] for i in context_ids])
                )
                data = _strip_retirement_budgets(data)
        elif kind == "retirement":
            data = _strip_retirement_budgets(data)
        converted.append(
            SavedPlanningDocument(
                id=original["id"],
                revision=original["revision"] + 1,
                inputs=INPUT_ADAPTER.validate_python(data),
            )
        )
    return tuple(converted)


def convert_planning_documents(
    originals: list[dict[str, Any]],
    *,
    retirement_rows: dict[str, tuple[int, ...]] | None = None,
    money_year: int | None = None,
    profile_contexts: dict[str, str | list[str]] | None = None,
) -> tuple[SavedPlanningDocument, ...]:
    """Convert v1 or v3 inputs explicitly, with reviewed context for legacy budgets."""
    intermediate = (
        originals
        if all(d.get("schema_version") == 3 for d in originals)
        else _convert_version_one(
            originals, retirement_rows=retirement_rows, money_year=money_year
        )
    )
    return convert_timeline_documents(
        list(intermediate), profile_contexts=profile_contexts
    )


def timeline_conversion_review(
    originals: list[dict[str, Any]], proposals: tuple[SavedPlanningDocument, ...]
) -> list[dict[str, Any]]:
    """Reconcile old and new annual consumption, including opening-year proration."""
    proposed = {str(p.id): p for p in proposals}
    review = []
    for old in originals:
        if old["inputs"]["kind"] != "plan":
            continue
        if not any(
            c["inputs"]["kind"] == "spending"
            for c in old["inputs"].get("components", [])
        ):
            continue
        rows = _legacy_spending(old["inputs"])
        new = proposed[old["id"]].inputs
        assert isinstance(new, PlanningScenario)
        spending = next(c.inputs for c in new.components if c.inputs.kind == "spending")
        assert isinstance(spending, SpendingAssumptions)
        _, _, retirements, ends, _ = _legacy_calendar(old["inputs"])
        as_of = date.fromisoformat(old["inputs"]["wealth"]["snapshot"]["as_of"])
        for row in rows:
            year = row["year"]
            fraction = Decimal(1)
            if year == as_of.year:
                fraction = Decimal(
                    str(
                        (date(year + 1, 1, 1) - as_of).days
                        / (date(year + 1, 1, 1) - date(year, 1, 1)).days
                    )
                )
            from financial_model.forecast_timeline import annual_expenses

            items = annual_expenses(spending, year, retirements, new.household, ends)
            recurring = sum(
                (item.amount for item in items if item.frequency == "recurring"),
                Decimal(0),
            )
            once = sum(
                (item.amount for item in items if item.frequency == "once"), Decimal(0)
            )
            old_recurring, old_once = (
                Decimal(row["recurring"]["central"]),
                Decimal(row["once"]["central"]),
            )
            if (recurring, once) != (old_recurring, old_once):
                msg = f"Converted expenses do not reconcile in {year}."
                raise ValueError(msg)
            review.append(
                {
                    "plan_id": old["id"],
                    "plan": new.name,
                    "year": year,
                    "year_fraction": str(fraction),
                    "old_recurring": str(old_recurring),
                    "new_recurring": str(recurring),
                    "old_one_time": str(old_once),
                    "new_one_time": str(once),
                    "old_total": str(old_recurring * fraction + old_once),
                    "new_total": str(recurring * fraction + once),
                }
            )
    return review
