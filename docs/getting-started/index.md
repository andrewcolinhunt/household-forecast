# Getting Started

Run the app locally with Python dependencies managed by [Pixi](https://pixi.sh):

```bash
pixi install --locked
pixi run --locked setup
pixi run --locked app
```

Open <http://localhost:8501>. The first calculation loads the tax engine and can
take longer than later calculations. No tax-service account is required.

Each session starts with an empty database. To continue previous work, use
**Open database** to upload your latest SQLite database file. **New database**
replaces the current session's database with an empty one.

To try the app first, choose **Load example** and confirm the replacement. This
loads fictional household assumptions and two saved Plans. Open **Forecast** and
compare **Current spending** with **Lower spending** to see the effect of a smaller
annual budget. You can edit and save these inputs, download your own copy, or use
**New database** to start with an empty database. Loading the example never changes
the file you originally opened.

Open **Scenarios**. Its sub-tabs collect Plans, Household, Wealth, Work,
Spending, Retirement, Investments, Economy, and Goals. **Compare annual plans**
remains a separate results page. **Forecast** projects saved combined Plans
through retirement.

For a one-year calculation, choose **Plans → New scenario** in the Annual plans
section. Enter the label and tax year, then follow **Edit Household**, **Edit Work**,
**Edit Spending**, and **Edit Investments** to enter each set of inputs. The banner
identifies the annual plan you are editing. Compensation and contributions are
annual amounts; rent, childcare, and other living expenses are monthly.

Return to **Plans**, read **Supported household and calculation assumptions**,
confirm the facts, and select **Calculate**. Results describe the last calculated
inputs. Choose **Save** to keep those inputs and results in this session's database.

To collect longer-term assumptions, create reusable profiles in **Household**,
**Work**, **Spending**, **Retirement**, **Investments**, and **Economy**. Household
profiles save family configurations separately from annual calculation defaults;
residence belongs to each combined Plan. Use **All Work assumptions** or the
corresponding action to leave an annual editing context. Then choose **Plans → New
combined plan** and select a Household and Wealth snapshot. Choose one scenario
per financial category to hold it fixed, or **Add alternative** to vary it. All
alternatives share the Plan's Household copy. Save the Plan, then open
**Forecast** to select combinations and enter the additional account and benefit
details needed to [project future wealth](#run-forecast).

Before closing or refreshing the browser tab or replacing its database, save your
edits and choose **Download database**. This keeps all saved records in one file
you can upload next time. Unsaved editor drafts are not included. New input saves
can be incomplete; **What still needs input** lists what is still unknown.

Sessions are independent, including when two people upload the same file. Share
the latest download to hand work to another person; changes do not synchronize
automatically. Keep database files private. Category-level JSON backups remain
available when you need to move an individual scenario or snapshot.
Store private databases outside the application repository. To keep one in a
separate notes repository, confirm that repository is private and save each
updated download there; saving in the app does not update that private file.

Next, [compare and save scenarios](../how-to-guides/index.md) or read
[how the calculations work](../concepts/index.md).
