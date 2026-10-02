# Getting Started

Run the app locally with Python dependencies managed by [Pixi](https://pixi.sh):

```bash
pixi install --locked
pixi run --locked setup
pixi run --locked app
```

Open <http://localhost:8501>. The first calculation loads the tax engine and can
take longer than later calculations. No tax-service account is required.

Open **Scenarios**. Its sub-tabs collect Plans, Household, Wealth, Work,
Spending, Retirement, Investments, Economy, and Goals. **Compare annual plans**
remains a separate results page.

For a one-year calculation, choose **Plans → New scenario** in the Annual plans
section. Enter the label and tax year, then follow **Edit Household**, **Edit Work**,
**Edit Spending**, and **Edit Investments** to enter each set of inputs. The banner
identifies the annual plan you are editing. Compensation and contributions are
annual amounts; rent, childcare, and other living expenses are monthly.

Return to **Plans**, read **Supported household and calculation assumptions**,
confirm the facts, and select **Calculate**. Results describe the last calculated
inputs. Choose **Save** to keep those inputs and results in the shared store.

To collect longer-term assumptions, create reusable profiles in **Work**, **Spending**,
**Retirement**, **Investments**, and **Economy**. Use **All Work assumptions** or the
corresponding action to leave an annual editing context. Then choose **Plans → New
combined plan** and select a Wealth snapshot. Choose one scenario per category to
hold it fixed, or **Add alternative** to vary it. The app previews the combinations;
it does not yet calculate future wealth or retirement outcomes.

Without a database connection, drafts last for the browser session. Use
**Backups → Download calculated scenario** for annual results, or the category's
backup section to download current valid assumptions or Wealth. New input saves
can be incomplete; **What still needs input** lists what is still unknown.

Next, [compare and save scenarios](../how-to-guides/index.md) or read
[how the calculations work](../concepts/index.md).
