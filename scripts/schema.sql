-- Apply once with a database owner. The app role needs SELECT/INSERT/UPDATE/DELETE.
CREATE TABLE IF NOT EXISTS scenarios (
    id UUID PRIMARY KEY,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    document JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CHECK (document ? 'id' AND document ->> 'id' = id::text),
    CHECK (document ? 'revision' AND (document ->> 'revision')::integer = revision),
    CHECK (document ->> 'schema_version' = '1')
);
