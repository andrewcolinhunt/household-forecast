"""Explicit conversion of version-one planning documents to retirement scenarios."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid5

from financial_model.planning import (
    INPUT_ADAPTER,
    RetirementAssumptions,
    SavedPlanningDocument,
)
from financial_model.wealth_migration import (
    convert_investment_inputs,
    convert_plan_inputs,
)


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
    return INPUT_ADAPTER.validate_python(data).model_dump(mode="json")


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


def convert_planning_documents(
    originals: list[dict[str, Any]],
    *,
    retirement_rows: dict[str, tuple[int, ...]] | None = None,
    money_year: int | None = None,
) -> tuple[SavedPlanningDocument, ...]:
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
    converted: dict[str, SavedPlanningDocument] = {}
    for old in originals:
        if old["inputs"]["kind"] != "plan":
            inputs = _profile(
                old["inputs"], retirement_rows.get(old["id"], ()), money_year
            )
            converted[old["id"]] = SavedPlanningDocument(
                id=old["id"],
                revision=old["revision"] + 1,
                inputs=INPUT_ADAPTER.validate_python(inputs),
            )

    def keep_retirement(
        data: dict[str, Any], origin: dict[str, Any]
    ) -> SavedPlanningDocument:
        candidate = RetirementAssumptions.model_validate(data)
        for record in converted.values():
            # Copied adult order must match even when financial values coincide.
            if (
                isinstance(record.inputs, RetirementAssumptions)
                and _meaning(record.inputs.model_dump(mode="json"))
                == _meaning(candidate.model_dump(mode="json"))
                and tuple(a.name for a in record.inputs.adults)
                == tuple(a.name for a in candidate.adults)
            ):
                return record
        record = SavedPlanningDocument(
            id=uuid5(UUID(origin["id"]), "retirement-v2"), inputs=candidate
        )
        converted[str(record.id)] = record
        return record

    # Standalone work/spending values need a new home even if no Plan chose them.
    for old in originals:
        raw = _without_range_notes(old["inputs"])
        kind = raw["kind"]
        if kind not in {"work", "spending"}:
            continue
        data = RetirementAssumptions(
            name=f"Retirement — {raw['name']}"[:80], money_year=raw.get("money_year")
        ).model_dump(mode="json")
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
            else RetirementAssumptions(
                name=f"Retirement — {plan['name']}"[:80], money_year=money_year
            ).model_dump(mode="json")
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
            same = (
                current is not None and current.inputs.model_dump(mode="json") == inputs
            )
            new_components.append(
                source
                | {
                    "inputs": inputs,
                    "source_revision": current.revision
                    if same and current is not None
                    else source["source_revision"],
                    "adult_mapping": work_mapping if kind == "work" else None,
                }
            )
        if life or work or spending:
            record = keep_retirement(retirement, old)
            new_components.append(
                {
                    "source_id": str(record.id),
                    "source_revision": record.revision,
                    "source_name": record.inputs.name,
                    "inputs": retirement,
                    "adult_mapping": mapping,
                }
            )
        plan["components"] = new_components
        converted[old["id"]] = SavedPlanningDocument(
            id=old["id"],
            revision=old["revision"] + 1,
            inputs=INPUT_ADAPTER.validate_python(convert_plan_inputs(plan)),
        )
    return tuple(converted.values())
