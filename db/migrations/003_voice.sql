-- Telegram stores the audio itself; we only keep the handle it gives back (file_id),
-- plus the voice and tempo it was made with. Change either and the stored audio is
-- stale, so it is made again. The audio is of the French word, so it belongs to the
-- item: both card directions share it.

ALTER TABLE french.items ADD COLUMN voice_file_id text;
ALTER TABLE french.items ADD COLUMN voice_key     text;
