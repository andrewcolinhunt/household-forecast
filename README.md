# financial-model

A shared household planning app built with Streamlit. Compare annual income,
living costs, taxes, cash savings, and retained equity across California and
Washington scenarios. Pydantic validates inputs and saved results; a pinned
PolicyEngine module calculates federal, state, and employee payroll taxes.

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
