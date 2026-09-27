"""Consistent scenario titles and distinguishing context for the app."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import UTC
from decimal import Decimal
from html import escape

from financial_model.schemas import PersonInputs, SavedScenario, Scenario

STATE_NAMES = {"CA": "California", "WA": "Washington"}


def employer_label(person: PersonInputs, *, compact: bool = False) -> str:
    """Distinguish missing information from explicitly reported nonemployment."""
    if person.not_working:
        return "Not working"
    return person.employer or ("Not entered" if compact else "Employer not entered")


@dataclass(frozen=True)
class ScenarioDisplay:
    """Separate an optional label, automatic title, context, and session status."""

    inputs: Scenario
    status: str | None = None
    differences: tuple[str, ...] = ()
    record_label: str | None = None

    @property
    def people(self) -> tuple[PersonInputs, ...]:
        """Align people even when scenarios entered adults in different orders."""
        return tuple(
            sorted(self.inputs.adults, key=lambda person: person.name.casefold())
        )

    @property
    def title(self) -> str:
        """Use the decision label, falling back to location without a work category."""
        return self.inputs.name or STATE_NAMES[self.inputs.state]

    @property
    def location(self) -> str:
        """Avoid repeating the state when the automatic title already contains it."""
        year = str(self.inputs.tax_year)
        return (
            f"{STATE_NAMES[self.inputs.state]} · {year}" if self.inputs.name else year
        )

    @property
    def label(self) -> str:
        """Provide a selector label using the same facts as the full heading."""
        parts = [self.title]
        if self.inputs.name:
            parts.append(self.inputs.state)
        parts.extend(
            (
                str(self.inputs.tax_year),
                " / ".join(employer_label(p, compact=True) for p in self.people),
            )
        )
        parts.extend(self.differences)
        if self.record_label:
            parts.append(self.record_label)
        if self.status:
            parts.append(self.status)
        return " · ".join(parts)

    def html(self, *, heading: bool = False) -> str:
        """Escape user fields in the shared title and condensed details block."""
        people = "".join(
            f'<span class="fm-scenario-person">{escape(p.name)}: {escape(employer_label(p))}</span>'
            for p in self.people
        )
        details = "".join(
            f'<span class="fm-scenario-person">{escape(value)}</span>'
            for value in self.differences
        )
        record = (
            '<details class="fm-scenario-record"><summary>Saved details</summary>'
            f"<span>{escape(self.record_label)}</span></details>"
            if self.record_label
            else ""
        )
        status = (
            f'<span class="fm-scenario-status">{escape(self.status)}</span>'
            if self.status
            else ""
        )
        tag = "h3" if heading else "span"
        return (
            '<div class="fm-scenario">'
            f'<{tag} class="fm-scenario-name">{escape(self.title)}</{tag}>'
            f'<span class="fm-scenario-location">{escape(self.location)}</span>'
            f'<span class="fm-scenario-employers">{people}</span>'
            f'<span class="fm-scenario-differences">{details}</span>{record}{status}</div>'
        )


def _number(value: Decimal | int) -> str:
    """Keep meaningful decimal places so small differences remain visible."""
    whole, dot, fraction = format(Decimal(value), ",f").partition(".")
    return whole + (dot + fraction.rstrip("0") if fraction.rstrip("0") else "")


def _assumptions(inputs: Scenario) -> dict[str, str]:
    """Describe every variable assumption with its person and units."""
    costs = inputs.living_costs
    facts = {
        "Rent": f"${_number(costs.rent)}/mo",
        "Childcare": f"${_number(costs.childcare)}/mo",
        "Other living costs": f"${_number(costs.other)}/mo",
        "Eligible childcare": f"${_number(costs.eligible_care_expense)}/yr",
        "CA renter credit eligible": "Yes" if costs.ca_renter_credit_eligible else "No",
        "Children's ages": ", ".join(
            str(child.age)
            for child in sorted(inputs.dependents, key=lambda child: child.age)
        )
        or "None",
    }
    for person in ScenarioDisplay(inputs).people:
        values = {
            "Age": str(person.age),
            "WA Cares exempt": "Yes" if person.wa_cares_exempt else "No",
        }
        for field, label in (
            ("cash_pay", "Salary + bonus"),
            ("rsu_vesting", "RSUs"),
            ("employee_401k", "401(k)"),
        ):
            values[label] = f"${_number(getattr(person, field))}/yr"
        for field, label in (
            ("savings_rate", "Saving goal"),
            ("employer_retirement_rate", "Employer retirement"),
        ):
            values[label] = f"{_number(100 * getattr(person, field))}%"
        values.update(
            {
                "ISOs exercised": _number(person.options.shares),
                "ISO exercise price": f"${_number(person.options.strike)}/share",
                "ISO exercise FMV": f"${_number(person.options.fair_market_value)}/share",
            }
        )
        award = person.option_award
        values.update(
            {
                "Option award": award.kind if award else "None",
                "Options vesting": f"{_number(award.vesting_shares)}/yr"
                if award
                else "None",
                "Award exercise price": f"${_number(award.strike)}/share"
                if award
                else "None",
                "Assumed share value": f"${_number(award.assumed_share_value)}/share"
                if award and award.assumed_share_value is not None
                else "Not entered",
            }
        )
        facts.update(
            {f"{person.name} · {label}": value for label, value in values.items()}
        )
    return facts


def _distinguishing_fields(facts: list[dict[str, str]]) -> list[str]:
    """Choose only fields needed to distinguish each unique set of assumptions."""
    selected: list[str] = []
    for field in dict.fromkeys(key for values in facts for key in values):
        before = {tuple(values.get(key) for key in selected) for values in facts}
        after = {
            tuple(values.get(key) for key in [*selected, field]) for values in facts
        }
        if len(after) > len(before):
            selected.append(field)
    return selected


def _record_label(document: SavedScenario) -> str:
    """Use an actual saved timestamp; older records have no creation timestamp."""
    date = document.updated_at.astimezone(UTC).strftime("%d %b %Y, %H:%M:%S UTC")
    return f"Saved {date}"


def scenario_displays(
    documents: tuple[SavedScenario, ...],
    statuses: tuple[str | None, ...] | None = None,
) -> tuple[ScenarioDisplay, ...]:
    """Distinguish matching headings using assumptions, keeping titles stable."""
    displays = [
        ScenarioDisplay(document.inputs, status)
        for document, status in zip(
            documents, statuses or (None,) * len(documents), strict=True
        )
    ]
    groups: dict[str, list[int]] = defaultdict(list)
    for index, display in enumerate(displays):
        # A draft's status is already visible, but its changed inputs are useful too.
        groups[replace(display, status=None).label].append(index)
    for indices in groups.values():
        if len(indices) < 2:
            continue
        facts = [_assumptions(documents[index].inputs) for index in indices]
        fields = _distinguishing_fields(facts)
        signatures = [tuple(values.get(field) for field in fields) for values in facts]
        identities = [
            (signature, displays[index].status)
            for index, signature in zip(indices, signatures, strict=True)
        ]
        record_labels = [
            (*identity, _record_label(documents[index]))
            for index, identity in zip(indices, identities, strict=True)
        ]
        for index, values, signature in zip(indices, facts, signatures, strict=True):
            identical = signatures.count(signature) > 1
            details = tuple(
                f"{field}: {values.get(field, 'Not entered')}" for field in fields
            )
            record_label = _record_label(documents[index])
            if (
                record_labels.count((signature, displays[index].status, record_label))
                > 1
            ):
                record_label += f" · {str(documents[index].id)[:8]}"
            displays[index] = replace(
                displays[index],
                differences=details + (("Same assumptions",) if identical else ()),
                record_label=record_label
                if identities.count((signature, displays[index].status)) > 1
                else None,
            )
    return tuple(displays)
