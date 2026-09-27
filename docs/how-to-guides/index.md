# Using and Hosting the App

## Create and edit scenarios

**Scenarios** is the home page. Each saved scenario shows its location, tax year,
monthly cash surplus or shortfall, and annual net saving from income.

- Choose **New scenario** to start from scratch, or **Duplicate** to
  start from an existing scenario. A duplicate stays unsaved until you save it.
- Choose **Edit** to open a scenario. **Calculate** updates its preview without
  saving. **Save** calculates and saves the current inputs and matching results.
- **Unsaved changes** identifies edits that are not in the shared database.
  A separate notice identifies results that need recalculation.

Edits stay in your browser session when you switch pages or open another
scenario. Use a scenario's **Edit** button or the **Unsaved drafts** section to
return. Save before closing the browser.

If another person has changed or deleted a scenario, your save cannot overwrite
that change. Keep your version with **Save as new scenario**, or use
**Reload saved version → Discard edits and reload** when a saved version still
exists. **Refresh** updates the library without replacing your drafts.

## Compare scenarios

Open **Compare** in the top navigation and select two to four scenarios.
Remove a selection with its × button; this does not delete the saved scenario.
Selections stay in place when you switch pages. All selected scenarios must use
the same tax year and current calculation rules.

Each view uses a table with the same scenario columns and order. On a narrow
screen, scroll horizontally while row labels stay visible. Expand **Definitions**
for the meaning of an amount.

**Overview** opens first. It shows monthly cash surplus or shortfall, followed by
annual cash added, retirement contributions, retained RSUs, and net saving.
Cash moved into option shares appears when present. Monthly and annual amounts
are grouped separately.

**Cash flow** groups average monthly income, taxes, living costs, and savings.
ISO exercise costs appear when present. Retained RSUs and employer retirement
are excluded from spendable cash. Minus signs show cash going out.

**Compensation** separates annual salary and bonus, RSUs vesting before tax, and
employer retirement contributions. Expand **By person** to see each adult's
amounts. The separate options table shows annual vesting counts, per-share
prices, share value if exercised, cost to exercise, and value above cost.
An unknown estimate stays unknown; a dash means no matching person or award.

**Saving & equity** separates net cash added, retirement contributions, retained
RSU shares. Cash added is planned cash saving plus the cash
surplus or shortfall. Retirement has restricted access; shares have changing
values and may have sale restrictions. Shares acquired by exercising ISOs appear
separately. **Net saved from income** combines cash, retirement, and estimated
RSU value; it is not spendable cash. It is compensation less taxes and living
costs, including any cash shortfall. It excludes option award values, exercise
spread, and investment growth. The table shows the reconciliation directly;
**Net cash added** is a subtotal. Cash paid for option shares is an asset transfer.

The editor uses these same four views for a single calculated scenario. A cash
shortfall means planned spending and saving need additional cash; the app does
not reduce planned saving or sell more shares automatically.

Turn on **Show differences** and choose a **Reference scenario** to add signed
changes beneath values in every table. Changes use the same units as the row:
monthly dollars, annual dollars, option counts, or per-share prices. For cash
outflows, a more negative change means more cash going out. Differences are
neutral comparisons, not ratings of whether a scenario is better. Unknown
values have no difference. The reference choice stays when you hide differences
or switch pages; removing that scenario selects the first remaining scenario.

To compare without saving, calculate in the editor and choose **Compare results**.
The last calculated draft appears as a separate choice. **Keep snapshot** retains
one calculated version while you change and recalculate the draft. Both session
results are labeled; uncalculated edits never replace their inputs.

Dollar totals are rounded to whole dollars; per-share prices retain up to six
decimal places. Calculations retain cents. See
[calculation assumptions](../concepts/index.md) for the meaning of each total.

## Enter option compensation

In each adult's **Stock option award** section, select **Include an option
award**. Enter ISO or NSO, the number vesting this year, and exercise price per
share. Enter an assumed share value if known; leave it blank otherwise.

The **Compensation** view shows option awards separately and estimates the value
above exercise cost before tax.
This describes this year's vesting, not the full multiyear grant or all vested
options. It does not change cash, taxes, or saving totals, and a share-value
estimate does not imply that the shares can be sold.

Use **ISO exercise and hold** separately for actual qualifying exercises held
through year end. NSO exercises and share sales are not supported.

## Delete a scenario

In **Scenarios**, choose **Delete** on the scenario's card. Check the name
in the confirmation dialog, then choose **Delete scenario** or **Cancel**.
Deletion removes the shared record for both people and its draft in your current
session. It cannot be undone. Download a backup first if you want to keep a copy.

A scenario changed since the dialog opened cannot be deleted until you refresh
and review it again. Deleted scenarios disappear from comparison selectors.
An open draft in another session can only be saved as a new scenario.

## Back up and recover

In the editor's **Backups** section, download the last calculated scenario as
JSON. To restore it, upload the file under **Backups** in Scenarios or the editor and choose
**Load backup as new draft**, then calculate and save. Imports are limited to
1 MB and the supported schema. Keep these files private: they contain household
financial inputs. Backups exclude edits made after the last calculation.

## Run the local database

The configured local database uses Docker Desktop and `compose.yaml`:

```bash
docker compose up -d --wait
pixi run --locked app
```

The database is `financial_model`, on `127.0.0.1:5433`. Its data lives in the
`financial-model-postgres` Docker volume. `docker compose stop` stops the
service without removing that data; `docker compose up -d --wait` starts it
again. Keep the named volume when recreating the container.

Local credentials are in the ignored `.env` and `.streamlit/secrets.toml` files.
The app uses a separate account with read/create/update/delete access to scenarios.
These files and the volume belong to this computer; cloning the repository does
not copy the database. Local access does not provide a shared hosted URL.

For a database backup, choose a private destination outside the application
repository and run:

```bash
docker compose exec -T db pg_dump -U financial_model_admin -d financial_model \
  --format=custom > /path/to/private/financial-model.dump
```

The dump contains financial inputs and results. Keep it private. Scenario-level
JSON backups are also available in the app.

## Connect another PostgreSQL database

Create a dedicated database, then apply `scripts/schema.sql` using its SQL
console or a PostgreSQL client. The app never creates or migrates tables on
startup. Give its runtime role `SELECT`, `INSERT`, `UPDATE`, and `DELETE` on `scenarios`,
plus schema usage and database connection rights. Use a separate owner for
schema changes.

Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`, then replace
the placeholder with the runtime role's connection string. Alternatively set
`FINANCIAL_MODEL_DATABASE_URL` in the server environment. Remote connections
require `sslmode=verify-full`; the system CA store is the default. Credentials
remain on the server and are not part of scenario exports.

Restart the app and save a synthetic scenario. Open another browser session and
verify that it can load it. Use a separate test database for automated tests;
never use the family database for testing.

## Prepare a private deployment

The proposed host is private Streamlit Community Cloud with managed PostgreSQL.
Hosting is a separate setup step; running the app locally does not publish it.

1. Confirm that the household accepts both providers and that a private app slot
   is available. Configure the database as above.
2. Generate deployment dependencies with `pixi run --locked export-cloud` and
   verify them with `pixi run --locked check-cloud`. Commit `environment.yml`.
3. Deploy the private GitHub repository with entry point `streamlit_app.py` and
   Python 3.12. Community Cloud installs the generated `environment.yml`.
4. Put `database_url` in the host's secrets settings. Restrict app access to the
   household's two email addresses before entering family data.
5. Verify both people can sign in, an uninvited browser cannot access data, two
   sessions detect competing saves, and saved scenarios survive a restart and
   redeploy. Verify JSON recovery and acceptable calculation time with both
   sessions in use.

Community Cloud currently supports one private app per workspace; invited
viewers can invite others. Treat sharing as trusted-family access. Local files
on the host are not durable storage. Hosting capacity, authentication, and
restart behavior must pass these checks before the deployment is ready.

Sources: [private sharing](https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app),
[dependency formats](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies),
[storage](https://docs.streamlit.io/develop/concepts/connections/connecting-to-data).

## Update tax rules intentionally

Change pinned dependencies through Pixi, regenerate `environment.yml`, and run
the tax reference suite. Review every changed expectation against the cited
primary source before accepting it. Update the engine constants, correction
revision when corrections change, and model revision when accounting changes.
Never update expected results solely to match a new engine output.

Run formatting, tests, type checks, and documentation build. Test the exported
Linux environment before redeploying. Recalculate existing scenarios to compare
them with the updated rules; saving remains explicit.
