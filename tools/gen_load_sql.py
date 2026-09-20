"""Generate one idempotent SQL file for the Supabase SQL editor from a theme YAML file.

Connections from the cluster to Supabase are unreliable (slow Wi-Fi, and the pooler blocks new
connections after a burst of failures), so content is loaded by pasting SQL into the editor.
The editor gives each statement its own session: no temporary tables, only CTEs.
"""

from __future__ import annotations

import sys
from pathlib import Path

from french_srs_bot.importer.theme_file import read_theme_file


def q(value: str | None) -> str:
    if value is None:
        return "NULL::text"
    return "'" + value.replace("'", "''") + "'"


def arr(values: list[str]) -> str:
    return "ARRAY[" + ", ".join(q(v) for v in values) + "]::text[]"


def main(yaml_path: str, out_path: str) -> None:
    theme = read_theme_file(Path(yaml_path))
    if theme.kind != "grammar":
        # The SQL below writes kind 'grammar' and a gap/translate card pair: for a vocabulary
        # theme it would create the wrong rows. Those are loaded with the importer.
        sys.exit(f"{yaml_path}: kind is {theme.kind!r}, this script only generates grammar themes")
    rows = [
        f"  ({position}, {arr(item.french)}, {arr(item.dutch)}, {q(item.sentence)},"
        f" {q(item.gap_answer)}, {q(item.rule)}, {q(item.hint)})"
        for position, item in enumerate(theme.items, start=1)
    ]
    staging_values = ",\n".join(rows)
    sql = f"""-- {theme.name}: themes, items and cards. Safe to run more than once.
-- Paste into the Supabase SQL editor and run everything at once.

-- 1. Migration 006 may not have run yet; apply it if needed and record it, so the bot's
-- migration runner does not try to add the same columns again.
ALTER TABLE french.items ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'vocab';
ALTER TABLE french.items ADD COLUMN IF NOT EXISTS sentence text;
ALTER TABLE french.items ADD COLUMN IF NOT EXISTS gap_answer text;
ALTER TABLE french.items ADD COLUMN IF NOT EXISTS rule text;
ALTER TABLE french.themes ADD COLUMN IF NOT EXISTS choices text[];
ALTER TABLE french.reviews ADD COLUMN IF NOT EXISTS prev_state jsonb;
ALTER TABLE french.reviews ADD COLUMN IF NOT EXISTS overridden boolean NOT NULL DEFAULT false;
ALTER TABLE french.bot_state ADD COLUMN IF NOT EXISTS batch_kind text;
-- The CHECKs 006 writes inline in its ADD COLUMNs, under the names PostgreSQL gives those,
-- so a database loaded here ends up identical to a normally migrated one.
ALTER TABLE french.items DROP CONSTRAINT IF EXISTS items_kind_check;
ALTER TABLE french.items ADD CONSTRAINT items_kind_check
    CHECK (kind IN ('vocab', 'grammar'));
ALTER TABLE french.bot_state DROP CONSTRAINT IF EXISTS bot_state_batch_kind_check;
ALTER TABLE french.bot_state ADD CONSTRAINT bot_state_batch_kind_check
    CHECK (batch_kind IS NULL OR batch_kind IN ('vocab', 'grammar'));
ALTER TABLE french.cards DROP CONSTRAINT IF EXISTS cards_direction_check;
ALTER TABLE french.cards ADD CONSTRAINT cards_direction_check
    CHECK (direction IN ('fr_nl', 'nl_fr', 'gap', 'translate'));
INSERT INTO french.schema_migrations (version) VALUES ('006_grammar') ON CONFLICT DO NOTHING;

-- 2. The theme, its items and a card pair per user, in one statement.
WITH staging (item_position, french, dutch, sentence, gap_answer, rule, hint) AS (
  VALUES
{staging_values}
),
upserted_theme AS (
  INSERT INTO french.themes (source, source_ref, level, name, position, lesson, choices)
  VALUES ({q(theme.source)}, {q(theme.source_ref)}, {q(theme.level)}, {q(theme.name)},
          {theme.position}, {q(theme.lesson)}, {arr(theme.choices)})
  ON CONFLICT (source, source_ref) DO UPDATE
  SET level = EXCLUDED.level, name = EXCLUDED.name, position = EXCLUDED.position,
      lesson = EXCLUDED.lesson, choices = EXCLUDED.choices
  RETURNING id
),
upserted_items AS (
  INSERT INTO french.items
      (theme_id, position, french, dutch, english, gender, hint, kind, sentence, gap_answer, rule)
  SELECT t.id, s.item_position, s.french, s.dutch, NULL, NULL, s.hint,
         'grammar', s.sentence, s.gap_answer, s.rule
  FROM staging s CROSS JOIN upserted_theme t
  ON CONFLICT (theme_id, (french[1])) DO UPDATE
  SET position = EXCLUDED.position, french = EXCLUDED.french, dutch = EXCLUDED.dutch,
      hint = EXCLUDED.hint, kind = EXCLUDED.kind, sentence = EXCLUDED.sentence,
      gap_answer = EXCLUDED.gap_answer, rule = EXCLUDED.rule
  RETURNING id
),
new_cards AS (
  INSERT INTO french.cards (user_id, item_id, direction)
  SELECT u.id, i.id, d.direction
  FROM french.users u CROSS JOIN upserted_items i
  CROSS JOIN (VALUES ('gap'), ('translate')) AS d(direction)
  ON CONFLICT (user_id, item_id, direction) DO NOTHING
  RETURNING id
)
SELECT (SELECT count(*) FROM upserted_items) AS items_written,
       (SELECT count(*) FROM new_cards) AS cards_added;

-- 3. What is in the database now
SELECT t.position, t.name, t.kind_label, t.items FROM (
  SELECT t.position, t.name, max(i.kind) AS kind_label, count(i.id) AS items
  FROM french.themes t LEFT JOIN french.items i ON i.theme_id = t.id
  GROUP BY t.id, t.position, t.name
) t ORDER BY t.position;
"""
    Path(out_path).write_text(sql, encoding="utf-8")
    print(f"wrote {out_path} ({len(rows)} items)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
