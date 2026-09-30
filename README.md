# financial-model

A shared household decision-planning app built with Streamlit. Its purpose is
to understand how choices such as moving, changing jobs, or changing spending
affect cash available today, future wealth, and retirement options.

Quicken Simplifi remains the source for tracking expenses and current accounts.
This tool connects a proposed decision to changes in compensation, taxes,
expenses, saving, and ultimately future outcomes. Imports can provide starting
facts; transaction tracking is outside the product's purpose.

The current version compares one year's income, living costs, taxes, cash
savings, and retained equity across California and Washington scenarios.
Multi-year and retirement projections are planned, not implemented. See
[the planning objective](docs/concepts/decision-planning.md) and
[the annual calculation model](docs/concepts/index.md).

Pydantic validates inputs and saved results; a pinned PolicyEngine module
calculates federal, state, and employee payroll taxes.

## Run locally

Install [Pixi](https://pixi.sh), then:

```bash
pixi install --locked
pixi run --locked setup
pixi run --locked app
```

Open <http://localhost:8501>. Enter both adults' ages and household assumptions,
then select **Calculate**. Without a database, the app runs in local preview:
sessions are temporary, saving is disabled, and JSON backups keep your work.

This computer's database is configured through `compose.yaml`. With Docker
Desktop running, start it with `docker compose up -d --wait`. PostgreSQL listens
only on `127.0.0.1:5433`; its named volume preserves scenarios across restarts.
The ignored `.env` and `.streamlit/secrets.toml` files hold local credentials.
See [local database operations](docs/how-to-guides/index.md#run-the-local-database).

For shared persistence, create the table with `scripts/schema.sql` in a dedicated
PostgreSQL database. Set `FINANCIAL_MODEL_DATABASE_URL`, or copy
`.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and fill in the
connection string. Remote connections require verified TLS. Keep credentials
out of Git.

[Usage and deployment](docs/how-to-guides/index.md) ·
[Calculation assumptions](docs/concepts/index.md) ·
[Data model reference](docs/reference/index.md)

## Scope

Supports 2025 and 2026, married filing jointly, adults aged 18–64, qualifying
children, full-year CA/WA wage income, traditional 401(k) contributions, RSUs,
and qualifying ISO exercise-and-hold. The app uses standard deductions.
**2026 California income-tax parameters are projected in the pinned engine.**
Moving-year returns, stock sales, Simplifi imports, and retirement projections
are outside this version. See the calculation reference before using a scenario.

## Development

```bash
pixi run --locked fmt
pixi run --locked test
pixi run --locked mypy
pixi run --locked docs-build
pixi run --locked check-cloud
```

Database tests skip unless `FINANCIAL_MODEL_TEST_DATABASE_URL` points to a local,
dedicated database whose name ends in `_test`. Each test creates an isolated
schema. CI supplies PostgreSQL and runs these tests. Never point tests at the
family database.

`pyproject.toml` and `pixi.lock` are the dependency source of truth.
`pixi run --locked export-cloud` generates the Linux `environment.yml` used by
Streamlit Community Cloud. Commit it alongside intentional dependency changes.

Source is in `src/financial_model/`; tests mirror it in `tests/financial_model/`.
Private design and research notes live in `~/git/notes/financial-model/`, linked
through the ignored `_dev` directory. Notes have separate Git history.
