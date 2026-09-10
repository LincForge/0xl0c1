-- 0xL0C1 schema. Four tables. Brought to the hackathon as an empty stub.
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS place (
    id          TEXT PRIMARY KEY,
    label       TEXT NOT NULL,
    path        TEXT NOT NULL,               -- dotted: house.upstairs.bath
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS object (
    id             TEXT PRIMARY KEY,
    label          TEXT NOT NULL,
    aliases_json   TEXT NOT NULL DEFAULT '[]',
    attrs_json     TEXT NOT NULL DEFAULT '{}',  -- material, mounting, vendor, model_no, owned_since
    place_id       TEXT REFERENCES place(id),
    tag_code       TEXT UNIQUE,                  -- printed Crockford Base32 short code, e.g. K94B
    verbatim_text  TEXT NOT NULL DEFAULT '',     -- what the vision model read off the object
    description    TEXT NOT NULL DEFAULT '',
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lesson (
    id               TEXT PRIMARY KEY,
    object_id        TEXT NOT NULL REFERENCES object(id) ON DELETE CASCADE,
    title            TEXT NOT NULL,
    intent           TEXT,
    next_question    TEXT,                        -- the carried open question
    is_cursor_active INTEGER NOT NULL DEFAULT 1,  -- exactly one active cursor per object
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claim (
    id          TEXT PRIMARY KEY,
    lesson_id   TEXT NOT NULL REFERENCES lesson(id) ON DELETE CASCADE,
    text        TEXT NOT NULL,
    confidence  REAL,
    status      TEXT NOT NULL DEFAULT 'asserted'
);

CREATE INDEX IF NOT EXISTS idx_object_place    ON object(place_id);
CREATE INDEX IF NOT EXISTS idx_object_tag      ON object(tag_code) WHERE tag_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_lesson_object   ON lesson(object_id);
CREATE INDEX IF NOT EXISTS idx_lesson_cursor   ON lesson(object_id) WHERE is_cursor_active = 1;
CREATE INDEX IF NOT EXISTS idx_claim_lesson    ON claim(lesson_id);
