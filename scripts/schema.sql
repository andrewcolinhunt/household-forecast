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
