-- Core storage, docs/specs/storage.md. Idempotent: db.init() runs it at every start.
CREATE TABLE IF NOT EXISTS hooks (
    user_id    text        NOT NULL,
    name       text        NOT NULL,
    route      text        NOT NULL,
    code       text        NOT NULL,
    enabled    boolean     NOT NULL DEFAULT true,
    version    integer     NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, name)
);

-- Append-only; no foreign key to hooks: versions outlive a deleted hook (ADR 0018).
CREATE TABLE IF NOT EXISTS hook_versions (
    user_id  text        NOT NULL,
    name     text        NOT NULL,
    version  integer     NOT NULL,
    code     text        NOT NULL,
    saved_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, name, version)
);

CREATE TABLE IF NOT EXISTS messages (
    id         bigserial   PRIMARY KEY,
    user_id    text        NOT NULL,
    turn       jsonb       NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS messages_user_id_id ON messages (user_id, id);
