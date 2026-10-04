-- Apply once with a database owner. The app role needs SELECT/INSERT/UPDATE/DELETE.
CREATE TABLE IF NOT EXISTS scenarios (
    id UUID PRIMARY KEY,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    document JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CHECK (document ? 'id' AND document ->> 'id' = id::text),
    CHECK (document ? 'revision' AND (document ->> 'revision')::integer = revision),
    CONSTRAINT scenarios_schema_version CHECK (document ->> 'schema_version' = '2')
);


CREATE TABLE IF NOT EXISTS goals (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 80),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1)
);

CREATE TABLE IF NOT EXISTS scenario_goals (
    scenario_id UUID NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
    goal_id UUID NOT NULL REFERENCES goals(id) ON DELETE RESTRICT,
    PRIMARY KEY (scenario_id, goal_id)
);


CREATE TABLE IF NOT EXISTS household (
    id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    document JSONB NOT NULL,
    CHECK (document ->> 'schema_version' = '1'),
    CHECK ((document ->> 'revision')::integer = revision)
);


CREATE TABLE IF NOT EXISTS wealth_snapshots (
    id UUID PRIMARY KEY,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    document JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CHECK (document ? 'id' AND document ->> 'id' = id::text),
    CHECK (document ? 'revision' AND (document ->> 'revision')::integer = revision),
    CHECK ((document ->> 'schema_version') IS NOT DISTINCT FROM '2'),
    CHECK ((document ->> 'document_type') IS NOT DISTINCT FROM 'wealth_snapshot')
);


CREATE TABLE IF NOT EXISTS planning_documents (
    id UUID PRIMARY KEY,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    document JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CHECK ((document ->> 'id') IS NOT DISTINCT FROM id::text),
    CHECK (((document ->> 'revision')::integer) IS NOT DISTINCT FROM revision),
    CHECK ((document ->> 'schema_version') IS NOT DISTINCT FROM '3'),
    CHECK ((document ->> 'document_type') IS NOT DISTINCT FROM 'planning_document'),
    CHECK ((document -> 'inputs' ->> 'kind'
           IN ('work', 'spending', 'investments', 'economy', 'retirement', 'plan')) IS TRUE)
);


-- Forecasts are inserted once; the runtime role only needs SELECT and INSERT.
CREATE TABLE IF NOT EXISTS forecast_runs (
    id UUID PRIMARY KEY,
    document JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT forecast_runs_document_id
        CHECK ((document ->> 'id') IS NOT DISTINCT FROM id::text),
    CONSTRAINT forecast_runs_document_type
        CHECK ((document ->> 'document_type') IS NOT DISTINCT FROM 'forecast'),
    CONSTRAINT forecast_runs_document_timestamp
        CHECK (((document ->> 'created_at')::timestamptz) IS NOT DISTINCT FROM created_at)
);

-- Keep original immutable runs intact while allowing the current format.
-- Replace either auto-generated name used by the original version-1 schema.
ALTER TABLE forecast_runs
    DROP CONSTRAINT IF EXISTS forecast_runs_document_check,
    DROP CONSTRAINT IF EXISTS forecast_runs_document_check1,
    DROP CONSTRAINT IF EXISTS forecast_runs_schema_version,
    ADD CONSTRAINT forecast_runs_schema_version
        CHECK ((document ->> 'schema_version' IN ('1', '2', '3')) IS TRUE);
