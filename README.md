# financial-model

A household decision-planning app built with Streamlit. Its purpose is
to understand how choices such as moving, changing jobs, or changing spending
affect cash available today, future wealth, and retirement options.

Quicken Simplifi remains the source for tracking expenses and current accounts.
This tool connects a proposed decision to changes in compensation, taxes,
expenses, saving, and ultimately future outcomes. Imports can provide starting
facts; transaction tracking is outside the product's purpose.

Compare one year's income, living costs, taxes, cash savings, and retained equity
across California and Washington scenarios, or forecast saved Plans through
retirement with shared market simulations and visible funding shortfalls. See
[the planning objective](docs/concepts/decision-planning.md) and
[the forecasting guide](docs/how-to-guides/forecasting.md).

Pydantic validates inputs and saved results; a pinned PolicyEngine module
calculates federal, state, and employee payroll taxes.

## Run locally

Install [Pixi](https://pixi.sh), then:

```bash
pixi install --locked
pixi run --locked setup
pixi run --locked app
```

Open <http://localhost:8501>. Each browser session starts with an empty SQLite
database. Choose **Load example** to explore a fictional household, **Open database**
to upload your own database, or **New database** to start over. Enter household
assumptions and use the normal **Save** actions to keep scenarios, Plans, Wealth,
and forecasts in that session's database.

Choose **Download database** before closing or refreshing the browser tab,
replacing its database, or restarting the app. The download contains all saved
records, including their identities and revisions; unsaved editor drafts are
excluded. Upload that file next time to continue. Keep it private: it contains
household financial data.

Each session has an isolated copy. Two people opening the same file can make
different changes, but the app does not synchronize or merge their copies. Pass
the latest downloaded file to the next person before they edit. No database
server or credentials are required. See the [database workflow and private
deployment guide](docs/how-to-guides/index.md#work-with-a-database-file).

The example uses invented, rounded amounts: two adults earning a combined
\$95,000, \$120,000 in financial accounts, and a \$300,000 home with a mortgage.
Its two Plans compare current spending with a lower spending budget. It includes
retirement dates, illustrative benefits, and investment assumptions so you can
run a forecast. All example inputs are fictional and editable.
[Try the example](docs/getting-started/index.md).

[Usage and deployment](docs/how-to-guides/index.md) ·
[Calculation assumptions](docs/concepts/index.md) ·
[Data model reference](docs/reference/index.md)

## Scope

The annual model supports 2025 and 2026, married filing jointly, adults aged 18–64, qualifying
children, full-year CA/WA wage income, traditional 401(k) contributions, RSUs,
and qualifying ISO exercise-and-hold. The app uses standard deductions.
**2026 California income-tax parameters are projected in the pinned engine.**
Forecasts start from a 2026 Wealth snapshot and use an explicit frozen-real-2026
tax policy, account withdrawal rules, retirement benefits, and fixed planning
lifetimes. Fixed, Monte Carlo, supplied historical, bootstrap, and stress modes
share the same account ledger. Moving-year tax returns, detailed tax lots,
forecast option exercises, and retirement-age optimization remain outside scope.
Read [forecast assumptions](docs/concepts/forecasting.md) before using results.

## Development

```bash
pixi run --locked fmt
pixi run --locked test
pixi run --locked mypy
pixi run --locked docs-build
pixi run --locked check-cloud
pixi run --locked python scripts/check_public_content.py
```

Database tests use temporary SQLite databases and run without a database server.
An optional `postgres-export` Pixi environment provides the driver for the
[one-time export of an existing PostgreSQL database](docs/how-to-guides/index.md#export-an-existing-postgresql-database).

`pyproject.toml` and `pixi.lock` are the dependency source of truth.
`pixi run --locked export-cloud` generates the Linux `environment.yml` used by
Streamlit Community Cloud. Commit it alongside intentional dependency changes.

Source is in `src/financial_model/`; tests mirror it in `tests/financial_model/`.
Private design and research notes live in `~/git/notes/financial-model/`, linked
through the ignored `_dev` directory. Notes have separate Git history.
Keep household databases and backups in private storage outside this repository.
The app opens a session copy and never writes back to the original file; download
changes and save them to your private location after each editing session.

Before sharing source or a package, run the privacy check above. An optional
`--private-terms /path/to/private-terms.json` checks a private JSON array of names
or phrases; that file must live outside the public repository. Add `--history`
to check all reachable Git revisions, or `--directory /path/to/unpacked-release`
to check release contents too. Findings report locations without printing
matched values. The check detects common credential formats and private file
types; it complements a manual review of examples and documentation.

Share a Git-derived archive or a checked package. A ZIP of the working directory
can include ignored personal databases, exports, credentials, and private notes.
