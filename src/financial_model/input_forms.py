"""Concern-specific input controls shared by annual cases and planning profiles."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any

import streamlit as st

from financial_model.schemas import Goal, dollars


def capture(
    row: dict[str, Any],
    field: str,
    key: str,
    transform: Callable[[Any], Any] | None = None,
) -> None:
    """Capture a widget change before navigation removes the widget."""
    value = st.session_state[key]
    row[field] = transform(value) if transform else value


def blank(value: str) -> str | None:
    """Keep blank optional text distinct from a known zero."""
    return value.strip() or None


def percent_value(value: str) -> str | None:
    """Convert valid percent text and retain invalid input for later correction."""
    if not value.strip():
        return None
    try:
        return str(Decimal(value) / 100)
    except InvalidOperation:
        return value


def text(
    row: dict[str, Any],
    field: str,
    label: str,
    key: str,
    *,
    optional: bool = False,
    help_text: str | None = None,
    multiline: bool = False,
    disabled: bool = False,
) -> None:
    """Edit raw text with a durable callback and an explicit owner."""
    transform = blank if optional else None
    widget = st.text_area if multiline else st.text_input
    value = widget(
        label,
        value=row.get(field) or "",
        key=key,
        help=help_text,
        disabled=disabled,
        on_change=capture,
        args=(row, field, key, transform),
    )
    row[field] = transform(value) if transform else value


def choice(
    row: dict[str, Any],
    field: str,
    label: str,
    options: list[Any] | tuple[Any, ...],
    key: str,
    *,
    labels: dict[Any, str] | None = None,
    help_text: str | None = None,
) -> None:
    """Choose an explicit meaning without inferring other fields."""
    row[field] = st.selectbox(
        label,
        options,
        index=options.index(row.get(field)),
        format_func=lambda value: labels.get(value, str(value))
        if labels
        else str(value).replace("_", " ").capitalize(),
        key=key,
        help=help_text,
        on_change=capture,
        args=(row, field, key),
    )


def check(
    row: dict[str, Any],
    field: str,
    label: str,
    key: str,
    *,
    toggle: bool = False,
    help_text: str | None = None,
) -> None:
    """Capture a boolean before a category change."""
    widget = st.toggle if toggle else st.checkbox
    row[field] = widget(
        label,
        value=row.get(field, False),
        key=key,
        help=help_text,
        on_change=capture,
        args=(row, field, key),
    )


def number(
    row: dict[str, Any],
    field: str,
    label: str,
    key: str,
    *,
    integer: bool = False,
    minimum: int | float = 0,
    maximum: int | float | None = None,
    optional: bool = False,
    help_text: str | None = None,
    decimals: int = 2,
) -> None:
    """Render constrained numeric controls while retaining exact unchanged amounts."""
    original = row.get(field)
    convert = int if integer else float
    value = (
        convert(original) if original is not None else None if optional else convert(0)
    )
    entered = st.number_input(
        label,
        value=value,
        min_value=convert(minimum),
        max_value=convert(maximum) if maximum is not None else None,
        key=key,
        help=help_text,
        format="%d" if integer else f"%.{decimals}f",
        on_change=capture,
        args=(row, field, key),
    )
    row[field] = original if original is not None and entered == value else entered


def percent(row: dict[str, Any], field: str, label: str, key: str) -> None:
    """Show decimal fractions as percentages without losing invalid raw edits."""
    original = row.get(field)
    try:
        shown = "" if original is None else str(Decimal(str(original)) * 100)
    except InvalidOperation:
        shown = str(original)
    value = st.text_input(
        label,
        value=shown,
        key=key,
        placeholder="Unknown",
        on_change=capture,
        args=(row, field, key, percent_value),
    )
    row[field] = percent_value(value)


def money(
    row: dict[str, Any],
    field: str,
    label: str,
    key: str,
    *,
    annual: bool = False,
    help_text: str | None = None,
) -> None:
    """Share dollar fields while permitting unknown future amounts."""
    if annual:
        number(row, field, label, key, help_text=help_text)
    else:
        text(
            row,
            field,
            label,
            key,
            optional=True,
            help_text=help_text
            or "USD, no commas. Blank is unknown; 0 is a known zero.",
        )


def compensation(data: dict[str, Any], scope: str, *, annual: bool) -> dict[str, Any]:
    """Edit compensation only; names, ages, residence and expenses have other owners."""
    if annual:
        check(
            data,
            "not_working",
            "Not working in this scenario",
            f"not_working_{scope}",
            help_text="Does not change compensation amounts. Set those separately below.",
        )
        text(
            data,
            "employer",
            "Employer",
            f"employer_{scope}",
            optional=True,
            disabled=data.get("not_working", False),
        )
        if data.get("not_working"):
            data["employer"] = None
    else:
        choice(
            data,
            "status",
            "Employment status",
            ("unknown", "working", "not_working"),
            f"status_{scope}",
        )
        text(data, "employer", "Employer", f"employer_{scope}", optional=True)
    money(
        data,
        "cash_pay",
        "Annual salary and bonus ($)",
        f"cash_pay_{scope}",
        annual=annual,
    )
    if annual:
        money(
            data,
            "rsu_vesting",
            "Annual RSU value at vesting ($)",
            f"rsu_vesting_{scope}",
            annual=True,
        )
    else:
        choice(
            data,
            "contribution_basis",
            "Employee contribution convention",
            ("real", "nominal", "pay_fraction"),
            f"contribution_basis_{scope}",
            labels={
                "real": "Constant purchasing power",
                "nominal": "Constant nominal dollars",
                "pay_fraction": "Percentage of salary and bonus",
            },
        )
    if not annual and data["contribution_basis"] == "pay_fraction":
        data["employee_401k"] = None
        percent(
            data,
            "contribution_rate",
            "Employee contribution (% of salary and bonus)",
            f"contribution_rate_{scope}",
        )
    else:
        if not annual:
            data["contribution_rate"] = None
        money(
            data,
            "employee_401k",
            "Annual employee 401(k) contributions ($)",
            f"employee_401k_{scope}",
            annual=annual,
            help_text="Traditional pretax contributions. Future limits are not evaluated here.",
        )
    if annual:
        data.setdefault("employer_retirement_rate", "0")
    percent(
        data,
        "employer_retirement_rate",
        "Employer 401(k) contributions (% of salary and bonus)",
        f"employer_retirement_rate_{scope}",
    )
    return data


def rsu_funding(data: dict[str, Any], scope: str, *, annual: bool) -> None:
    """Choose shares or cash as the tax funding source without calculating taxes."""
    if annual:
        data.setdefault("rsu_taxes_from_shares", True)
        check(
            data,
            "rsu_taxes_from_shares",
            "Use vested shares to pay their taxes",
            f"rsu_taxes_from_shares_{scope}",
            toggle=True,
            help_text="On: vested RSUs cover their incremental taxes. Off: cash pays all taxes. Total taxes are unchanged.",
        )
    else:
        choice(
            data,
            "rsu_taxes_from_shares",
            "RSU taxes paid from",
            (None, True, False),
            f"rsu_taxes_from_shares_{scope}",
            labels={None: "Undecided", True: "Vested shares", False: "Cash"},
        )


def option_award(data: dict[str, Any], scope: str) -> None:
    """Capture an informational annual award independently of an exercise."""
    choice(data, "kind", "Option type", ("ISO", "NSO"), f"award_kind_{scope}")
    number(
        data,
        "vesting_shares",
        "Options vesting this year",
        f"award_shares_{scope}",
        integer=True,
    )
    number(
        data,
        "strike",
        "Award exercise price ($/share)",
        f"award_strike_{scope}",
        decimals=6,
    )
    number(
        data,
        "assumed_share_value",
        "Assumed share value ($/share)",
        f"award_value_{scope}",
        decimals=6,
        optional=True,
    )


def option_exercise(data: dict[str, Any], scope: str) -> None:
    """Collect a qualifying ISO exercise-and-hold event with its own date context."""
    number(data, "shares", "Shares exercised", f"shares_{scope}", integer=True)
    number(data, "strike", "Exercise price ($/share)", f"strike_{scope}", decimals=6)
    number(
        data,
        "fair_market_value",
        "FMV at exercise ($/share)",
        f"fmv_{scope}",
        decimals=6,
    )


def _enable(
    parent: dict[str, Any], field: str, key: str, default: dict[str, Any]
) -> None:
    """Apply an explicit optional-section toggle before navigation."""
    parent[field] = (
        (parent.get(field) or dict(default)) if st.session_state[key] else None
    )


def optional_section(
    parent: dict[str, Any], field: str, label: str, key: str, default: dict[str, Any]
) -> bool:
    """Enable or clear a specific optional value, keeping persistence outside forms."""
    enabled = st.checkbox(
        label,
        value=parent.get(field) is not None,
        key=key,
        on_change=_enable,
        args=(parent, field, key, default),
    )
    if enabled and parent.get(field) is None:
        parent[field] = dict(default)
    return enabled


def annual_options(adult: dict[str, Any], scope: str) -> None:
    """Retain annual award and exercise controls in the Work category."""
    with st.expander("Stock option award"):
        if optional_section(
            adult,
            "option_award",
            "Include an option award",
            f"award_enabled_{scope}",
            {"kind": "ISO"},
        ):
            st.caption(
                "This year's vesting only. Award estimates do not enter cash, tax or saving totals."
            )
            option_award(adult["option_award"], scope)
    with st.expander("ISO exercise and hold"):
        st.caption(
            "Qualifying ISOs exercised and held through year end. NSO exercises and share sales are not supported."
        )
        option_exercise(adult.setdefault("options", {}), scope)


def annual_household(data: dict[str, Any], ages: dict[str, Any], scope: str) -> None:
    """Edit an annual case's family and residence facts separately from Work."""
    data.setdefault("state", "WA")
    choice(
        data,
        "state",
        "Full-year state",
        ("WA", "CA"),
        f"state_{scope}",
        labels={"WA": "Washington", "CA": "California"},
    )
    st.caption("Married filing jointly. Ages are at the annual plan's tax-year end.")
    for i, column in enumerate(st.columns(2)):
        with column:
            adult = data.setdefault("adults", [{}, {}])[i]
            st.subheader(f"Adult {i + 1}")
            text(adult, "name", "Name", f"name_adult_{i}_{scope}")
            number(
                adult,
                "age",
                "Age at year end",
                f"age_adult_{i}_{scope}",
                integer=True,
                minimum=18,
                maximum=64,
                optional=True,
            )
            check(
                adult,
                "wa_cares_exempt",
                "Exempt from WA Cares",
                f"wa_cares_adult_{i}_{scope}",
            )
    text(
        ages,
        "text",
        "Qualifying children's ages",
        f"children_{scope}",
        help_text="Whole ages 0-17, separated by commas. Leave blank for none.",
    )


def living_costs(data: dict[str, Any], scope: str) -> dict[str, Any]:
    """Edit monthly consumption and separately labeled annual tax eligibility."""
    for column, field, label in zip(
        st.columns(3),
        ("rent", "childcare", "other"),
        (
            "Monthly rent ($)",
            "Monthly childcare ($)",
            "Other monthly living expenses ($)",
        ),
        strict=True,
    ):
        with column:
            money(data, field, label, f"living_{field}_{scope}", annual=True)
    money(
        data,
        "eligible_care_expense",
        "Annual qualifying work-related childcare expense ($)",
        f"eligible_care_{scope}",
        annual=True,
        help_text="Eligibility describes childcare already entered above; this is not additional spending.",
    )
    check(
        data,
        "ca_renter_credit_eligible",
        "Qualifying California renter",
        f"ca_renter_{scope}",
    )
    return data


def _cash_capture(
    row: dict[str, Any], key: str, factor: int, original: Decimal
) -> None:
    """Store exact annual cash when an entry unit differs from its stored unit."""
    entered = st.session_state[key]
    row["cash_amount"] = str(
        original
        if entered == float(original / factor)
        else dollars(Decimal(str(entered)) * factor)
    )


def saving_instructions(
    data: dict[str, Any], scope: str, goals: tuple[Goal, ...]
) -> bool:
    """Edit goal amounts and destinations, returning whether row order changed."""
    catalogue = {str(g.id): g for g in goals}
    rows = data["rows"]
    rows.sort(key=lambda r: r["cash_amount"] is None)
    unit = st.radio(
        "Savings amounts",
        ["Monthly", "Annual"],
        horizontal=True,
        key=f"allocation_unit_{scope}",
    )
    factor = 12 if unit == "Monthly" else 1
    st.caption(
        "Cash goals are funded in order. Available to spend is unallocated cash, not an addition to the Spending budget."
    )
    changed = False
    remove_id = move_id = None
    remainder = next(r["goal"]["id"] for r in rows if r["cash_amount"] is None)
    names = {
        r["goal"]["id"]: catalogue.get(
            r["goal"]["id"], Goal.model_validate(r["goal"])
        ).name
        for r in rows
    }
    names[remainder] = "Available to spend"
    for i, row in enumerate(rows):
        goal_id = row["goal"]["id"]
        if goal_id in catalogue:
            row["goal"] = catalogue[goal_id].model_dump(mode="json")
        label, cash, shares, remove = st.columns([2, 1, 1, 1])
        label.markdown(f"**{names[goal_id]}**")
        if goal_id != remainder:
            if label.button(
                "Move up", key=f"priority_up_{goal_id}_{scope}", disabled=i == 0
            ):
                move_id = goal_id
            with cash:
                original = Decimal(str(row["cash_amount"]))
                key = f"allocation_cash_{goal_id}_{unit}_{scope}"
                value = st.number_input(
                    f"Additional savings / {'month' if factor == 12 else 'year'}",
                    min_value=0.0,
                    value=float(original / factor),
                    format="%.2f",
                    key=key,
                    on_change=_cash_capture,
                    args=(row, key, factor, original),
                )
                row["cash_amount"] = str(
                    original
                    if value == float(original / factor)
                    else dollars(Decimal(str(value)) * factor)
                )
        else:
            cash.caption("Leftover cash")
        with shares:
            percent(
                row,
                "rsu_rate",
                "Retained RSU shares (%)",
                f"allocation_rsu_{goal_id}_{scope}",
            )
        if remove.button(
            "Remove",
            key=f"remove_allocation_{goal_id}_{scope}",
            disabled=goal_id == remainder
            or bool(Decimal(str(row["cash_amount"] or 0)))
            or row["rsu_rate"] not in ("0", "0.0", "0.00")
            or goal_id in (data["retirement_goal_id"], data["option_goal_id"]),
        ):
            remove_id = goal_id
    for field, destination_label in (
        ("retirement_goal_id", "Retirement contributions designated for"),
        ("option_goal_id", "Shares from option exercises designated for"),
    ):
        choice(
            data,
            field,
            destination_label,
            list(names),
            f"allocation_{field}_{scope}",
            labels=names,
        )
    unused = {key: goal for key, goal in catalogue.items() if key not in names}
    if unused:
        selected = st.selectbox(
            "Goal to add",
            list(unused),
            format_func=lambda key: unused[key].name,
            key=f"allocation_add_{scope}",
        )
        if st.button("Add goal to scenario", key=f"add_goal_{scope}"):
            rows.append(
                {
                    "goal": unused[selected].model_dump(mode="json"),
                    "cash_amount": "0",
                    "rsu_rate": "0",
                }
            )
            changed = True
    if remove_id:
        rows[:] = [r for r in rows if r["goal"]["id"] != remove_id]
        changed = True
    if move_id:
        i = next(i for i, r in enumerate(rows) if r["goal"]["id"] == move_id)
        rows[i - 1], rows[i] = rows[i], rows[i - 1]
        changed = True
    return changed
