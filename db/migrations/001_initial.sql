-- Initial schema for french-srs-bot. The runner has already created schema "french".

CREATE TABLE french.themes (
    id          serial PRIMARY KEY,
    source      text NOT NULL,
    source_ref  text NOT NULL,
    level       text,
    name        text NOT NULL,
    position    int  NOT NULL,
    UNIQUE (source, source_ref)
);

CREATE TABLE french.items (
    id        serial PRIMARY KEY,
    theme_id  int    NOT NULL REFERENCES french.themes (id) ON DELETE CASCADE,
    position  int    NOT NULL,
    french    text[] NOT NULL CHECK (cardinality(french) > 0),
    dutch     text[] NOT NULL CHECK (cardinality(dutch) > 0),
    english   text,
    gender    char(1) CHECK (gender IN ('m', 'f')),
    hint      text
);

CREATE UNIQUE INDEX items_theme_french_key ON french.items (theme_id, (french[1]));

CREATE TABLE french.cards (
    id             serial PRIMARY KEY,
    item_id        int  NOT NULL REFERENCES french.items (id) ON DELETE CASCADE,
    direction      text NOT NULL CHECK (direction IN ('fr_nl', 'nl_fr')),
    introduced_at  timestamptz,
    due            timestamptz,
    fsrs_state     smallint,
    step           smallint,
    stability      double precision,
    difficulty     double precision,
    last_review    timestamptz,
    UNIQUE (item_id, direction)
);

CREATE INDEX cards_due_idx ON french.cards (due) WHERE introduced_at IS NOT NULL;

CREATE TABLE french.reviews (
    id           bigserial PRIMARY KEY,
    card_id      int         NOT NULL REFERENCES french.cards (id) ON DELETE CASCADE,
    reviewed_at  timestamptz NOT NULL,
    answer       text        NOT NULL,
    grade        text        NOT NULL CHECK (grade IN ('correct', 'almost', 'hard', 'wrong')),
    rating       smallint    NOT NULL,
    due_before   timestamptz,
    due_after    timestamptz NOT NULL
);

CREATE INDEX reviews_reviewed_at_idx ON french.reviews (reviewed_at);

CREATE TABLE french.bot_state (
    id               int PRIMARY KEY CHECK (id = 1),
    pending_card_id  int REFERENCES french.cards (id) ON DELETE SET NULL,
    pending_since    timestamptz,
    batch_remaining  smallint NOT NULL DEFAULT 0
);

INSERT INTO french.bot_state (id) VALUES (1);
