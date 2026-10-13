-- 0xL0C1 / LINC Loci multi-tenant schema (PostgreSQL 16 flavour).
-- Mirrors schema.sql table-for-table and adds Row-Level Security (RLS) policies.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS ltree;

CREATE TABLE IF NOT EXISTS space (
    id                    TEXT PRIMARY KEY,
    slug                  TEXT NOT NULL UNIQUE,
    name                  TEXT NOT NULL,
    tier                  TEXT NOT NULL DEFAULT 'free',
    capability_token      TEXT NOT NULL UNIQUE,
    capability_token_hash TEXT NOT NULL UNIQUE,
    tag_prefix            TEXT NOT NULL DEFAULT '',
    created_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS membership (
    id              TEXT PRIMARY KEY,
    space_id        TEXT NOT NULL REFERENCES space(id) ON DELETE CASCADE,
    subject         TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'writer',
    token_hash      TEXT UNIQUE,
    created_at      TEXT NOT NULL,
    UNIQUE(space_id, subject)
);

CREATE TABLE IF NOT EXISTS place (
    id              TEXT PRIMARY KEY,
    space_id        TEXT NOT NULL REFERENCES space(id) ON DELETE CASCADE,
    label           TEXT NOT NULL,
    path            TEXT NOT NULL,               -- dotted: house.upstairs.bath
    created_at      TEXT NOT NULL,
    UNIQUE(space_id, id),
    UNIQUE(space_id, path)
);

CREATE TABLE IF NOT EXISTS object (
    id              TEXT PRIMARY KEY,
    space_id        TEXT NOT NULL REFERENCES space(id) ON DELETE CASCADE,
    label           TEXT NOT NULL,
    canonical_class TEXT NOT NULL DEFAULT '',
    material        TEXT NOT NULL DEFAULT 'composite_or_other',
    mounting        TEXT NOT NULL DEFAULT 'handheld_portable',
    aliases_json    TEXT NOT NULL DEFAULT '[]',
    attrs_json      TEXT NOT NULL DEFAULT '{}',
    place_id        TEXT,
    tag_code        TEXT,                        -- printed visual Base32 short code, e.g. K9-4C
    verbatim_text   TEXT NOT NULL DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    embedding_model TEXT NOT NULL DEFAULT 'deterministic-64d',
    embedding_json  TEXT NOT NULL DEFAULT '[]',
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    UNIQUE(space_id, id),
    UNIQUE(space_id, tag_code),
    FOREIGN KEY (space_id, place_id) REFERENCES place(space_id, id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS lesson (
    id               TEXT PRIMARY KEY,
    space_id         TEXT NOT NULL REFERENCES space(id) ON DELETE CASCADE,
    object_id        TEXT NOT NULL,
    author_subject   TEXT NOT NULL DEFAULT 'anonymous',
    title            TEXT NOT NULL,
    intent           TEXT,
    next_question    TEXT,
    is_cursor_active INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL,
    UNIQUE(space_id, id),
    FOREIGN KEY (space_id, object_id) REFERENCES object(space_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS claim (
    id              TEXT PRIMARY KEY,
    space_id        TEXT NOT NULL REFERENCES space(id) ON DELETE CASCADE,
    lesson_id       TEXT NOT NULL,
    text            TEXT NOT NULL,
    confidence      REAL,
    status          TEXT NOT NULL DEFAULT 'asserted',
    FOREIGN KEY (space_id, lesson_id) REFERENCES lesson(space_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_place_space       ON place(space_id, path);
CREATE INDEX IF NOT EXISTS idx_object_space      ON object(space_id, place_id);
CREATE INDEX IF NOT EXISTS idx_object_tag        ON object(space_id, tag_code) WHERE tag_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_lesson_object     ON lesson(space_id, object_id);
CREATE INDEX IF NOT EXISTS idx_lesson_cursor     ON lesson(space_id, object_id) WHERE is_cursor_active = 1;
CREATE INDEX IF NOT EXISTS idx_claim_lesson      ON claim(space_id, lesson_id);

-- Row-Level Security (RLS) enforcement per space_id
ALTER TABLE place ENABLE ROW LEVEL SECURITY;
ALTER TABLE place FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS space_isolation_policy ON place;
CREATE POLICY space_isolation_policy ON place
    USING (space_id = current_setting('loci.current_space_id', true))
    WITH CHECK (space_id = current_setting('loci.current_space_id', true));

ALTER TABLE object ENABLE ROW LEVEL SECURITY;
ALTER TABLE object FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS space_isolation_policy ON object;
CREATE POLICY space_isolation_policy ON object
    USING (space_id = current_setting('loci.current_space_id', true))
    WITH CHECK (space_id = current_setting('loci.current_space_id', true));

ALTER TABLE lesson ENABLE ROW LEVEL SECURITY;
ALTER TABLE lesson FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS space_isolation_policy ON lesson;
CREATE POLICY space_isolation_policy ON lesson
    USING (space_id = current_setting('loci.current_space_id', true))
    WITH CHECK (space_id = current_setting('loci.current_space_id', true));

ALTER TABLE claim ENABLE ROW LEVEL SECURITY;
ALTER TABLE claim FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS space_isolation_policy ON claim;
CREATE POLICY space_isolation_policy ON claim
    USING (space_id = current_setting('loci.current_space_id', true))
    WITH CHECK (space_id = current_setting('loci.current_space_id', true));
