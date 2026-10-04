"""One-at-a-time fixed forecasts of explicit changes to a frozen Plan."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from financial_model.forecast import calculate_forecast
from financial_model.forecast_models import ForecastRequest, ForecastSettings
from financial_model.planning import PlanningScenario

if TYPE_CHECKING:
    from financial_model.forecast import Progress


@dataclass(frozen=True)
class SensitivityPoint:
    """One year-end balance in the Plan's reference-year dollars."""

    year: int
    wealth_real: float
    accessible_real: float


@dataclass(frozen=True)
class SensitivityCase:
    """A fixed curve, or the reason a requested assumption was invalid."""

    label: str
    status: Literal["ok", "invalid"]
    annual: tuple[SensitivityPoint, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class SensitivityDriver:
    """The lower and higher input values for one controlled change."""

    key: str
    label: str
    range_label: str
    description: str
    low: SensitivityCase
    high: SensitivityCase


@dataclass(frozen=True)
class SensitivityResult:
    """An independent fixed baseline and seven assumption comparisons."""

    plan_name: str
    money_year: int
    baseline: SensitivityCase
    drivers: tuple[SensitivityDriver, ...]


_DRIVERS = (
    "spending",
    "adult_1",
    "adult_2",
    "equity_return",
    "inflation",
    "cash_pay",
    "starting_assets",
)
_RUNS = 1 + 2 * len(_DRIVERS)


def _scale(value: Decimal | None, factor: Decimal) -> Decimal | None:
    return (value * factor).quantize(Decimal(".01")) if value is not None else None


def _scale_spending(profiles: dict[str, Any], factor: Decimal) -> None:
    for expense in profiles["spending"]["expenses"]:
        if expense["frequency"] == "recurring":
            for level in expense["levels"]:
                level["amount"] = {
                    key: _scale(value, factor) for key, value in level["amount"].items()
                }


def _shift(values: dict[str, Any], offset: Decimal | int) -> None:
    for key, value in values.items():
        if value is not None:
            values[key] = value + offset


def _shift_retirement(data: dict[str, Any], slot: int, offset: int) -> None:
    selection = next(
        c for c in data["components"] if c["inputs"]["kind"] == "retirement"
    )
    adult = selection["inputs"]["adults"][selection["adult_mapping"].index(slot)]
    _shift(adult["retirement"], offset)
    person = data["household"]["adults"][slot - 1]
    end = data["household"]["reference_year"] + adult["age"]["central"] - person["age"]
    if adult["retirement"]["central"] > end:
        msg = f"{person['name']}: retirement is after the planning lifetime ends in {end}."
        raise ValueError(msg)


def _variant(plan: PlanningScenario, key: str, direction: int) -> PlanningScenario:
    data = plan.model_dump()
    profiles = {c["inputs"]["kind"]: c["inputs"] for c in data["components"]}
    factor = Decimal(1) + Decimal(direction) / 10
    if key == "spending":
        _scale_spending(profiles, Decimal(1) + Decimal(direction) / 5)
    elif key in {"adult_1", "adult_2"}:
        _shift_retirement(data, int(key[-1]), direction * 2)
    elif key in {"equity_return", "inflation"}:
        field = "equities" if key == "equity_return" else "inflation"
        _shift(profiles["economy"][field], Decimal(direction) / 100)
    elif key == "cash_pay":
        for adult in profiles["work"]["adults"]:
            for phase in adult["phases"]:
                phase["cash_pay"] = _scale(phase["cash_pay"], factor)
    elif key == "starting_assets":
        for account in data["wealth"]["snapshot"]["accounts"]:
            for field in ("balance", "cost_basis"):
                account[field] = _scale(account[field], factor)
    return PlanningScenario.model_validate(data)


def _labels(plan: PlanningScenario, key: str) -> tuple[str, str, str, str, str]:
    if key in {"adult_1", "adult_2"}:
        slot = int(key[-1])
        name = plan.household.adults[slot - 1].name
        if plan.household.adults[0].name == plan.household.adults[1].name:
            name = f"{name} (adult {slot})"
        return (
            f"{name}'s retirement",
            "±2 years",
            "2 years earlier",
            "2 years later",
            "Move retirement and its linked spending and investment dates. Keep the "
            "entered work schedules, benefit claiming ages, and planning lifespans.",
        )
    if key in {"equity_return", "inflation"}:
        economy = next(c.inputs for c in plan.components if c.inputs.kind == "economy")
        central = getattr(
            economy, "equities" if key == "equity_return" else key
        ).central
        low, high = (
            f"{((central + delta) * 100).normalize():f}%"
            for delta in (Decimal("-.01"), Decimal(".01"))
        )
        description = (
            "Change the nominal stock compound return; keep bond and cash returns, "
            "dividend yields, and allocation rules."
            if key == "equity_return"
            else "Change inflation throughout the forecast, including indexed income, "
            "spending, and tax assumptions. Deflate each result using its own inflation."
        )
        return (
            "Stock return" if key == "equity_return" else "Inflation",
            f"{low} to {high}",
            low,
            high,
            description,
        )
    return {
        "spending": (
            "Recurring spending",
            "±20%",
            "80% of entered spending",
            "120% of entered spending",
            "Scale every amount in recurring expense schedules. Keep their dates, "
            "growth rates, and one-time spending unchanged.",
        ),
        "cash_pay": (
            "Household cash pay",
            "±10%",
            "90% of entered cash pay",
            "110% of entered cash pay",
            "Scale cash pay in both adults' entered work phases. Keep equity compensation, "
            "growth, schedules, and contribution rules; pay-based contributions follow pay.",
        ),
        "starting_assets": (
            "Starting financial assets",
            "±10%",
            "90% of opening accounts",
            "110% of opening accounts",
            "Scale opening account balances and their entered cost bases proportionally. "
            "Keep debts, account ownership, and investment rules unchanged.",
        ),
    }[key]


def _fixed_case(
    plan: PlanningScenario, label: str, index: int, progress: Progress | None
) -> SensitivityCase:
    def report(detail: str, fraction: float) -> None:
        if progress:
            progress(f"{label}: {detail}", (index + fraction) / _RUNS)

    report("Calculating fixed forecast", 0)
    result = calculate_forecast(
        ForecastRequest(
            plans=(plan,), settings=ForecastSettings(mode="fixed", count=1)
        ),
        progress=report,
    )
    points = result.document.summaries[0].points
    report("Complete", 1)
    return SensitivityCase(
        label,
        "ok",
        tuple(SensitivityPoint(p.year, p.median, p.accessible_median) for p in points),
    )


def calculate_sensitivity(
    plan: PlanningScenario, progress: Progress | None = None
) -> SensitivityResult:
    """Calculate a fixed baseline and fourteen independent changes to a saved Plan.

    Args:
        plan: One frozen, fully specified combination of household inputs.
        progress: Optional message and fraction callback across all fifteen runs.

    Returns:
        Reference-year balances and labels for the baseline and each tested input range.
        Invalid variants contain a reason instead of a curve. Ranges are illustrative,
        not probabilities; all untested inputs retain their entered values.

    Raises:
        ValueError: When the baseline Plan cannot be forecast.
        RuntimeError: When any calculation fails reconciliation or its numerical solver.
    """
    baseline = _fixed_case(plan, "Fixed baseline", 0, progress)
    assert plan.household.reference_year is not None
    drivers = []
    for driver_index, key in enumerate(_DRIVERS):
        label, range_label, low, high, description = _labels(plan, key)
        cases = []
        for side, case_label in enumerate((low, high)):
            index = 1 + driver_index * 2 + side
            try:
                variant = _variant(plan, key, -1 if side == 0 else 1)
                case = _fixed_case(variant, f"{label}: {case_label}", index, progress)
            except ValueError as exc:
                case = SensitivityCase(
                    f"{label}: {case_label}", "invalid", error=str(exc)
                )
                if progress:
                    progress(f"{case.label}: Invalid assumption", (index + 1) / _RUNS)
            cases.append(case)
        drivers.append(SensitivityDriver(key, label, range_label, description, *cases))
    return SensitivityResult(
        plan.name, plan.household.reference_year, baseline, tuple(drivers)
    )
