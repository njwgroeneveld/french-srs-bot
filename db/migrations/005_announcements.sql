-- Short release notes are pushed once, when the bot starts with something new in it.
-- Remember per user which one they last received, so a restart never repeats them and
-- a redeploy of unrelated code stays silent.

ALTER TABLE french.users ADD COLUMN last_announcement int NOT NULL DEFAULT 0;
