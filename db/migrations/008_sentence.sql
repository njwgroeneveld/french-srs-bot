-- Zinskaarten: een derde soort item, alleen NL -> FR (zie spec 2026-11-20).
-- The two CHECKs were declared inline in 006, so Postgres named them <table>_<column>_check.
-- DROP ... IF EXISTS keeps this idempotent: the same SQL also goes to Niels by hand.
ALTER TABLE french.items DROP CONSTRAINT IF EXISTS items_kind_check;
ALTER TABLE french.items ADD CONSTRAINT items_kind_check
    CHECK (kind IN ('vocab', 'grammar', 'sentence'));
ALTER TABLE french.bot_state DROP CONSTRAINT IF EXISTS bot_state_batch_kind_check;
ALTER TABLE french.bot_state ADD CONSTRAINT bot_state_batch_kind_check
    CHECK (batch_kind IS NULL OR batch_kind IN ('vocab', 'grammar', 'sentence'));
