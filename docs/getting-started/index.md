# Getting Started

Run the app locally with Python dependencies managed by [Pixi](https://pixi.sh):

```bash
pixi install --locked
pixi run --locked setup
pixi run --locked app
```

Open <http://localhost:8501>. The first calculation loads the tax engine and can
take longer than later calculations. No tax-service account is required.

Choose **New scenario** on the Annual plans page, then enter a name, year, and full-year state. Enter both adults' ages, compensation,
and savings assumptions. Enter children's ages only when they meet the
supported qualifying-child facts. Rent, childcare, and other spending are
monthly; compensation and qualifying childcare expenses are annual.

Read **Supported household and calculation assumptions**, confirm the facts,
and select **Calculate**. Review average monthly remaining cash and the annual
breakdown. Results describe the last calculated inputs; edit and calculate again
to update them.

Without a database connection, drafts last for the browser session. Use
**Backups → Download calculated scenario** to keep a portable copy.

Next, [compare and save scenarios](../how-to-guides/index.md) or read
[how the calculations work](../concepts/index.md).
