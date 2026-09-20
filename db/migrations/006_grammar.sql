-- Grammar sentences live in the same tables as words: one item, two cards. The extra columns
-- are NULL for every existing (vocabulary) row, so nothing has to be backfilled.

ALTER TABLE french.items ADD COLUMN kind text NOT NULL DEFAULT 'vocab'
    CHECK (kind IN ('vocab', 'grammar'));
ALTER TABLE french.items ADD COLUMN sentence text;      -- the sentence with the ___ gap
ALTER TABLE french.items ADD COLUMN gap_answer text;    -- what belongs in the gap
ALTER TABLE french.items ADD COLUMN rule text;          -- one line of explanation

ALTER TABLE french.themes ADD COLUMN choices text[];    -- the buttons, shared by the theme

-- The FSRS state the card had *before* a review, so an override can redo the scheduling
-- exactly instead of stacking a second review on top of the first.
ALTER TABLE french.reviews ADD COLUMN prev_state jsonb;
ALTER TABLE french.reviews ADD COLUMN overridden boolean NOT NULL DEFAULT false;

-- Which kind of cards the running batch is about: /grammar keeps serving grammar.
ALTER TABLE french.bot_state ADD COLUMN batch_kind text
    CHECK (batch_kind IS NULL OR batch_kind IN ('vocab', 'grammar'));

ALTER TABLE french.cards DROP CONSTRAINT IF EXISTS cards_direction_check;
ALTER TABLE french.cards ADD CONSTRAINT cards_direction_check
    CHECK (direction IN ('fr_nl', 'nl_fr', 'gap', 'translate'));
