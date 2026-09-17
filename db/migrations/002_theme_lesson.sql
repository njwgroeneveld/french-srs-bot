-- Optional reference to the lesson a theme belongs to, e.g. the Obsidian note
-- 'Frans/Lessen/2026-W38 A1.1 U4 Transport'. NULL for themes that are not tied to a lesson.

ALTER TABLE french.themes ADD COLUMN lesson text;
