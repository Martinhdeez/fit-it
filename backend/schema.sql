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

-- Marketplace (ADR 0022, docs/specs/marketplace.md). Rows of features and feature_versions are never deleted.
CREATE TABLE IF NOT EXISTS features (
    id          bigserial   PRIMARY KEY,
    owner_id    text        NOT NULL,
    hook_name   text        NOT NULL,
    title       text        NOT NULL,
    description text        NOT NULL,
    version     integer     NOT NULL,
    published   boolean     NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (owner_id, hook_name)
);

CREATE TABLE IF NOT EXISTS feature_versions (
    feature_id   bigint      NOT NULL REFERENCES features (id),
    version      integer     NOT NULL,
    code         text        NOT NULL,
    route        text        NOT NULL,
    published_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (feature_id, version)
);

-- Subscription order is ascending id; accepting an update changes version in place.
CREATE TABLE IF NOT EXISTS subscriptions (
    id         bigserial   PRIMARY KEY,
    user_id    text        NOT NULL,
    feature_id bigint      NOT NULL,
    version    integer     NOT NULL,
    enabled    boolean     NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, feature_id),
    FOREIGN KEY (feature_id, version) REFERENCES feature_versions (feature_id, version)
);

CREATE TABLE IF NOT EXISTS reports (
    id         bigserial   PRIMARY KEY,
    feature_id bigint      NOT NULL REFERENCES features (id),
    user_id    text        NOT NULL,
    reason     text        NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
