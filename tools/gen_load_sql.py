"""Generate one idempotent SQL file for the Supabase SQL editor from theme YAML files.

Connections from the cluster to Supabase are unreliable (slow Wi-Fi, and the pooler blocks new
connections after a burst of failures), so content is loaded by pasting SQL into the editor.
The editor gives each statement its own session: no temporary tables, only CTEs.
"""

from __future__ import annotations

import sys
from pathlib import Path

from french_srs_bot.importer.theme_file import ThemeFile, read_theme_file


def q(value: str | None) -> str:
    if value is None:
        return "NULL::text"
    return "'" + value.replace("'", "''") + "'"


def arr(values: list[str]) -> str:
    return "ARRAY[" + ", ".join(q(v) for v in values) + "]::text[]"


MIGRATION_008 = """ALTER TABLE french.items DROP CONSTRAINT IF EXISTS items_kind_check;
ALTER TABLE french.items ADD CONSTRAINT items_kind_check
    CHECK (kind IN ('vocab', 'grammar', 'sentence'));
ALTER TABLE french.bot_state DROP CONSTRAINT IF EXISTS bot_state_batch_kind_check;
ALTER TABLE french.bot_state ADD CONSTRAINT bot_state_batch_kind_check
    CHECK (batch_kind IS NULL OR batch_kind IN ('vocab', 'grammar', 'sentence'));
INSERT INTO french.schema_migrations (version) VALUES ('008_sentence') ON CONFLICT DO NOTHING;
"""
"""db/migrations/008_sentence.sql, idempotent, and recorded as applied. Every SQL file that
(re)writes the kind CHECKs uses these lists: an older, narrower list would refuse sentence rows
that already exist."""


def vocab_sql(themes: list[ThemeFile]) -> str:
    """One staging VALUES list over all themes, in the shape data/load-a1.5.sql proved out.

    Cards are deliberately not created here: they exist per user and the bot adds them with
    db.sync_cards on startup, so the restart that picks up the new words also hands them to
    everyone at once.
    """
    rows = [
        f"  ({q(t.source)}, {q(t.source_ref)}, {q(t.level)}, {q(t.name)}, {t.position},"
        f" {q(t.lesson)}, {position}, {arr(item.french)}, {arr(item.dutch)},"
        f" {q(item.english)}, {q(item.gender)}, {q(item.hint)})"
        for t in themes
        for position, item in enumerate(t.items, start=1)
    ]
    names = ", ".join(f"{t.position}: {t.name}" for t in themes)
    staging_values = ",\n".join(rows)
    return f"""-- {names}
-- Veilig om vaker te draaien: bestaande items worden bijgewerkt, kaarten (en hun FSRS-
-- voortgang) blijven ongemoeid. Plak dit in de Supabase SQL-editor en draai alles in een keer.

WITH staging (source, source_ref, level, name, theme_position, lesson,
              item_position, french, dutch, english, gender, hint) AS (
  VALUES
{staging_values}
),
upserted_themes AS (
  INSERT INTO french.themes (source, source_ref, level, name, position, lesson)
  SELECT DISTINCT source, source_ref, level, name, theme_position, lesson FROM staging
  ON CONFLICT (source, source_ref) DO UPDATE
  SET level = EXCLUDED.level, name = EXCLUDED.name, position = EXCLUDED.position, lesson = EXCLUDED.lesson
  RETURNING id, source, source_ref
),
upserted_items AS (
  INSERT INTO french.items (theme_id, position, french, dutch, english, gender, hint, kind)
  SELECT t.id, s.item_position, s.french, s.dutch, s.english, s.gender::char(1), s.hint, 'vocab'
  FROM staging s JOIN upserted_themes t ON t.source = s.source AND t.source_ref = s.source_ref
  ON CONFLICT (theme_id, (french[1])) DO UPDATE
  SET position = EXCLUDED.position, french = EXCLUDED.french, dutch = EXCLUDED.dutch,
      english = EXCLUDED.english, gender = EXCLUDED.gender, hint = EXCLUDED.hint, kind = EXCLUDED.kind
  RETURNING id
)
SELECT (SELECT count(*) FROM upserted_themes) AS themes_written,
       (SELECT count(*) FROM upserted_items) AS items_written;

-- Kaarten worden hier niet aangemaakt: die bestaan per gebruiker en worden door de bot
-- bijgemaakt (db.sync_cards, bij het opstarten). Herstart de bot na deze import, dan heeft
-- iedereen kaarten voor de nieuwe woorden:
--   kubectl rollout restart deployment/french-srs-bot -n french

-- Controle: staat alles in de juiste volgorde?
SELECT t.position, t.source, t.name, count(i.id) AS items
FROM french.themes t LEFT JOIN french.items i ON i.theme_id = t.id
GROUP BY t.id, t.position, t.source, t.name ORDER BY t.position;
"""


def sentence_sql(themes: list[ThemeFile]) -> str:
    """Themes, items and one nl_fr card per user, for one or more sentence themes.

    Like grammar, the cards are written here, so the sentences can be practised without a
    restart; a sentence has only the one direction.
    """
    rows = [
        f"  ({q(t.source)}, {q(t.source_ref)}, {q(t.level)}, {q(t.name)}, {t.position},"
        f" {q(t.lesson)}, {position}, {arr(item.french)}, {arr(item.dutch)}, {q(item.hint)})"
        for t in themes
        for position, item in enumerate(t.items, start=1)
    ]
    staging_values = ",\n".join(rows)
    names = ", ".join(f"{t.position}: {t.name}" for t in themes)
    return f"""-- {names}
-- Thema's, zinnen en per gebruiker een nl_fr-kaart. Veilig om vaker te draaien: bestaande
-- items worden bijgewerkt, kaarten (en hun FSRS-voortgang) blijven ongemoeid.
-- Plak dit in de Supabase SQL-editor en draai alles in een keer.

-- 1. Migratie 008 is misschien nog niet gedraaid (de bot doet hem bij het opstarten); pas hem
-- toe en noteer dat. Hij is idempotent, dus de volgorde (eerst SQL of eerst uitrol) maakt niet uit.
{MIGRATION_008}
-- 2. De thema's, hun zinnen en de kaarten, in een statement.
WITH staging (source, source_ref, level, name, theme_position, lesson,
              item_position, french, dutch, hint) AS (
  VALUES
{staging_values}
),
upserted_themes AS (
  INSERT INTO french.themes (source, source_ref, level, name, position, lesson)
  SELECT DISTINCT source, source_ref, level, name, theme_position, lesson FROM staging
  ON CONFLICT (source, source_ref) DO UPDATE
  SET level = EXCLUDED.level, name = EXCLUDED.name, position = EXCLUDED.position,
      lesson = EXCLUDED.lesson
  RETURNING id, source, source_ref
),
upserted_items AS (
  INSERT INTO french.items (theme_id, position, french, dutch, english, gender, hint, kind)
  SELECT t.id, s.item_position, s.french, s.dutch, NULL, NULL, s.hint, 'sentence'
  FROM staging s JOIN upserted_themes t ON t.source = s.source AND t.source_ref = s.source_ref
  ON CONFLICT (theme_id, (french[1])) DO UPDATE
  SET position = EXCLUDED.position, french = EXCLUDED.french, dutch = EXCLUDED.dutch,
      hint = EXCLUDED.hint, kind = EXCLUDED.kind
  RETURNING id
),
new_cards AS (
  INSERT INTO french.cards (user_id, item_id, direction)
  SELECT u.id, i.id, d.direction
  FROM french.users u CROSS JOIN upserted_items i
  CROSS JOIN (VALUES ('nl_fr')) AS d(direction)
  ON CONFLICT (user_id, item_id, direction) DO NOTHING
  RETURNING id
)
SELECT (SELECT count(*) FROM upserted_themes) AS themes_written,
       (SELECT count(*) FROM upserted_items) AS items_written,
       (SELECT count(*) FROM new_cards) AS cards_added;

-- 3. Wat staat er nu in de database?
SELECT t.position, t.chapter, t.name, count(i.id) AS items
FROM french.themes t LEFT JOIN french.items i ON i.theme_id = t.id
GROUP BY t.id, t.position, t.chapter, t.name ORDER BY t.position;

-- 4. Hoeveel herhalingen staan er de komende 7 dagen voor gebruiker 1? Daarmee kies je
-- daily_new: veel herhalingen = minder nieuwe zinnen per dag.
SELECT (due AT TIME ZONE 'Europe/Amsterdam')::date AS dag, count(*) AS herhalingen
FROM french.cards WHERE user_id = 1 AND introduced_at IS NOT NULL
  AND due < now() + interval '7 days'
GROUP BY 1 ORDER BY 1;
"""


def grammar_sql(themes: list[ThemeFile]) -> str:
    """Themes, items and a gap/translate card pair per user, for one or more grammar themes.

    Unlike vocab, the cards are written here: a grammar item needs both directions and the
    pair is fixed, so there is nothing for db.sync_cards to work out later.
    """
    rows = [
        f"  ({q(t.source)}, {q(t.source_ref)}, {q(t.level)}, {q(t.name)}, {t.position},"
        f" {q(t.lesson)}, {arr(t.choices) if t.choices else 'NULL::text[]'},"
        f" {position}, {arr(item.french)}, {arr(item.dutch)}, {q(item.sentence)},"
        f" {q(item.gap_answer)}, {q(item.rule)}, {q(item.hint)})"
        for t in themes
        for position, item in enumerate(t.items, start=1)
    ]
    staging_values = ",\n".join(rows)
    names = ", ".join(f"{t.position}: {t.name}" for t in themes)
    return f"""-- {names}
-- Thema's, items en per gebruiker een gap/translate-paar. Veilig om vaker te draaien.
-- Plak dit in de Supabase SQL-editor en draai alles in een keer.

-- 1. Migratie 006 is misschien nog niet gedraaid; pas hem toe en noteer dat, zodat de
-- migratierunner van de bot niet dezelfde kolommen nog eens probeert toe te voegen.
ALTER TABLE french.items ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'vocab';
ALTER TABLE french.items ADD COLUMN IF NOT EXISTS sentence text;
ALTER TABLE french.items ADD COLUMN IF NOT EXISTS gap_answer text;
ALTER TABLE french.items ADD COLUMN IF NOT EXISTS rule text;
ALTER TABLE french.themes ADD COLUMN IF NOT EXISTS choices text[];
ALTER TABLE french.reviews ADD COLUMN IF NOT EXISTS prev_state jsonb;
ALTER TABLE french.reviews ADD COLUMN IF NOT EXISTS overridden boolean NOT NULL DEFAULT false;
ALTER TABLE french.bot_state ADD COLUMN IF NOT EXISTS batch_kind text;
-- De CHECK op cards.direction die 006 inline schrijft, onder de naam die PostgreSQL daaraan
-- geeft, zodat een hier geladen database identiek is aan een normaal gemigreerde.
ALTER TABLE french.cards DROP CONSTRAINT IF EXISTS cards_direction_check;
ALTER TABLE french.cards ADD CONSTRAINT cards_direction_check
    CHECK (direction IN ('fr_nl', 'nl_fr', 'gap', 'translate'));
INSERT INTO french.schema_migrations (version) VALUES ('006_grammar') ON CONFLICT DO NOTHING;
-- De CHECKs op items.kind en bot_state.batch_kind, in de vorm van migratie 008: met de
-- smallere lijst van 006 zou dit bestaande zinnen weigeren.
{MIGRATION_008}
-- 2. De thema's, hun items en de kaarten, in een statement.
WITH staging (source, source_ref, level, name, theme_position, lesson, choices,
              item_position, french, dutch, sentence, gap_answer, rule, hint) AS (
  VALUES
{staging_values}
),
upserted_themes AS (
  INSERT INTO french.themes (source, source_ref, level, name, position, lesson, choices)
  SELECT DISTINCT source, source_ref, level, name, theme_position, lesson, choices FROM staging
  ON CONFLICT (source, source_ref) DO UPDATE
  SET level = EXCLUDED.level, name = EXCLUDED.name, position = EXCLUDED.position,
      lesson = EXCLUDED.lesson, choices = EXCLUDED.choices
  RETURNING id, source, source_ref
),
upserted_items AS (
  INSERT INTO french.items
      (theme_id, position, french, dutch, english, gender, hint, kind, sentence, gap_answer, rule)
  SELECT t.id, s.item_position, s.french, s.dutch, NULL, NULL, s.hint,
         'grammar', s.sentence, s.gap_answer, s.rule
  FROM staging s JOIN upserted_themes t ON t.source = s.source AND t.source_ref = s.source_ref
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
SELECT (SELECT count(*) FROM upserted_themes) AS themes_written,
       (SELECT count(*) FROM upserted_items) AS items_written,
       (SELECT count(*) FROM new_cards) AS cards_added;

-- 3. Wat staat er nu in de database?
SELECT t.position, t.chapter, t.name, t.kind_label, t.items FROM (
  SELECT t.position, t.chapter, t.name, max(i.kind) AS kind_label, count(i.id) AS items
  FROM french.themes t LEFT JOIN french.items i ON i.theme_id = t.id
  GROUP BY t.id, t.position, t.chapter, t.name
) t ORDER BY t.position;
"""


def main(out_path: str, yaml_paths: list[str]) -> None:
    # The arguments used to be the other way round. Refusing the old order rather than
    # guessing it: silently swapping would write SQL over a theme file on the first rerun
    # out of shell history, and those files are not in git.
    if out_path.endswith((".yaml", ".yml")) or any(p.endswith(".sql") for p in yaml_paths):
        sys.exit(f"usage: {Path(sys.argv[0]).name} OUT.sql IN.yaml [IN.yaml ...]")
    themes = [read_theme_file(Path(path)) for path in yaml_paths]
    kinds = {theme.kind for theme in themes}
    if len(kinds) > 1:
        # Each writes different rows: vocab leaves the cards to the bot, grammar writes a
        # gap/translate pair itself, sentence one nl_fr card. One file would have to do
        # each part conditionally.
        sys.exit(f"mixed kinds {sorted(kinds)}: generate one file per kind")
    if kinds == {"grammar"}:
        sql = grammar_sql(themes)
    elif kinds == {"sentence"}:
        sql = sentence_sql(themes)
    else:
        sql = vocab_sql(themes)
    Path(out_path).write_text(sql, encoding="utf-8")
    items = sum(len(theme.items) for theme in themes)
    print(f"wrote {out_path} ({len(themes)} theme(s), {items} items)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
