"""Independent statistical and chronological checks for joint forecast paths."""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pytest

from financial_model.forecast_paths import generate_paths, parse_history_csv
from financial_model.planning import EconomicAssumptions


@pytest.fixture
def economy() -> EconomicAssumptions:
    return EconomicAssumptions(
        name="Synthetic outlook",
        equities={"central": "0.06", "lower": "-0.2", "upper": "0.3"},
        fixed_income={"central": "0.03"},
        cash={"central": "0.02"},
        inflation={"central": "0.025"},
        volatility=("0.18", "0.06", "0.01", "0.02"),
        correlation=(
            (1, "0.2", 0, "-0.1"),
            ("0.2", 1, "0.1", "-0.2"),
            (0, "0.1", 1, "0.3"),
            ("-0.1", "-0.2", "0.3", 1),
        ),
    )


@pytest.fixture
def history() -> tuple[tuple[float, float, float, float], ...]:
    return tuple((i / 100, i / 200, i / 300, i / 400) for i in range(12))


def test_generate_paths_fixed_requires_no_statistics(
    economy: EconomicAssumptions,
) -> None:
    inputs = economy.model_copy(update={"volatility": (None,) * 4, "correlation": None})
    paths = generate_paths(inputs, (2026, 2027), count=100, mode="fixed")
    assert paths.shape == (1, 2, 4)
    np.testing.assert_array_equal(paths, [[[0.06, 0.03, 0.02, 0.025]] * 2])


def test_generate_paths_zero_volatility_matches_fixed_exactly(
    economy: EconomicAssumptions,
) -> None:
    inputs = economy.model_copy(update={"volatility": (0,) * 4, "correlation": None})
    fixed = generate_paths(inputs, (2026, 2027), mode="fixed")
    random = generate_paths(inputs, (2026, 2027), count=20)
    np.testing.assert_array_equal(random, np.repeat(fixed, 20, axis=0))


def test_generate_paths_moments_match_compound_centers_and_simple_volatility(
    economy: EconomicAssumptions,
) -> None:
    values = generate_paths(economy, (2026,), count=200_000, seed=86)[:, 0]
    central = np.array([0.06, 0.03, 0.02, 0.025])
    np.testing.assert_allclose(
        np.log1p(values).mean(axis=0), np.log1p(central), atol=0.001
    )
    np.testing.assert_allclose(values.std(axis=0), [0.18, 0.06, 0.01, 0.02], rtol=0.01)
    np.testing.assert_allclose(
        np.corrcoef(np.log1p(values), rowvar=False),
        np.array(economy.correlation, dtype=float),
        atol=0.008,
    )
    assert values[:, 0].mean() > central[0] + 0.01
    assert (values > -1).all()


def test_generate_paths_defaults_match_published_relationship_targets() -> None:
    economy = EconomicAssumptions(
        name="Default simulation",
        equities={"central": "0.06"},
        fixed_income={"central": "0.045"},
        cash={"central": "0.03"},
        inflation={"central": "0.025"},
    )
    values = generate_paths(economy, (2026,), count=200_000, seed=86)[:, 0]
    np.testing.assert_allclose(values.std(axis=0), [0.18, 0.06, 0.01, 0.02], rtol=0.01)
    # JPM's published relationships are targets for simple returns, not log rates.
    np.testing.assert_allclose(
        np.corrcoef(values, rowvar=False),
        [
            [1, 0.30, 0.01, -0.01],
            [0.30, 1, 0.10, -0.24],
            [0.01, 0.10, 1, -0.03],
            [-0.01, -0.24, -0.03, 1],
        ],
        atol=0.008,
    )
    np.testing.assert_allclose(
        np.log1p(values).mean(axis=0),
        np.log1p([0.06, 0.045, 0.03, 0.025]),
        atol=0.001,
    )


def test_generate_paths_reproducible_and_bounds_unused(
    economy: EconomicAssumptions,
) -> None:
    changed = economy.model_dump()
    changed["equities"] = {"central": "0.06", "lower": "-0.7", "upper": "0.9"}
    alternative = EconomicAssumptions.model_validate(changed)
    years = (2026, 2027, 2028)
    first = generate_paths(economy, years, count=10, seed=32)
    np.testing.assert_array_equal(
        first, generate_paths(economy, years, count=10, seed=32)
    )
    np.testing.assert_array_equal(
        first, generate_paths(alternative, years, count=10, seed=32)
    )
    np.testing.assert_array_equal(
        first, generate_paths(economy, years, count=20, seed=32)[:10]
    )
    assert not np.array_equal(first, generate_paths(economy, years, count=10, seed=33))


def test_generate_paths_outlooks_share_underlying_shocks(
    economy: EconomicAssumptions,
) -> None:
    data = economy.model_dump()
    data["equities"] = {"central": "0.04"}
    alternative = EconomicAssumptions.model_validate(data)
    first = generate_paths(economy, (2026, 2027), count=50)
    second = generate_paths(alternative, (2026, 2027), count=50)
    np.testing.assert_array_equal(first[:, :, 1:], second[:, :, 1:])
    first_shocks = np.log1p(first[:, :, 0]) - np.log1p(0.06)
    second_shocks = np.log1p(second[:, :, 0]) - np.log1p(0.04)
    np.testing.assert_allclose(
        first_shocks / first_shocks.std(), second_shocks / second_shocks.std()
    )


def test_generate_paths_singular_correlations_and_fixed_dimension(
    economy: EconomicAssumptions,
) -> None:
    data = economy.model_dump()
    data.update(correlation=((1,) * 4,) * 4, volatility=("0.18", "0.06", 0, "0.02"))
    paths = generate_paths(EconomicAssumptions.model_validate(data), (2026,), count=100)
    np.testing.assert_array_equal(paths[:, :, 2], np.full((100, 1), 0.02))
    logs = np.log1p(paths[:, 0])
    assert np.corrcoef(logs[:, 0], logs[:, 1])[0, 1] == pytest.approx(1)


@pytest.mark.parametrize(
    "years", [(), (2026, 2028), (2027, 2026), (2026, 2026), (True,)]
)
def test_generate_paths_rejects_invalid_years(
    economy: EconomicAssumptions, years
) -> None:
    with pytest.raises(ValueError, match=r"[Yy]ears"):
        generate_paths(economy, years)


@pytest.mark.parametrize("count", [0, -1, 1.5, True])
def test_generate_paths_rejects_invalid_count(
    economy: EconomicAssumptions, count
) -> None:
    with pytest.raises(ValueError, match="count"):
        generate_paths(economy, (2026,), count=count)


def test_generate_paths_missing_inputs_remain_unknown(
    economy: EconomicAssumptions,
) -> None:
    with pytest.raises(ValueError, match="central outlooks"):
        generate_paths(EconomicAssumptions(name="Unknown"), (2026,), mode="fixed")
    with pytest.raises(ValueError, match="all four annual standard deviations"):
        generate_paths(economy.model_copy(update={"volatility": (None,) * 4}), (2026,))
    with pytest.raises(ValueError, match="correlation matrix"):
        generate_paths(economy.model_copy(update={"correlation": None}), (2026,))


def test_generate_paths_historical_uses_each_complete_window(economy, history) -> None:
    paths = generate_paths(
        economy, (2026, 2027, 2028), mode="historical", history=history
    )
    assert paths.shape == (10, 3, 4)
    for i, path in enumerate(paths):
        np.testing.assert_array_equal(path, history[i : i + 3])
    with pytest.raises(ValueError, match="at least as many"):
        generate_paths(
            economy, tuple(range(2026, 2040)), mode="historical", history=history
        )


def test_generate_paths_bootstrap_preserves_joint_consecutive_blocks(
    economy, history
) -> None:
    years = tuple(range(2026, 2037))
    paths = generate_paths(
        economy, years, mode="bootstrap", history=history, block_years=3, count=40
    )
    assert paths.shape == (40, 11, 4)
    np.testing.assert_array_equal(
        paths,
        generate_paths(
            economy, years, mode="bootstrap", history=history, block_years=3, count=40
        ),
    )
    for path in paths:
        for offset in (0, 3, 6, 9):
            block = path[offset : offset + 3]
            assert any(
                np.array_equal(block, history[i : i + len(block)]) for i in range(10)
            )


@pytest.mark.parametrize("block_years", [0, -1, 13, 1.5, True])
def test_generate_paths_bootstrap_rejects_invalid_blocks(
    economy, history, block_years
) -> None:
    with pytest.raises(ValueError, match="block years"):
        generate_paths(
            economy, (2026,), mode="bootstrap", history=history, block_years=block_years
        )


@pytest.mark.parametrize("mode", ["historical", "bootstrap"])
@pytest.mark.parametrize(
    "history", [(), ((0, 0, 0),), ((0, 0, 0, -1),), ((0, 0, 0, float("nan")),)]
)
def test_generate_paths_rejects_invalid_history(economy, mode, history) -> None:
    with pytest.raises(ValueError, match=r"[Hh]istor"):
        generate_paths(economy, (2026,), mode=mode, history=history)


def test_generate_paths_stresses_have_documented_duration(economy) -> None:
    years = tuple(range(2026, 2038))
    early = generate_paths(economy, years, mode="stress")
    assert early.shape == (1, 12, 4)
    np.testing.assert_array_equal(early[0, :3, 0], [-0.4, -0.2, 0])
    np.testing.assert_array_equal(early[0, 3:, 0], np.full(9, 0.06))
    inflation = generate_paths(
        economy, years, mode="stress", stress="persistent_inflation"
    )
    np.testing.assert_array_equal(inflation[0, :10, 3], np.full(10, 0.08))
    np.testing.assert_array_equal(inflation[0, 10:, 3], np.full(2, 0.025))
    np.testing.assert_array_equal(
        inflation[0, :, :3], np.tile([0.06, 0.03, 0.02], (12, 1))
    )


def test_generate_paths_invalid_mode_and_stress(economy) -> None:
    with pytest.raises(ValueError, match="Unknown forecast path mode"):
        generate_paths(economy, (2026,), mode="invalid")
    with pytest.raises(ValueError, match="Unknown stress"):
        generate_paths(economy, (2026,), mode="stress", stress="invalid")


def test_generate_paths_numerically_unusable_rates_fail(economy) -> None:
    data = economy.model_dump()
    data["equities"] = {"central": Decimal("1e1000")}
    with pytest.raises(ValueError, match="Central rates"):
        generate_paths(EconomicAssumptions.model_validate(data), (2026,))


def test_parse_history_csv_preserves_units_and_joint_observations() -> None:
    csv = "year,equities,fixed_income,cash,inflation\n2000,-0.2,0.05,0.01,0.03\n2001,0.1,0,0.02,-0.01"
    assert parse_history_csv(csv) == ((-0.2, 0.05, 0.01, 0.03), (0.1, 0, 0.02, -0.01))


@pytest.mark.parametrize(
    "body",
    [
        "",
        "2000,0,0,0,0\n2002,0,0,0,0",
        "2000,0,0,0,0\n2000,0,0,0,0",
        "2000,0,0,0",
        "2000,0,0,0,0,0",
        "2000,bad,0,0,0",
        "2000,0,0,0,nan",
        "2000,0,0,0,-1",
    ],
)
def test_parse_history_csv_rejects_incomplete_or_invalid_data(body: str) -> None:
    with pytest.raises(ValueError):
        parse_history_csv("year,equities,fixed_income,cash,inflation\n" + body)


def test_parse_history_csv_rejects_unlabeled_columns() -> None:
    with pytest.raises(ValueError, match="columns"):
        parse_history_csv("2000,0,0,0,0")
