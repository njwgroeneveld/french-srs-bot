-- From a bot for one person to a bot for several. Words and audio stay shared (themes,
-- items, items.voice_file_id); progress becomes per user.
--
-- Every existing card belongs to the only user there has been so far. That user gets a row
-- here with telegram_user_id = 0: a placeholder, because a SQL migration cannot know the
-- real Telegram id. The bot claims it on its next start (db.claim_owner).

CREATE TABLE french.users (
    id                serial      PRIMARY KEY,
    telegram_user_id  bigint      NOT NULL UNIQUE,
    name              text        NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    -- Pace per person. NULL means: follow the shared value from settings.yaml. Only the
    -- pacing knobs are overridable; times and learning steps stay shared, because those
    -- drive one set of scheduler jobs for the whole bot.
    daily_goal        int         CHECK (daily_goal > 0),
    daily_new         int         CHECK (daily_new > 0),
    batch_size        int         CHECK (batch_size > 0)
);

INSERT INTO french.users (telegram_user_id, name) VALUES (0, 'owner');

-- cards: progress becomes per user
ALTER TABLE french.cards ADD COLUMN user_id int REFERENCES french.users (id) ON DELETE CASCADE;
UPDATE french.cards SET user_id = (SELECT id FROM french.users WHERE telegram_user_id = 0);
ALTER TABLE french.cards ALTER COLUMN user_id SET NOT NULL;

ALTER TABLE french.cards DROP CONSTRAINT cards_item_id_direction_key;
ALTER TABLE french.cards ADD CONSTRAINT cards_user_item_direction_key
    UNIQUE (user_id, item_id, direction);

DROP INDEX french.cards_due_idx;
CREATE INDEX cards_due_idx ON french.cards (user_id, due) WHERE introduced_at IS NOT NULL;

-- bot_state: from a single row (id = 1) to a row per user
ALTER TABLE french.bot_state ADD COLUMN user_id int REFERENCES french.users (id) ON DELETE CASCADE;
UPDATE french.bot_state SET user_id = (SELECT id FROM french.users WHERE telegram_user_id = 0);
ALTER TABLE french.bot_state DROP CONSTRAINT bot_state_pkey;
ALTER TABLE french.bot_state DROP COLUMN id;
ALTER TABLE french.bot_state ALTER COLUMN user_id SET NOT NULL;
ALTER TABLE french.bot_state ADD PRIMARY KEY (user_id);
