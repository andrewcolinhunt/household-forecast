"""Forms for planning assumptions and the editable copies inside Plans."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from functools import partial
from typing import Any
from uuid import uuid4

import streamlit as st

from financial_model import input_forms as forms
from financial_model import planning_library, setup_pages
from financial_model.planning import (
    AllocationCheckpoint,
    EconomicAssumptions,
    ExpenseLevel,
    ExpenseSchedule,
    PlanningChild,
    Residence,
    TimeAnchor,
    Vesting,
    WorkPhase,
)
from financial_model.schemas import default_allocation_plan


def metadata(data: dict[str, Any], scope: str) -> None:
    """Edit names, money units and optional provenance without economic defaults."""
    name, year = st.columns((3, 1))
    with name:
        forms.text(data, "name", "Name", f"name_{scope}")
    if "money_year" in data:
        with year:
            forms.number(
                data,
                "money_year",
                "Amounts in this year's dollars",
                f"money_year_{scope}",
                integer=True,
                minimum=1900,
                maximum=9999,
                optional=True,
            )
    with st.expander("Notes and source (optional)"):
        forms.text(data, "notes", "Notes", f"notes_{scope}", multiline=True)
        source = data["source"]
        for field, label in (
            ("description", "Source description"),
            ("url", "Source URL"),
            ("version", "Publication / version"),
        ):
            forms.text(source, field, label, f"source_{field}_{scope}")
        forms.text(
            source,
            "as_of",
            "Source date (YYYY-MM-DD)",
            f"source_date_{scope}",
            optional=True,
        )
        if data.get("annual_source"):
            st.caption(
                f"Copied from annual plan {data['annual_source']['tax_year']}, revision {data['annual_source']['revision']}. Future dates and assumptions require review."
            )


def _range_summary(data: dict[str, Any], unit: str = "money") -> str:
    """Show entered alternatives even when their controls are hidden."""
    from decimal import Decimal

    values = (data.get("lower"), data.get("upper"))
    if all(value is None for value in values):
        return "—"
    try:
        shown = [
            f"{(Decimal(str(v)) * 100).normalize():f}%"
            if unit == "rate"
            else f"\\${Decimal(str(v)):,.0f}"
            if unit in {"money", "once"}
            else str(v)
            for v in values
        ]
    except InvalidOperation:
        return "Range needs input"
    if any(value is None for value in values):
        return "Range needs input"
    return " to ".join(shown)


def _range_limits(
    data: dict[str, Any], scope: str, unit: str, columns: list[Any]
) -> None:
    """Edit both bounds without clearing them when the row is collapsed."""
    for column, field in zip(columns[:2], ("lower", "upper"), strict=True):
        with column:
            _range_value(data, field, field.capitalize(), scope, unit, compact=True)
    with columns[2]:
        if forms.row_action("Remove range", "close", f"clear_range_{scope}"):
            data["lower"] = data["upper"] = None
            planning_library.changed_rows()


def _range_details(
    data: dict[str, Any], label: str, scope: str, unit: str = "money"
) -> None:
    """Keep range summaries readable and controls available in row details."""
    st.caption(f"{label} range: {_range_summary(data, unit)}")
    forms.table_header(("Lower", "Upper", ""), (2, 2, 0.5))
    _range_limits(data, scope, unit, st.columns((2, 2, 0.5)))


def time_label(
    data: dict[str, Any] | None,
    *,
    empty: str = "Not entered",
    household: dict[str, Any] | None = None,
) -> str:
    """Summarize an entered boundary without requiring a complete Plan."""
    if data is None:
        return empty
    if data["kind"] == "year":
        return str(data["year"]) if data["year"] is not None else "Year needed"
    if data["kind"] == "age":
        people = {"adult_1": "Person 1", "adult_2": "Person 2"}
        if household:
            people.update(
                {
                    f"adult_{i + 1}": adult["name"]
                    for i, adult in enumerate(household["adults"])
                }
            )
            people.update(
                {child["id"]: child["name"] for child in household["children"]}
            )
        name = people.get(data.get("person") or "", "Person reference needs review")
        return f"{name} · age {data.get('age') if data.get('age') is not None else 'needed'}"
    label = {
        "adult_1": "Person 1 retires",
        "adult_2": "Person 2 retires",
        "both": "Both retired",
        "survivor": "After first death",
    }[data["kind"]]
    offset = data.get("offset", 0)
    return f"{label} {offset:+} years" if offset else label


def _range_value(
    data: dict[str, Any],
    field: str,
    label: str,
    scope: str,
    unit: str,
    *,
    compact: bool = False,
) -> None:
    """Use the same durable controls for central values and optional bounds."""
    key = f"{field}_{scope}"
    if unit == "rate":
        forms.percent(data, field, f"{label} (%)", key, compact=compact)
    elif unit in {"year", "age"}:
        forms.number(
            data,
            field,
            label,
            key,
            integer=True,
            minimum=1900 if unit == "year" else 18,
            maximum=9999 if unit == "year" else 130,
            optional=True,
            compact=compact,
        )
    else:
        forms.money(
            data,
            field,
            f"{label} ($)" if unit == "once" else f"{label} ($/year)",
            key,
            compact=compact,
        )


def _time(
    data: dict[str, Any],
    label: str,
    scope: str,
    household: dict[str, Any] | None = None,
) -> None:
    """Capture annual timing while clearing fields from other anchor kinds."""
    forms.choice(
        data,
        "kind",
        label,
        ("year", "age", "adult_1", "adult_2", "both", "survivor"),
        f"anchor_{scope}",
        labels={
            "year": "Calendar year",
            "age": "Person's age",
            "adult_1": "Person 1 retires",
            "adult_2": "Person 2 retires",
            "both": "Both retired",
            "survivor": "After first death",
        },
    )
    if data["kind"] != "age":
        data["person"] = data["age"] = None
    if data["kind"] != "year":
        data["year"] = None
    if data["kind"] in {"year", "age"}:
        data["offset"] = 0
    if data["kind"] == "year":
        forms.number(
            data,
            "year",
            f"{label} year",
            f"anchor_year_{scope}",
            integer=True,
            minimum=1900,
            maximum=9999,
            optional=True,
        )
    elif data["kind"] == "age":
        people = {"adult_1": "Person 1", "adult_2": "Person 2"}
        if household:
            people.update(
                {f"adult_{i + 1}": a["name"] for i, a in enumerate(household["adults"])}
            )
            people.update({c["id"]: c["name"] for c in household["children"]})
        if data.get("person") not in people and data.get("person"):
            people[data["person"]] = (
                "Missing child — choose a person"
                if household
                else "Child reference (review in Plan)"
            )
            if household:
                st.warning(
                    "This expense refers to a child who is no longer in this Plan. Choose a person to resolve the date."
                )
        if data.get("person") is None:
            data["person"] = "adult_1"
        forms.choice(
            data, "person", "Person", tuple(people), f"person_{scope}", labels=people
        )
        forms.number(
            data,
            "age",
            "Age",
            f"age_{scope}",
            integer=True,
            minimum=0,
            maximum=130,
            optional=True,
        )
        if not household:
            st.caption(
                "Choose child ages in the Plan timeline, where household details are available."
            )
    else:
        forms.number(
            data,
            "offset",
            "Years before (-) / after (+) boundary",
            f"offset_{scope}",
            integer=True,
            minimum=-100,
            maximum=100,
        )


def _remove(rows: list[dict[str, Any]], index: int, label: str, scope: str) -> None:
    """Remove only an explicitly chosen row and regenerate index-based widget keys."""
    if forms.row_action(label, "close", f"remove_{scope}"):
        rows.pop(index)
        planning_library.changed_rows()


def _add(
    rows: list[dict[str, Any]], value: dict[str, Any], label: str, scope: str
) -> None:
    """Append a new editable row without saving it."""
    if st.button(label, key=f"add_{scope}"):
        rows.append(value)
        planning_library.changed_rows()


def work(data: dict[str, Any], scope: str) -> None:
    """Keep compensation and growth together, independent of retirement dates."""
    forms.rsu_funding(data, scope, annual=False)
    for i, column in enumerate(st.columns(2)):
        with column:
            adult = data["adults"][i]
            key = f"adult_{i}_{scope}"
            st.subheader(adult["name"])
            st.caption(
                "A blank end year continues until retirement. Salary growth of 0% keeps pay in line with inflation."
            )
            for j, phase in enumerate(adult["phases"]):
                phase_key = f"phase_{j}_{key}"
                with st.container(border=True):
                    for date_column, field, label in zip(
                        st.columns(2),
                        ("start", "end"),
                        ("Start year", "End year (first year excluded)"),
                        strict=True,
                    ):
                        with date_column:
                            forms.number(
                                phase,
                                field,
                                label,
                                f"{field}_{phase_key}",
                                integer=True,
                                minimum=1900,
                                maximum=9999,
                                optional=True,
                            )
                    forms.compensation(phase, phase_key, annual=False)
                    _range_value(
                        phase["real_growth"],
                        "central",
                        "Salary growth above inflation",
                        f"growth_{phase_key}",
                        "rate",
                    )
                    if _range_summary(phase["real_growth"], "rate") != "—":
                        st.caption(
                            f"Growth range: {_range_summary(phase['real_growth'], 'rate')}"
                        )
                    with st.expander("Growth range and notes (optional)"):
                        _range_details(
                            phase["real_growth"],
                            "Salary growth",
                            f"growth_{phase_key}",
                            "rate",
                        )
                        forms.text(phase, "notes", "Notes", f"notes_{phase_key}")
                    _remove(adult["phases"], j, "Remove work phase", phase_key)
            phase = WorkPhase().model_dump(mode="json")
            phase["real_growth"]["central"] = "0"
            _add(adult["phases"], phase, "Add work phase", key)
            _vesting_and_options(adult, key)


def _vesting_and_options(adult: dict[str, Any], scope: str) -> None:
    """Keep dated equity events together and make entered events visible."""
    with st.expander(
        "Dated equity events (optional)",
        expanded=bool(adult["vesting"] or adult["options"]),
    ):
        for i, row in enumerate(adult["vesting"]):
            key = f"vesting_{i}_{scope}"
            with st.container(border=True):
                st.markdown(f"**Vesting schedule {i + 1}**")
                for column, field, label in zip(
                    st.columns(2),
                    ("start", "end"),
                    ("Vesting start year", "First year without vesting"),
                    strict=True,
                ):
                    with column:
                        forms.number(
                            row,
                            field,
                            label,
                            f"{field}_{key}",
                            integer=True,
                            minimum=1900,
                            maximum=9999,
                            optional=True,
                        )
                forms.text(
                    row,
                    "amount",
                    "Annual vesting amount",
                    f"amount_{key}",
                    optional=True,
                )
                forms.choice(
                    row,
                    "basis",
                    "Vesting amount basis",
                    ("nominal", "real", "shares"),
                    f"basis_{key}",
                    labels={
                        "nominal": "Future dollars",
                        "real": "Reference-year dollars",
                        "shares": "Number of shares",
                    },
                    segmented=True,
                )
                forms.text(row, "notes", "Vesting notes (optional)", f"notes_{key}")
                _remove(adult["vesting"], i, "Remove vesting entry", key)
        _add(
            adult["vesting"],
            Vesting().model_dump(mode="json"),
            "Add vesting entry",
            f"vesting_{scope}",
        )
        for i, row in enumerate(adult["options"]):
            key = f"option_{i}_{scope}"
            with st.container(border=True):
                st.markdown(f"**Option event {i + 1}**")
                forms.number(
                    row,
                    "year",
                    "Option event year",
                    f"year_{key}",
                    integer=True,
                    minimum=1900,
                    maximum=9999,
                    optional=True,
                )
                if forms.optional_section(
                    row,
                    "award",
                    "Include an option award",
                    f"award_enabled_{key}",
                    {"kind": "ISO"},
                ):
                    forms.option_award(row["award"], key)
                if forms.optional_section(
                    row,
                    "exercise",
                    "Include an ISO exercise",
                    f"exercise_enabled_{key}",
                    {},
                ):
                    forms.option_exercise(row["exercise"], key)
                forms.text(
                    row, "notes", "Option event notes (optional)", f"notes_{key}"
                )
                _remove(adult["options"], i, "Remove option event", key)
        _add(
            adult["options"],
            {"year": None, "award": None, "exercise": None, "notes": ""},
            "Add dated option event",
            f"options_{scope}",
        )


EXPENSE_CATEGORIES = {
    "ordinary": "Ordinary living",
    "children": "Children",
    "healthcare": "Healthcare",
    "care": "Care",
    "other": "Other",
}


def _expense(
    row: dict[str, Any],
    scope: str,
    household: dict[str, Any] | None = None,
) -> None:
    """Edit the authoritative expense row in either a library or a Plan copy."""
    forms.text(row, "label", "Expense name", f"label_{scope}")
    forms.choice(
        row,
        "category",
        "Category",
        tuple(EXPENSE_CATEGORIES),
        f"category_{scope}",
        labels=EXPENSE_CATEGORIES,
    )
    forms.choice(
        row,
        "frequency",
        "Frequency",
        ("recurring", "once"),
        f"frequency_{scope}",
        labels={"recurring": "Every year", "once": "One time"},
        segmented=True,
    )
    forms.text(row, "notes", "Included costs and exclusions", f"notes_{scope}")
    details = st.toggle("Edit amount ranges", key=f"expense_details_{scope}")
    unit = "once" if row["frequency"] == "once" else "money"
    for i, level in enumerate(row["levels"]):
        key = f"level_{i}_{scope}"
        with st.container(border=True):
            forms.text(level, "label", "Phase name (optional)", f"label_{key}")
            _range_value(level["amount"], "central", "Amount", key, unit)
            _time(level["when"], "Amount starts", key, household)
            if _range_summary(level["amount"], unit) != "—":
                st.caption(f"Range: {_range_summary(level['amount'], unit)}")
            if details:
                _range_details(level["amount"], "Amount", key, unit)
            if len(row["levels"]) > 1:
                _remove(row["levels"], i, "Remove phase", key)
    if row["frequency"] == "recurring":
        _add(
            row["levels"],
            ExpenseLevel().model_dump(mode="json"),
            "Add spending phase",
            scope,
        )
    if forms.optional_section(
        row,
        "end",
        "Specify an end date",
        f"end_enabled_{scope}",
        TimeAnchor().model_dump(mode="json"),
    ):
        _time(row["end"], "Payments stop", f"end_{scope}", household)
    if row["frequency"] == "once" and (len(row["levels"]) != 1 or row["end"]):
        st.warning(
            "A one-time expense needs one amount and date. Remove extra phases and the end date."
        )
    forms.percent(row, "real_growth", "Growth above inflation (%)", f"growth_{scope}")


def _split_expense(
    expense: dict[str, Any],
    share: Decimal,
    label: str,
    category: str,
    remaining_notes: str,
    separated_notes: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split every phase and range without changing timing or combined amounts."""
    original = ExpenseSchedule.model_validate(expense).model_dump(mode="json")
    separated = deepcopy(original)
    separated.update(
        id=str(uuid4()), label=label, category=category, notes=separated_notes
    )
    original["notes"] = remaining_notes
    for remaining, added in zip(original["levels"], separated["levels"], strict=True):
        for field, amount in remaining["amount"].items():
            if amount is not None:
                part = (Decimal(amount) * share).quantize(Decimal("0.01"))
                added["amount"][field] = str(part)
                remaining["amount"][field] = str(Decimal(amount) - part)
    return original, separated


def _reclassify(data: dict[str, Any], scope: str) -> None:
    """Preview an explicit transfer out of an existing expense before applying it."""
    if not data["expenses"]:
        return
    with st.expander("Separate costs already included in an expense"):
        st.caption(
            "Split every phase by the same percentage. Dates, growth, and combined totals stay the same. For different timing, review and edit both schedules afterward."
        )
        choices = {row["id"]: row for row in data["expenses"]}
        selected = st.selectbox(
            "Expense to split",
            tuple(choices),
            format_func=lambda key: choices[key]["label"],
            key=f"split_source_{scope}",
        )
        share = st.number_input(
            "Share to separate (%)",
            min_value=0.0,
            max_value=100.0,
            value=0.0,
            key=f"split_share_{scope}",
        )
        label = st.text_input("Separated expense name", key=f"split_label_{scope}")
        category = st.selectbox(
            "Separated category",
            tuple(EXPENSE_CATEGORIES),
            format_func=EXPENSE_CATEGORIES.__getitem__,
            key=f"split_category_{scope}",
        )
        remaining_notes = st.text_input(
            "Costs remaining in the original expense", key=f"split_remaining_{scope}"
        )
        separated_notes = st.text_input(
            "Costs included in the separated expense", key=f"split_notes_{scope}"
        )
        if not (
            0 < share < 100
            and label.strip()
            and remaining_notes.strip()
            and separated_notes.strip()
        ):
            st.caption(
                "Enter a percentage between 0 and 100 and describe both sets of costs to review the split."
            )
            return
        try:
            remaining, separated = _split_expense(
                choices[selected],
                Decimal(str(share)) / 100,
                label,
                category,
                remaining_notes,
                separated_notes,
            )
        except ValueError:
            st.warning("Complete the original expense before splitting it.")
            return
        st.table(
            [
                {
                    "Phase starts": time_label(before["when"]),
                    "Before": before["amount"]["central"],
                    "Remaining": after["amount"]["central"],
                    "Separated": added["amount"]["central"],
                    "Combined after": str(
                        Decimal(after["amount"]["central"])
                        + Decimal(added["amount"]["central"])
                    )
                    if before["amount"]["central"] is not None
                    else "Unknown",
                }
                for before, after, added in zip(
                    choices[selected]["levels"],
                    remaining["levels"],
                    separated["levels"],
                    strict=True,
                )
            ]
        )
        if st.button("Apply reviewed split", key=f"apply_split_{scope}"):
            data["expenses"][data["expenses"].index(choices[selected])] = remaining
            data["expenses"].append(separated)
            planning_library.changed_rows()


def spending(
    data: dict[str, Any],
    scope: str,
    household: dict[str, Any] | None = None,
    category: str | None = None,
) -> None:
    """Edit additive expenses with one timeline across working and retired years."""
    st.caption(
        "Amounts use this profile's money year. Each new phase replaces only this expense's amount. Starts include the year; ends exclude it. Inflation is applied once, with growth above inflation measured from the money year."
    )
    st.caption(
        "Include healthcare and care here. Exclude taxes, saving, and debt payments already modeled elsewhere. Categories group expenses; they do not calculate costs."
    )
    category = category or st.selectbox(
        "Show expense category",
        ("all", *EXPENSE_CATEGORIES),
        format_func=lambda value: "All expenses"
        if value == "all"
        else EXPENSE_CATEGORIES[value],
        key=f"expense_filter_{scope.rsplit('_', 1)[0]}",
    )
    for i, row in enumerate(data["expenses"]):
        if category != "all" and row["category"] != category:
            continue
        key = f"expense_{row['id']}_{scope}"
        with st.container(border=True):
            _expense(row, key, household)
            _remove(data["expenses"], i, "Remove expense", key)
    _add(
        data["expenses"],
        ExpenseSchedule.model_validate(
            {
                "label": "New expense",
                "category": "ordinary" if category == "all" else category,
            }
        ).model_dump(mode="json"),
        "Add an expense",
        scope,
    )
    _reclassify(data, scope)


def investments(data: dict[str, Any], scope: str, dsn: str) -> None:
    """Keep saving, cash reserves, and dated allocation targets distinct."""
    st.subheader("Saving instructions")
    if forms.optional_section(
        data,
        "saving",
        "Include saving instructions",
        f"saving_enabled_{scope}",
        default_allocation_plan().model_dump(mode="json"),
    ):
        setup_pages.read_goal_catalogue(dsn)
        if forms.saving_instructions(
            data["saving"], scope, st.session_state.get("goals", ()), surplus=True
        ):
            planning_library.changed_rows()
    st.subheader("Cash reserve")
    forms.money(
        data,
        "reserved_cash",
        "Cash reserve ($)",
        f"reserved_cash_{scope}",
        help_text="Cash kept outside the portfolio allocation, in the reference-year dollars set above. It is already part of Wealth, not an extra asset. Enter 0 for none.",
    )
    st.subheader("Portfolio strategy")
    forms.choice(
        data,
        "policy",
        "Allocation policy",
        ("undecided", "constant", "gradual"),
        f"policy_{scope}",
        labels={
            "undecided": "Undecided",
            "constant": "Keep the same mix",
            "gradual": "Change the mix over time",
        },
        segmented=True,
    )
    st.caption(
        "Stocks, bonds, and cash must total 100%. The cash reserve is separate. To change the mix over time, add a starting target and a final target with their dates."
    )
    if data["policy"] == "gradual":
        forms.choice(
            data,
            "interpolation",
            "Glide-path shape",
            tuple(planning_library.GLIDE_SHAPES),
            f"glide_shape_{scope}",
            labels=planning_library.GLIDE_SHAPES,
            segmented=True,
        )
        st.caption(
            "The shape changes how quickly you move toward the final mix. All shapes reach it on the finish date and hold it afterward. With extra dated targets, the same shape applies between each pair."
        )
    for i, row in enumerate(data["checkpoints"]):
        key = f"checkpoint_{i}_{scope}"
        with st.container(border=True):
            st.markdown(f"**Allocation target {i + 1}**")
            _time(row["when"], "Allocation effective", key)
            if forms.optional_section(
                row,
                "mix",
                "Specify target allocation",
                f"mix_enabled_{key}",
                {"equities": None, "fixed_income": None, "cash": None},
            ):
                for column, field, label in zip(
                    st.columns(3),
                    ("equities", "fixed_income", "cash"),
                    ("Stocks", "Bonds", "Cash"),
                    strict=True,
                ):
                    with column:
                        forms.percent(
                            row["mix"], field, f"{label} (%)", f"{field}_{key}"
                        )
            _remove(data["checkpoints"], i, "Remove allocation checkpoint", key)
    _add(
        data["checkpoints"],
        AllocationCheckpoint().model_dump(mode="json"),
        "Add allocation checkpoint",
        scope,
    )
    forms.choice(
        data,
        "rebalancing",
        "Rebalancing",
        ("undecided", "annual", "flows_only"),
        f"rebalancing_{scope}",
        labels={
            "undecided": "Undecided",
            "annual": "Annual",
            "flows_only": "When money enters or leaves",
        },
        segmented=True,
        help_text="Annual rebalancing restores the target mix each year. The other option moves toward the target only through contributions and withdrawals.",
    )


def economy(data: dict[str, Any], scope: str) -> None:
    """Capture compound outlooks separately from editable simulation assumptions."""
    st.caption(
        "Enter annual inflation and total investment returns. Returns are after fees, before tax, and include inflation."
    )
    fields = (
        ("inflation", "Inflation"),
        ("equities", "Stock return"),
        ("fixed_income", "Bond return"),
        ("cash", "Cash return"),
    )
    ranges = st.toggle("Edit ranges", key=f"ranges_{scope}")
    forms.table_header(
        ("Assumption", "Annual rate (%)", "Lower (%)", "Upper (%)", "")
        if ranges
        else ("Assumption", "Annual rate (%)", "Range"),
        (3, 2, 2, 2, 0.5) if ranges else (3, 2, 4.5),
    )
    for field, label in fields:
        columns = st.columns(
            (3, 2, 2, 2, 0.5) if ranges else (3, 2, 4.5), vertical_alignment="center"
        )
        columns[0].text(label)
        with columns[1]:
            _range_value(
                data[field], "central", label, f"{field}_{scope}", "rate", compact=True
            )
        if ranges:
            _range_limits(data[field], f"{field}_{scope}", "rate", columns[2:])
        else:
            columns[2].caption(_range_summary(data[field], "rate"))
    if ranges:
        st.caption(
            "Enter both ends. Ranges describe alternative assumptions, not probabilities."
        )
    st.subheader("Investment income")
    st.caption(
        "The returns above already include dividends and interest. These yields determine how much income is taxed each year."
    )
    for column, field, label in zip(
        st.columns(2),
        ("stock_dividend_yield", "bond_interest_yield"),
        ("Stock dividend yield (%)", "Bond interest yield (%)"),
        strict=True,
    ):
        with column:
            forms.percent(data, field, label, f"{field}_{scope}")
    st.subheader("Forecast uncertainty")
    _simulation_summary(data)
    st.caption(
        "The default 10th to 90th percentile band shows the middle 80% of simulated balances in each year. The lower/upper ranges above describe alternative assumptions, not this outcome band."
    )
    with st.expander("Advanced simulation settings"):
        st.caption(
            "Volatility measures how much yearly returns fluctuate. Correlation measures how they move together. These settings affect Monte Carlo forecasts."
        )
        st.caption(
            "Preset sources: [Vanguard volatility forecasts](https://www.vanguardsouthamerica.com/en/home/insights/economic-market-outlook/vanguard-capital-markets-model-forecasts-2026) and [JPMorgan 2026 correlations](https://am.jpmorgan.com/content/dam/jpm-am-aem/global/en/insights/portfolio-insights/ltcma/noindex/ltcma-full-report.pdf#page=82). Values are rounded and adapted for this model."
        )
        st.button(
            "Use recommended simulation settings",
            key=f"reset_simulation_{scope}",
            on_click=_reset_simulation,
            args=(data, scope),
        )
        # Use a retained row dictionary so callbacks can capture tuple elements too.
        for i, label in enumerate(("Equities", "Fixed income", "Cash", "Inflation")):
            key = f"volatility_{i}_{scope}"
            _list_percent(
                data["volatility"], i, f"{label} annual standard deviation (%)", key
            )
        include = st.checkbox(
            "Include correlation matrix",
            value=data["correlation"] is not None,
            key=f"correlations_{scope}",
        )
        if include:
            if data["correlation"] is None:
                data["correlation"] = [[None] * 4 for _ in range(4)]
            st.caption(
                "Correlations apply to log gross returns (log of 1 + the annual rate). Rows and columns: equities, fixed income, cash, inflation. Enter all 16 values, including the diagonal; use -1 to 1, not percent."
            )
            for i, label in enumerate(
                ("Equities", "Fixed income", "Cash", "Inflation")
            ):
                for j, column in enumerate(st.columns(4)):
                    with column:
                        key = f"corr_{i}_{j}_{scope}"
                        value = st.text_input(
                            f"{label} / {('Equities', 'Fixed income', 'Cash', 'Inflation')[j]}",
                            value=str(data["correlation"][i][j])
                            if data["correlation"][i][j] is not None
                            else "",
                            key=key,
                            on_change=_list_capture,
                            args=(data["correlation"][i], j, key, False),
                        )
                        data["correlation"][i][j] = forms.blank(value)
        else:
            data["correlation"] = None


def _simulation_summary(data: dict[str, Any]) -> None:
    """Describe the retained statistics without replacing missing or custom values."""
    try:
        settings = EconomicAssumptions(
            name="Simulation summary",
            volatility=data["volatility"],
            correlation=data["correlation"],
        )
    except ValueError:
        st.caption("Simulation settings need attention; review the advanced settings.")
        return
    parts = [
        f"{label} {(value * 100).normalize():f}%"
        if value is not None
        else f"{label} not entered"
        for label, value in zip(
            ("stocks", "bonds", "cash", "inflation"), settings.volatility, strict=True
        )
    ]
    st.caption("Annual volatility: " + "; ".join(parts) + ".")
    if settings.correlation is None:
        st.caption("Correlations are not entered; review the advanced settings.")


def _reset_simulation(data: dict[str, Any], scope: str) -> None:
    """Apply the recommended statistics while retaining all other raw edits."""
    defaults = EconomicAssumptions(name="Recommended simulation").model_dump(
        mode="json"
    )
    for field in ("volatility", "correlation"):
        data[field] = defaults[field]
    keys = [f"correlations_{scope}"]
    keys += [f"volatility_{i}_{scope}" for i in range(4)]
    keys += [f"corr_{i}_{j}_{scope}" for i in range(4) for j in range(4)]
    for key in keys:
        st.session_state.pop(key, None)


def _list_capture(row: list[Any], index: int, key: str, percent: bool) -> None:
    """Capture a fixed statistical vector entry before its controls disappear."""
    row[index] = (
        forms.percent_value(st.session_state[key])
        if percent
        else forms.blank(st.session_state[key])
    )


def _list_percent(row: list[Any], index: int, label: str, key: str) -> None:
    """Reuse percentage rendering and route its callback into a fixed vector."""
    from decimal import Decimal

    try:
        shown = str(Decimal(str(row[index])) * 100) if row[index] is not None else ""
    except InvalidOperation:
        shown = str(row[index])
    value = st.text_input(
        label,
        value=shown,
        key=key,
        on_change=_list_capture,
        args=(row, index, key, True),
    )
    row[index] = forms.percent_value(value)


def retirement(data: dict[str, Any], scope: str) -> None:
    """Keep retirement dates and benefits visible; offer ranges on demand."""
    st.subheader("Retirement and lifespan")
    st.caption(
        "Retirement year is the first full year without employment income. Plan through age sets how long spending must be covered."
    )
    details = st.toggle("Edit retirement ranges", key=f"retirement_details_{scope}")
    with st.expander(
        "Mortality assumptions (advanced)", expanded=data["mode"] == "sample"
    ):
        forms.choice(
            data,
            "mode",
            "Lifespan assumptions",
            ("ages", "sample"),
            f"mode_{scope}",
            labels={
                "ages": "Choose planning ages",
                "sample": "Mortality model (not forecastable yet)",
            },
            segmented=True,
        )
        if data["mode"] == "sample":
            st.warning(
                "Forecasts currently require planning ages. Mortality model inputs can be saved for later use."
            )
            forms.choice(
                data,
                "table_basis",
                "Life table basis",
                ("unknown", "period", "cohort"),
                f"table_basis_{scope}",
                help_text="Period tables use current death rates; cohort tables allow rates to change over time.",
                segmented=True,
            )
            for i, adult in enumerate(data["adults"]):
                adult["age"] = {"central": None, "lower": None, "upper": None}
                forms.text(
                    adult,
                    "table_classification",
                    f"{adult['name']} table classification",
                    f"classification_{i}_{scope}",
                )
    forms.table_header(("Person", "Retirement year", "Plan through age"), (2, 2, 2))
    for i, adult in enumerate(data["adults"]):
        name, year, age = st.columns(3, vertical_alignment="center")
        name.text(adult["name"])
        with year:
            _range_value(
                adult["retirement"],
                "central",
                f"{adult['name']} retires",
                f"retirement_{i}_{scope}",
                "year",
                compact=True,
            )
        with age:
            if data["mode"] == "ages":
                _range_value(
                    adult["age"],
                    "central",
                    f"Plan for {adult['name']} through age",
                    f"age_{i}_{scope}",
                    "age",
                    compact=True,
                )
            else:
                st.caption("Mortality model selected")
        if any(
            _range_summary(adult[field], unit) != "—"
            for field, unit in (("retirement", "year"), ("age", "age"))
        ):
            st.caption(
                f"{adult['name']} ranges · Retire: {_range_summary(adult['retirement'], 'year')} · Age: {_range_summary(adult['age'], 'age')}"
            )
        if details:
            _range_details(
                adult["retirement"],
                f"{adult['name']} retirement year",
                f"retirement_{i}_{scope}",
                "year",
            )
            if data["mode"] == "ages":
                _range_details(
                    adult["age"],
                    f"{adult['name']} planning age",
                    f"age_{i}_{scope}",
                    "age",
                )
    st.caption(
        "All household expenses, including retirement and survivor spending, are entered in Spending."
    )
    _retirement_income(data, scope)


def _benefit_status(value: Any) -> str:
    """Distinguish missing benefits, known zero, and entered amounts."""
    from decimal import Decimal

    if value is None:
        return "unknown"
    try:
        amount = Decimal(str(value))
        if amount.is_zero():
            return "none"
        return "positive" if amount.is_finite() and amount > 0 else "invalid"
    except InvalidOperation:
        return "invalid"


def _pension_choice(adult: dict[str, Any], key: str, amount_key: str) -> None:
    """Make the no-pension choice explicit and clear a stale amount widget."""
    choice = st.session_state[key]
    if choice in {"unknown", "none"}:
        adult["pension"] = None if choice == "unknown" else "0"
        st.session_state.pop(amount_key, None)


def _pension(adult: dict[str, Any], scope: str) -> None:
    """Hide pension timing and survivor choices unless a pension is expected."""
    key = f"pension_choice_{scope}"
    amount_key = f"pension_{scope}"
    amount = adult.get("pension")
    status = _benefit_status(amount)
    selected = status if status in {"unknown", "none"} else "pension"
    choices = ("unknown", "none", "pension")
    choice = st.segmented_control(
        f"{adult['name']} pension",
        choices,
        default=selected,
        required=True,
        format_func=lambda value: {
            "unknown": "Not entered",
            "none": "No pension",
            "pension": "Enter pension",
        }[value],
        key=key,
        on_change=_pension_choice,
        args=(adult, key, amount_key),
    )
    if choice != "pension":
        return
    forms.money(adult, "pension", "Pension ($/year)", amount_key)
    if _benefit_status(adult["pension"]) == "positive":
        with st.container(horizontal=True):
            forms.number(
                adult,
                "pension_start_age",
                "Pension starts at age",
                f"pension_age_{scope}",
                integer=True,
                minimum=18,
                maximum=100,
            )
            forms.percent(
                adult,
                "pension_survivor_fraction",
                "Pension paid to survivor (%)",
                f"pension_survivor_{scope}",
            )


def _retirement_income(data: dict[str, Any], scope: str) -> None:
    """Collect benefits once in the reusable Retirement scenario."""
    st.subheader("Retirement income")
    st.caption(
        f"Annual amounts in {data['money_year'] or 'the reference-year'} dollars, before tax. Enter the Social Security estimate for the chosen claiming age; use 0 if none."
    )
    for i, adult in enumerate(data["adults"]):
        key = f"income_{i}_{scope}"
        adult.setdefault("claiming_age", 67)
        adult.setdefault("pension_start_age", 65)
        with st.container(border=True):
            st.text(adult["name"])
            with st.container(horizontal=True):
                forms.money(
                    adult,
                    "social_security",
                    f"{adult['name']} Social Security ($/year)",
                    f"social_security_{key}",
                )
                if _benefit_status(adult.get("social_security")) == "positive":
                    forms.number(
                        adult,
                        "claiming_age",
                        "Social Security claiming age",
                        f"claiming_age_{key}",
                        integer=True,
                        minimum=62,
                        maximum=70,
                    )
            _pension(adult, key)


def _alignment_label(
    names: list[str], household_names: list[str], mapping: tuple[int, int] | None
) -> str:
    """Explain both person mappings before the user confirms an alignment."""
    if mapping is None:
        return "Not reviewed"
    return " · ".join(
        f"{name} → {household_names[slot - 1]} (adult {slot})"
        for name, slot in zip(names, mapping, strict=True)
    )


def household_demographics(household: dict[str, Any], scope: str) -> None:
    """Edit the family details shared by library sources and Plan copies."""
    forms.number(
        household,
        "reference_year",
        "Ages at year end",
        f"household_year_{scope}",
        integer=True,
        minimum=1900,
        maximum=9999,
        optional=True,
    )
    forms.table_header(("Person", "Age at year end", "WA Cares"), (3, 1.5, 2))
    for i, adult in enumerate(household["adults"]):
        name, age, exemption = st.columns((3, 1.5, 2), vertical_alignment="center")
        with name:
            forms.text(
                adult, "name", "Name", f"household_name_{i}_{scope}", compact=True
            )
        with age:
            forms.number(
                adult,
                "age",
                "Age at reference year end",
                f"household_age_{i}_{scope}",
                integer=True,
                minimum=18,
                maximum=130,
                optional=True,
                compact=True,
            )
        with exemption:
            forms.choice(
                adult,
                "wa_cares_exempt",
                "WA Cares exemption",
                (None, True, False),
                f"wa_cares_{i}_{scope}",
                labels={None: "Unknown", True: "Exempt", False: "Not exempt"},
                compact=True,
            )
    forms.choice(
        household,
        "filing_status",
        "Filing status",
        (None, "married_filing_jointly"),
        f"filing_{scope}",
        labels={None: "Unknown", "married_filing_jointly": "Married filing jointly"},
    )
    st.subheader("Children")
    if household["children"]:
        forms.table_header(
            ("Child", "Birth year (actual or expected)", ""), (3, 2, 0.4)
        )
    for i, child in enumerate(household["children"]):
        name, year, remove = st.columns((3, 2, 0.4), vertical_alignment="center")
        with name:
            forms.text(
                child, "name", "Child label", f"child_name_{i}_{scope}", compact=True
            )
        with year:
            forms.number(
                child,
                "birth_year",
                "Birth year (actual or expected)",
                f"child_year_{i}_{scope}",
                integer=True,
                minimum=1900,
                maximum=9999,
                optional=True,
                compact=True,
            )
        with remove:
            _remove(household["children"], i, "Remove child", f"child_{i}_{scope}")
    _add(
        household["children"],
        PlanningChild(name="Child").model_dump(mode="json"),
        "Add child",
        f"child_{scope}",
    )
    st.caption(
        "Children's costs belong in Spending; adding a child does not create expenses."
    )


def household_context(data: dict[str, Any], scope: str) -> None:
    """Edit copied demographics, residence and person-specific profile mappings."""
    household = data["household"]
    household_demographics(household, scope)
    st.subheader("Residence")
    if household["residence"]:
        forms.table_header(("Starting year", "Location", ""), (2, 3, 0.4))
    for i, row in enumerate(household["residence"]):
        year, location, remove = st.columns((2, 3, 0.4), vertical_alignment="center")
        with year:
            forms.number(
                row,
                "year",
                "Residence effective year",
                f"residence_year_{i}_{scope}",
                integer=True,
                minimum=1900,
                maximum=9999,
                optional=True,
                compact=True,
            )
        with location:
            forms.text(
                row,
                "location",
                "Residence jurisdiction",
                f"residence_location_{i}_{scope}",
                optional=True,
                compact=True,
            )
        with remove:
            _remove(
                household["residence"], i, "Remove residence", f"residence_{i}_{scope}"
            )
    _add(
        household["residence"],
        Residence().model_dump(mode="json"),
        "Add residence",
        f"residence_{scope}",
    )
    for component in data["components"]:
        kind = component["inputs"]["kind"]
        if kind in {"work", "retirement"}:
            names = [a["name"] for a in component["inputs"]["adults"]]
            household_names = [a["name"] for a in household["adults"]]
            mapping = component["adult_mapping"]
            selected = None if mapping is None else (mapping[0], mapping[1])
            key = f"mapping_{component['source_id']}_{scope}"
            choices: list[tuple[int, int] | None] = [None, (1, 2), (2, 1)]
            value = st.selectbox(
                f"Match people · {component['inputs']['name']}",
                choices,
                index=choices.index(selected),
                format_func=partial(_alignment_label, names, household_names),
                key=key,
                on_change=forms.capture,
                args=(component, "adult_mapping", key),
            )
            component["adult_mapping"] = value
