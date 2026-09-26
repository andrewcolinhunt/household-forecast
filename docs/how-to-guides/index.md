# Using and Hosting the App

## Compare scenarios

1. Select a saved scenario or enter a new one. Submit the form with **Calculate**.
2. Select **Use these results as comparison baseline** to keep a baseline in
   this session. Saved scenarios can also serve as baselines.
3. Change the inputs and calculate again. Choose the baseline below Results.
4. Read the difference as **alternative minus baseline**. Both scenarios must
   use the same tax year and current calculation rules.

The table uses annual amounts except the labeled monthly cash row. See
[calculation assumptions](../concepts/index.md) for the meaning of each total.

## Save, duplicate, and recover

**Calculate and save** calculates the visible form and saves matching inputs
and results together. **Save as new scenario** creates a separate record; an
unchanged name gets a “(copy)” suffix. **Calculate** alone leaves storage alone.
Save before changing the selected scenario.

If someone else saved first, your save is rejected and your draft stays open.
Choose **Save as new scenario** to keep both versions, or **Reload saved** to
discard your draft and load the latest shared version. **Refresh saved list**
updates the list without replacing the current draft.

Under **Backups**, download the last calculated scenario as JSON. To restore it,
upload that file and choose **Load backup as new draft**, then calculate and
save. Imports are limited to 1 MB and the supported schema. Keep these files
private: they contain household financial inputs.

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
The app uses a separate account with read/create/update access to scenarios.
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
startup. Give its runtime role `SELECT`, `INSERT`, and `UPDATE` on `scenarios`,
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
