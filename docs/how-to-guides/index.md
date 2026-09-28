# Using and Hosting the App

## Household and goals

**Household** holds names and ages for a specified year end, qualifying children's
ages, and the supported filing status. Save these defaults once. New scenarios copy
them; existing scenarios, duplicates, imports, and open drafts keep their own facts.
Changing defaults does not update a saved financial calculation.

**Goals** manages shared purposes. Add, rename, or delete unused goals here. Renaming
a goal changes its displayed name across the app, without changing allocations or
financial results. Saved scenario backups retain the names used when saved. A goal
referenced by a saved scenario or an open draft cannot be deleted from this page.

## Create and edit scenarios

**Scenarios** is the home page. Each saved scenario shows its title,
location, tax year, each person's employer, net additions per year,
and the monthly funding gap.

- Choose **New scenario** to start from scratch, or **Duplicate** to
  start from an existing scenario. A duplicate stays unsaved until you save it.
- Choose **Edit** to open a scenario. **Assumptions** contains the year, state,
  family facts, employment, compensation, and expenses. **Allocations** assigns
  that scenario's annual assets to goals. **Results** shows its calculated views.
- **Calculate** opens Results without saving. **Save** calculates and saves the
  current inputs and matching results, then opens Results.
- **Unsaved changes** identifies edits that are not in the shared database.
  A separate notice identifies results that need recalculation.

Leave **Custom label (optional)** blank to use the state as the heading.
Employment details appear beneath it, without an extra work-status category.

Enter a label such as “Higher rent” when useful. Labels need not be unique and
stay as written when inputs change. Clear a label to return to the automatic
title. Existing names remain custom labels until cleared. Duplicate and
**Save as new scenario** preserve the label, including a blank one.

Enter **Employer** within each person's section. Use a short name for table
headings; leave it blank if unknown. Select **Not working in this scenario** to
show that status explicitly. A blank employer or zero salary does not mean
someone is not working. Checking the box removes the current employer when you
calculate or save; it does not change any compensation amounts. Set those
separately. Employer details follow the same calculate/save process as other inputs.

Edits stay in your browser session when you switch pages or open another
scenario. Use a scenario's **Edit** button or the **Unsaved drafts** section to
return. Save before closing the browser.

Reopening a scenario loads a newer saved version when you have no unsaved edits.
If you do have edits, the editor keeps them and shows a warning about the newer
saved version.

If another person has changed or deleted a scenario, your save cannot overwrite
that change. Keep your version with **Save as new scenario**, or use
**Reload saved version → Discard edits and reload** when a saved version still
exists. **Refresh** updates the library without replacing your drafts.

## Allocate assets to goals

In the editor's **Allocations** section:

1. Choose monthly or annual cash entry. Enter fixed amounts and choose one goal
   for the cash remainder. The app stores exact annual amounts.
2. Divide retained RSUs across goals, totaling 100% when RSUs are present.
3. Assign existing 401(k)/employer contributions and exercised option shares to
   goals. These amounts are calculated from compensation, not entered again.
4. Choose **Calculate**, review the annual allocations and funding gap, then save.

**Add goal to scenario** includes an existing shared goal with zero allocations.
Use **Manage household goals** to create or rename goals on the Goals page.
**Remove** removes an unused allocation row only from this scenario. Reassign its
cash, RSUs, and contribution destinations first, then save the scenario.

Retirement, House, and Available to spend are editable names, not special goal
types. Available to spend is still held cash; actual spending belongs in living
costs. Assigning shares does not sell them, and assigning cash does not invest it.

If fixed cash allocations exceed available cash, the remainder is zero and the
plan shows a funding gap. Such scenarios can still be saved and compared.

## Compare scenarios

Open **Compare** in the top navigation and select two to four scenarios.
Remove a selection with its × button; this does not delete the saved scenario.
Selections stay in place when you switch pages. All selected scenarios must use
the same tax year and current calculation rules.

Each view uses a table with the same scenario columns and order. On a narrow
screen, scroll horizontally while row labels stay visible. Expand **Definitions**
for the meaning of an amount.

Column headings and cards show the title above year and employer details. State
appears in the details when a custom label replaces the automatic title.
Selectors use compact versions of the same information.

When titles and context match, the app adds enough differing inputs to distinguish
the scenarios, such as rent per month or a goal's cash allocation. Titles stay
stable when the comparison selection changes. Identical assumptions are marked
**Same assumptions**. Expand **Saved details** to see their last-saved date,
with a short record identifier when dates also match. Selectors include those
details to distinguish identical copies. The database does not store creation
dates. Draft and snapshot labels remain separate. Notes do not count as a change
to financial assumptions.

**Overview** shows planned annual additions by goal, the funding gap, and net
additions. Each asset is counted once. These are annual changes, not current
balances or investment returns.

**Effective tax rate** includes federal/state income taxes and employee payroll
taxes, divided by gross salary, bonus, and vested RSUs. It includes RSU-funded
taxes and any modeled ISO exercise tax. It excludes employer contributions and
option values from the denominator. Zero wages show **N/A**.

**Cash flow** groups average monthly income, taxes, living costs, and savings.
ISO exercise costs appear when present. Retained RSUs and employer retirement
are excluded from spendable cash. Minus signs show cash going out.

**Compensation** separates annual salary and bonus, RSUs vesting before tax, and
employer retirement contributions. Expand **By person** to see each adult's
amounts. The separate options table shows annual vesting counts, per-share
prices, share value if exercised, cost to exercise, and value above cost.
An unknown estimate stays unknown; a dash means no matching person or award.

**Goal allocations** separates each goal's cash, retirement accounts, retained
RSUs, and option shares at purchase cost. A single scenario shows asset columns;
comparisons show scenario columns with expandable asset details for each goal.
Exercise spread is shown separately and excluded from goal funding.

The editor uses the same four views. A funding gap means the plan requires more
cash; no withdrawal, borrowing, or additional share sale is assumed.

Turn on **Show differences** and choose a **Reference scenario** to add signed
changes beneath values in every table. Changes use the same units as the row:
monthly dollars, annual dollars, option counts, or per-share prices. Tax-rate
differences use percentage points (pp), so 25% versus 20% shows +5.0 pp. For cash
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

In the editor's **Results → Backups** section, download the last calculated scenario as
JSON. To restore it, upload the file under **Backups** in Scenarios or the editor and choose
**Load backup as new draft**, then calculate and save. Imports are limited to
1 MB and the supported schema. Keep these files private: they contain household
financial inputs. Backups exclude edits made after the last calculation.

Backups include goal identities and names. If an imported identity has a different
current name, explicitly keep the shared goal or create a separate one. Version-one
backups require review of the proposed destinations before loading. Their old extra
saving becomes a fixed annual Retirement allocation; taxes are recalculated.

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
The app uses a separate account with read/create/update/delete access to scenarios, goals, and scenario-goal references.
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
startup. Give its runtime role `SELECT`, `INSERT`, `UPDATE`, and `DELETE` on `scenarios`, `goals`, `scenario_goals`, and `household`,
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

## Migrate a version-one database

Use an owner connection in `FINANCIAL_MODEL_DATABASE_URL`, with a private folder
outside this repository. The first command writes original backups and a preview;
review the destinations and totals before applying:

```bash
pixi run --locked python scripts/migrate_goal_allocations.py preview /private/path/review
pixi run --locked python scripts/migrate_goal_allocations.py apply /private/path/review
```

The migration preserves scenario IDs and verifies unchanged taxes and net additions.
It stops if records changed after the preview. Give the app role access to the new
`goals` and `scenario_goals` tables after migration. Restart the app once complete.

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
