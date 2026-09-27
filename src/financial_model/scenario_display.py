"""Consistent scenario names and context for cards, selectors, and tables."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
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
    """Keep chosen names, context, transient status, and duplicate markers separate."""

    inputs: Scenario
    status: str | None = None
    duplicate: int | None = None

    @property
    def people(self) -> tuple[PersonInputs, ...]:
        """Keep person lines aligned even when scenarios entered adults differently."""
        return tuple(
            sorted(self.inputs.adults, key=lambda person: person.name.casefold())
        )

    @property
    def label(self) -> str:
        """Provide a compact selector label without altering the stored name."""
        suffix = f" ({self.duplicate})" if self.duplicate else ""
        employers = " / ".join(employer_label(p, compact=True) for p in self.people)
        parts = [
            self.inputs.name + suffix,
            self.inputs.state,
            str(self.inputs.tax_year),
            employers,
        ]
        if self.status:
            parts.append(self.status)
        return " · ".join(parts)

    def html(self, *, heading: bool = False) -> str:
        """Escape every user field in the shared name and metadata block."""
        duplicate = (
            f'<span class="fm-scenario-duplicate"> ({self.duplicate})</span>'
            if self.duplicate
            else ""
        )
        location = f"{STATE_NAMES[self.inputs.state]} · {self.inputs.tax_year}"
        people = "".join(
            f'<span class="fm-scenario-person">{escape(p.name)}: {escape(employer_label(p))}</span>'
            for p in self.people
        )
        status = (
            f'<span class="fm-scenario-status">{escape(self.status)}</span>'
            if self.status
            else ""
        )
        tag = "h3" if heading else "span"
        return (
            '<div class="fm-scenario">'
            f'<{tag} class="fm-scenario-name">{escape(self.inputs.name)}{duplicate}</{tag}>'
            f'<span class="fm-scenario-location">{escape(location)}</span>'
            f'<span class="fm-scenario-employers">{people}</span>{status}</div>'
        )


def scenario_displays(
    documents: tuple[SavedScenario, ...],
    statuses: tuple[str | None, ...] | None = None,
) -> tuple[ScenarioDisplay, ...]:
    """Number only identical visible identities, keeping distinct context intact."""
    displays = tuple(
        ScenarioDisplay(document.inputs, status)
        for document, status in zip(
            documents, statuses or (None,) * len(documents), strict=True
        )
    )
    counts = Counter(display.label for display in displays)
    seen: Counter[str] = Counter()
    result = []
    for display in displays:
        seen[display.label] += 1
        result.append(
            replace(display, duplicate=seen[display.label])
            if counts[display.label] > 1
            else display
        )
    return tuple(result)
