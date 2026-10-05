PRAGMA application_id = 1179471692;
PRAGMA user_version = 1;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS scenarios (
    id TEXT PRIMARY KEY NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    document TEXT NOT NULL CHECK (json_valid(document)),
    updated_at TEXT NOT NULL,
    CHECK ((document ->> 'id' = id) IS TRUE),
    CHECK ((json_type(document, '$.revision') = 'integer'
        AND document ->> 'revision' = revision) IS TRUE),
    CHECK ((json_type(document, '$.schema_version') = 'integer'
        AND document ->> 'schema_version' = 2) IS TRUE)
) STRICT;

CREATE TABLE IF NOT EXISTS goals (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 80),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1)
) STRICT;

CREATE TABLE IF NOT EXISTS scenario_goals (
    scenario_id TEXT NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
    goal_id TEXT NOT NULL REFERENCES goals(id) ON DELETE NO ACTION,
    PRIMARY KEY (scenario_id, goal_id)
) STRICT;

CREATE TABLE IF NOT EXISTS household (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    document TEXT NOT NULL CHECK (json_valid(document)),
    CHECK ((json_type(document, '$.schema_version') = 'integer'
        AND document ->> 'schema_version' = 1) IS TRUE),
    CHECK ((json_type(document, '$.revision') = 'integer'
        AND document ->> 'revision' = revision) IS TRUE)
) STRICT;

CREATE TABLE IF NOT EXISTS wealth_snapshots (
    id TEXT PRIMARY KEY NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    document TEXT NOT NULL CHECK (json_valid(document)),
    updated_at TEXT NOT NULL,
    CHECK ((document ->> 'id' = id) IS TRUE),
    CHECK ((json_type(document, '$.revision') = 'integer'
        AND document ->> 'revision' = revision) IS TRUE),
    CHECK ((json_type(document, '$.schema_version') = 'integer'
        AND document ->> 'schema_version' = 2) IS TRUE),
    CHECK ((document ->> 'document_type' = 'wealth_snapshot') IS TRUE)
) STRICT;

CREATE TABLE IF NOT EXISTS planning_documents (
    id TEXT PRIMARY KEY NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    document TEXT NOT NULL CHECK (json_valid(document)),
    updated_at TEXT NOT NULL,
    CHECK ((document ->> 'id' = id) IS TRUE),
    CHECK ((json_type(document, '$.revision') = 'integer'
        AND document ->> 'revision' = revision) IS TRUE),
    CHECK ((json_type(document, '$.schema_version') = 'integer'
        AND document ->> 'schema_version' = 4) IS TRUE),
    CHECK ((document ->> 'document_type' = 'planning_document') IS TRUE),
    CHECK ((document -> 'inputs' ->> 'kind' IN
        ('household', 'work', 'spending', 'investments', 'economy', 'retirement', 'plan')) IS TRUE)
) STRICT;

CREATE TABLE IF NOT EXISTS forecast_runs (
    id TEXT PRIMARY KEY NOT NULL,
    document TEXT NOT NULL CHECK (json_valid(document)),
    created_at TEXT NOT NULL,
    CHECK ((document ->> 'id' = id) IS TRUE),
    CHECK ((document ->> 'document_type' = 'forecast') IS TRUE),
    CHECK ((julianday(document ->> 'created_at') = julianday(created_at)) IS TRUE),
    CHECK ((json_type(document, '$.schema_version') = 'integer'
        AND document ->> 'schema_version' = 5) IS TRUE)
) STRICT;
