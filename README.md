# household-forecast

household-forecast compares how jobs, location, spending, and retirement dates
affect a household's finances. It calculates annual cash flow and projects
assets and debts through retirement.

## How it works

Enter household details, income, expenses, account balances, and planning
assumptions. Save alternatives as Plans, then compare their taxes, available
cash, and future wealth. Forecasts track contributions, investment returns,
retirement income, withdrawals, and funding shortfalls year by year. You can
use fixed returns or simulations to see how results vary.

The Python app uses Streamlit for its interface, Pydantic to validate inputs,
PolicyEngine to calculate taxes, and SQLite to store saved inputs and results.

Annual calculations support California and Washington for 2025 and 2026,
married filing jointly, adults aged 18–64, full-year residence, and standard
deductions. Forecasts start in 2026 and hold 2026 tax rules constant in purchasing
power. See the [annual calculation assumptions](docs/concepts/index.md) and
[forecast assumptions](docs/concepts/forecasting.md) for coverage and limits.

## Run locally

Install [Pixi](https://pixi.sh), then run these commands from the repository
directory:

```bash
pixi install --locked
pixi run --locked setup
pixi run --locked app
```

Open [localhost:8501](http://localhost:8501).

## Use the app

1. Choose **Load example** and confirm to open a fictional household.
2. Open **Forecast**, select **Current spending** and **Lower spending** under
   **Plans to compare**, then choose **Run forecast**.
3. Edit a Plan's assumptions in **Scenarios → Plans**, save the Plan, and run the
   forecast again.

Use **New database** to enter your own household data. **Compare annual plans**
shows differences in one year's income, taxes, spending, and saving.

Each browser session uses a temporary database. Save your edits, then choose
**Download database** before closing or refreshing the tab. Next time, use
**Open database** to load that file. The app does not update the original file
or synchronize separate copies. Store household databases privately, outside
the source repository.

See [getting started](docs/getting-started/index.md) for a walkthrough and the
[usage guide](docs/how-to-guides/index.md) for saving and comparing your own Plans.

## Development

Source code is in `src/financial_model/`; tests are in `tests/financial_model/`.
Pixi manages dependencies through `pyproject.toml` and `pixi.lock`.
See [Contributing](CONTRIBUTING.md) for privacy checks and the development workflow.

```bash
pixi run --locked fmt
pixi run --locked test
pixi run --locked docs-build
```

## License and contact

[BSD 3-Clause](LICENSE). Author: [Andrew Hunt](mailto:andrewcolinhunt@gmail.com).
