# french-srs-bot — Design

**Date:** 2026-09-17
**Status:** approved in brainstorming, awaiting spec review
**Repo:** new standalone repo `french-srs-bot` (intended to become public on GitHub)

## 1. Goal

A Telegram bot that quizzes Niels daily on French vocabulary using spaced repetition, in both
directions (FR→NL and NL→FR). Answers are typed, graded automatically with partial credit, and
scheduled with FSRS. The first word source is the A0 theme lists from kwiziq
(`https://french.kwiziq.com/learn/theme`).

### Roadmap (separate specs, not part of v1)

1. **v1 (this spec):** vocabulary SRS bot + kwiziq importer.
2. Cards generated from Obsidian notes of French lessons (likely via Hermes).
3. Grammar practice (sentence translation, "explain when to use X") graded by an LLM.

The v1 data model leaves room for 2 and 3 (`themes.source`), but nothing for them is built now.

### Out of scope for v1

Obsidian import, grammar/LLM grading, the FSRS "Easy" rating, statistics/dashboards, levels above A0
(the importer works for any kwiziq theme, but only A0 is loaded initially), multiple users.

## 2. Language conventions

- Code, identifiers, database objects, commits, README, specs, k8s resources: **English**.
- User-facing bot messages: **Dutch**, all located in `messages.py`.

## 3. Architecture

```
phone (Telegram)  ──long polling──  french-srs-bot pod (k8s ns: french, 1 replica)
                                          │
                                          │ Postgres (psycopg)
                                          ▼
                                    Supabase, schema: french
                                          ▲
                                          │ one-off per theme
                                    importer (runs locally on PC)
```

- **Dedicated Telegram bot** (new token via BotFather). The Hermes bot token cannot be shared: only
  one process may poll a bot token.
- **Deterministic code, no LLM at runtime.** Scheduling and grading are plain Python.
- The bot only responds to the Telegram user id in `TELEGRAM_USER_ID`; all other updates are ignored.
- Timezone for batches, "today", daily goal and streak: `Europe/Amsterdam`.

### Components (`src/french_srs_bot/`)

| module | responsibility | depends on |
|---|---|---|
| `__main__.py` | entry point: load config, run migrations, start polling | all below |
| `bot.py` | Telegram handlers (`/start`, `/practice`, text answers, "Begrepen" button) | `session.py`, `messages.py` |
| `scheduler.py` | registers JobQueue jobs: batches and evening reminder | `bot.py`, `session.py` |
| `session.py` | chooses the next card (new-card slot, sibling rule, batch size, daily goal), handles answers | `db.py`, `grading.py`, `srs.py` |
| `grading.py` | pure: `grade(answer, accepted, lang) -> GradeResult` | — |
| `srs.py` | pure: maps `Grade` → FSRS rating, returns updated card state | `fsrs` |
| `models.py` | shared dataclasses (`CardView`, `SrsState`, `DayStats`) | — |
| `messages.py` | all Dutch user-facing strings | `models.py` |
| `db.py` | all SQL, transactions, migration runner | `psycopg` |
| `config.py` | loads `settings.yaml` + env vars | — |
| `importer/` | kwiziq parser, Claude translation suggestions, YAML read/write, CLI | `db.py` |

Libraries: `python-telegram-bot` (with JobQueue), `psycopg` (v3), `fsrs` (py-fsrs 6), `PyYAML`, `tzdata`.
Importer only: `anthropic`, `beautifulsoup4`.

## 4. Configuration

`settings.yaml` (committed, no secrets):

```yaml
timezone: Europe/Amsterdam
daily_goal: 10          # cards per day
daily_new: 3            # new cards per day (more only when too few reviews are due to reach the goal)
batch_size: 4
batch_times: ["08:00", "13:00", "19:00"]
reminder_time: "20:30"
learning_steps: ["4h", "4h", "1d"]
relearning_steps: ["4h"]
typo_min_length: 4      # 1-edit typo tolerance only for answers of ≥ 4 characters
```

`learning_steps` follow py-fsrs semantics: a new card starts at step 0, and a "Good" on step *n* schedules
the card `learning_steps[n+1]` later; "Good" on the last step graduates it. With `["4h", "4h", "1d"]` the
flow is: quiz right after intro → +4h → +1d → graduated (verified against fsrs 6.3.2). "Again" returns the
card to step 0 (+4h). The "7 reviews : 3 new" split is not a setting: it follows from `daily_goal` − `daily_new`.

Environment variables (Kubernetes Secret `french-srs-bot-secrets`, or local `.env`; `.env.example`
in repo): `TELEGRAM_BOT_TOKEN`, `TELEGRAM_USER_ID`, `DATABASE_URL`. The importer additionally uses
`ANTHROPIC_API_KEY` (local only, never in the pod).

## 5. Data model (Postgres schema `french`)

```
themes ──< items ──< cards ──< reviews          bot_state (single row)    schema_migrations
```

### `themes`
| column | type | notes |
|---|---|---|
| id | serial PK | |
| source | text not null | `kwiziq` (later `obsidian`, `grammar`) |
| source_ref | text not null | e.g. `theme/128` |
| level | text | e.g. `A0` |
| name | text not null | e.g. `Pets vocabulary` |
| position | int not null | order in which themes are introduced |
| | | unique (source, source_ref) |

### `items`
| column | type | notes |
|---|---|---|
| id | serial PK | |
| theme_id | int FK → themes | |
| position | int not null | order within theme (as on source page) |
| french | text[] not null | accepted French answers; `french[1]` is the canonical one shown |
| dutch | text[] not null | accepted Dutch answers; `dutch[1]` is canonical |
| english | text | as on source, reference only |
| gender | char(1) null | `m` / `f` / null |
| hint | text null | shown with the NL→FR prompt when the Dutch alone is ambiguous, e.g. `vrouwelijk` for *allemande* |
| | | unique (theme_id, (french[1])) |

### `cards`
| column | type | notes |
|---|---|---|
| id | serial PK | |
| item_id | int FK → items | |
| direction | text not null | `fr_nl` or `nl_fr`; unique (item_id, direction) |
| introduced_at | timestamptz null | null = new, never shown |
| due | timestamptz null | null while new |
| fsrs_state | smallint null | FSRS state (learning / review / relearning) |
| step | smallint null | current (re)learning step |
| stability | double precision null | FSRS |
| difficulty | double precision null | FSRS |
| last_review | timestamptz null | |

Both cards are created when an item is loaded. Each direction has its own independent FSRS schedule:
knowing `fr_nl` well does not affect when `nl_fr` comes back, and vice versa.

### `reviews` (append-only)
| column | type | notes |
|---|---|---|
| id | bigserial PK | |
| card_id | int FK → cards | |
| reviewed_at | timestamptz not null | |
| answer | text not null | raw typed answer |
| grade | text not null | `correct` / `almost` / `hard` / `wrong` |
| rating | smallint not null | FSRS rating used |
| due_before | timestamptz null | |
| due_after | timestamptz not null | |

Daily progress and streak are derived from `reviews` (local date in `Europe/Amsterdam`); there is no
separate counter table.

### `bot_state` (exactly one row, id = 1)
| column | type | notes |
|---|---|---|
| id | int PK check (id = 1) | |
| pending_card_id | int null FK → cards | card awaiting an answer |
| pending_since | timestamptz null | |
| batch_remaining | smallint not null default 0 | cards left in the current batch |

### Migrations

- SQL files in `db/migrations/NNN_description.sql` (first: `001_initial.sql`, creates schema and all
  tables above).
- On startup `db.py` creates `french.schema_migrations(version text PK, applied_at timestamptz)` if
  missing, then applies every file whose version is not recorded, in filename order, together with their
  `schema_migrations` inserts, in one transaction (all pending migrations succeed or none do).
- A transaction-scoped Postgres advisory lock guards the runner.
- Connections use `prepare_threshold=None` so they also work through Supabase's transaction pooler.
- No migration library; the runner is small and explicit.

## 6. Flows

### 6.1 Starting a batch (`/practice` or a scheduled batch time)

1. Set `batch_remaining = batch_size`.
2. If `bot_state.pending_card_id` is set → re-ask that card first (it counts as the batch's first card).
3. Next card selection (`session.pick_next`). Definitions, all for the local day:
   - `N` = cards whose `introduced_at` is today (new cards)
   - `R` = review rows today on cards not introduced today (reviews)
   - `D` = eligible due cards: `introduced_at` not null, `due ≤ now`, and **sibling rule** satisfied
   - `new_allowed = max(daily_new, daily_goal − (R + D))` — new cards fill up the goal only when there are
     not enough reviews to reach it

   **Sibling rule:** a card is not eligible (neither as review nor as new card) if the other direction of
   the same item has a `reviews` row today; it simply stays due and comes up on a later day. This prevents
   one direction from giving away the answer to the other.

   **New card** = first card with `introduced_at` null and sibling rule satisfied, ordered by theme
   `position`, item `position`, `fr_nl` before `nl_fr`; an `nl_fr` card is skipped while its sibling
   `fr_nl` card is not introduced, was introduced today, or is due right now (so the reverse direction starts
on a later local day, and never displaces a due review of the forward direction).

   Rules, first match wins:
   1. First card of a batch and `N < new_allowed` and a new card exists → **new card**. This spreads new
      words over the day, so most of them get their +4h learning step on the same day.
   2. `D > 0` → **review**: the eligible card with the oldest `due`.
   3. `N < new_allowed` and a new card exists → **new card** (fills the batch when reviews run out).
   4. Otherwise → no card.

   Example, normal day (plenty of reviews due): 08:00 new A + 3 reviews; 13:00 new B + 3 reviews
   (including A's +4h step); 19:00 new C + 3 reviews (including B's step) → 3 new, 9 reviews.
   Early days (2 reviews due): 08:00 new A + 2 reviews + new B, and so on until the goal is reached.
   The immediate learning-step reviews of today's new cards count towards `N` (via `introduced_at`),
   not `R`. `/practice` follows the same rules (a manual batch is a batch).
4. When `batch_remaining` hits 0 or no card is available → batch summary (done today, goal status, reviews
   still due).

### 6.2 Scheduled messages

"Today's total" = number of `reviews` rows with a local `reviewed_at` date of today.

- At each `batch_times` entry: if today's total ≥ `daily_goal` → send nothing. Otherwise start a batch as
  in 6.1; if that batch has no card at all, send nothing.
- At `reminder_time`: if today's total < `daily_goal` → one reminder message. No catch-up of batches
  missed while the pod was down.

### 6.3 One card

```
new card?  → "🆕 le chien = de hond (m)"  [Begrepen]   → on press: introduced_at = now
ask        → "🇫🇷→🇳🇱  le chien"          → bot_state.pending_card_id = card
user types → grading.grade(...)           → Grade
           → srs.review(card, grade, now) → new card state
           → db: update cards + insert reviews + clear pending   (one transaction)
           → feedback message → next card (6.1 step 3) or summary
```

The immediate quiz after "Begrepen" is the first review of the card (learning step 0).

Text messages while no card is pending get a short hint to use `/practice`. A "Begrepen" button of a card
that is no longer current (another card is pending, or it was already introduced) is ignored.

## 7. Grading (`grading.py`)

`grade(answer: str, accepted: list[str], lang: "fr" | "nl", typo_min_length: int) -> GradeResult`
(`GradeResult` = grade, reason `exact`/`accent`/`article`/`typo`/`wrong`, and the closest accepted answer)

**Normalisation** (both sides): trim, lowercase, collapse internal whitespace, `’`/`‘` → `'`, strip
trailing `.`, `!`, `?` (except an answer that is only `?`). Accent removal also maps `œ` → `oe`.

Compare the answer with **each** accepted answer and keep the **best** result:

| order | check | grade |
|---|---|---|
| 1 | equal after normalisation | `correct` |
| 2 | equal after also removing accents (Unicode NFD, drop combining marks) | `almost` |
| 3 | equal (accent-insensitive) after removing a leading article from both sides — FR: `le`, `la`, `les`, `l'`, `un`, `une`, `des`; NL: `de`, `het`, `een` | `hard` |
| 4 | Levenshtein distance 1 (accent- and article-insensitive), accepted answer ≥ `typo_min_length` characters | `hard` |
| 5 | otherwise, empty, or `?` | `wrong` |

Feedback messages distinguish accent (`almost`), article, and typo cases, always showing the canonical
answer, and for French nouns the gender.

## 8. Scheduling (`srs.py`)

- py-fsrs `Scheduler` with `learning_steps` and `relearning_steps` from settings, `enable_fuzzing=True`.
- Rating map:

  | grade | FSRS rating |
  |---|---|
  | correct | Good |
  | almost | Hard |
  | hard | Hard |
  | wrong | Again |

- **Easy is never used**, so a new card always goes through all learning steps:
  quiz after intro → +4h → +1d → graduates to FSRS review intervals (≈ 7 days, then ≈ 1 month, …).
- py-fsrs requires UTC datetimes; `srs.py` converts everything with `astimezone(timezone.utc)`.
- `almost` and `hard` schedule identically in v1; the difference lives in feedback and in `reviews.grade`.
- `srs.py` converts between the `cards` row and a py-fsrs `Card`; it has no database or Telegram access.

## 9. Importer (`src/french_srs_bot/importer/`, runs locally: `python -m french_srs_bot.importer`)

Workflow in three steps:

1. `fetch <theme-url> --level A0 --position N` → parses the kwiziq theme page and asks Claude for
   suggestions. Writes `data/<level>/<theme-id>.yaml`.
   - kwiziq HTML (verified 2026-09-17): `h1` = theme name (suffix " in French" dropped); rows in
     `table.table-vocab-list tbody tr`, French in `.txt--lang-foreign`, English in `.txt--lang-native`.
   - Gender markers are inconsistent on kwiziq (`allemand (m)`, `green (m)`, `Italian (masc)`); the parser
     strips a trailing `(m)`/`(f)`/`(masc)`/`(fem)` from the French term and leaves English as-is.
   - One Claude call per theme (`claude-opus-5`, structured output, server-side `fallbacks: "default"`)
     suggests per item: Dutch answers (with Dutch article for nouns, alternatives allowed), gender of the
     French noun (`m`/`f`/none), and a short Dutch hint when the Dutch prompt alone is ambiguous.
2. Niels reviews and edits the YAML by hand.
3. `load data/<level>/<theme-id>.yaml` → upserts `themes`, `items` (on unique key), creates missing `cards`.
   Re-loading an edited file updates `french`/`dutch`/`english`/`gender`/`hint` of existing items without
   touching card progress.

YAML format:

```yaml
source: kwiziq
source_ref: theme/128
level: A0
name: Pets vocabulary
position: 1
items:
  - french: [le chien]
    dutch: [de hond]
    english: the dog (male)
    gender: m
    hint: null
```

- `data/` is git-ignored (kwiziq content is copyrighted; personal use only). The repo ships
  `examples/example-theme.yaml` with self-written sample words (loaded with `source: example`); parser tests use a self-written HTML
  snippet with the same structure, not a copy of a kwiziq page.
- Pages are fetched once per theme, never on a schedule.

## 10. Error handling

| situation | behaviour |
|---|---|
| pod restart while a card is pending | pending card stored in `bot_state`, re-asked on next message or batch |
| pending card unanswered at next batch time | the batch starts with that card again; its schedule is unchanged |
| node outage | pod reschedules to another node; missed batches are not caught up; reminder / `/practice` cover it |
| database unreachable | user gets "database even niet bereikbaar"; error logged; process keeps running |
| update from another user | ignored |
| saving an answer | card update + review insert + pending clear in one transaction |

## 11. Testing (pytest)

- `grading.py`: table-driven cases — accents, missing/wrong article (FR and NL), 1-letter typo, short-word
  typo not accepted, synonyms, apostrophe variants, trailing punctuation, `?`, empty.
- `srs.py`: new card + correct → due +4h; then correct → +1d; then graduates; wrong in review → relearning;
  a new card can never graduate on its first review.
- `session.py`: one new card leads each batch while allowed, fill with new cards when few reviews are due,
  sibling delay for new `nl_fr` cards, sibling rule (other direction reviewed today → not eligible),
  daily new cap, reviews still served after goal, batch size.
- `db.py` + migrations: against a real Postgres, not mocks; migrations idempotent on re-run. Tests read
  `TEST_DATABASE_URL` (skipped when unset; refused unless the URL contains `test`, because the fixture drops
  the `french` schema). Locally: database `french_srs_test` on the existing PG18 (localhost:5432). CI: a
  `postgres:17` service container.
- importer: parser against a self-written HTML fixture (no live HTTP); building the YAML from parsed rows +
  suggestions; `load` upsert keeps card progress. The Claude call itself is not unit-tested.
- Telegram interaction: manual check after deploy.

## 12. Repository & deployment

```
french-srs-bot/
  src/french_srs_bot/            __main__.py bot.py scheduler.py session.py grading.py srs.py
                                 models.py messages.py db.py config.py
  src/french_srs_bot/importer/   __main__.py kwiziq.py suggest.py theme_file.py
  db/migrations/                 001_initial.sql
  examples/                      example-theme.yaml
  tests/
  k8s/                           namespace.yaml deployment.yaml secret.example.env
  settings.yaml  Dockerfile  pyproject.toml  requirements.txt  requirements-importer.txt
  requirements-dev.txt  .env.example  .gitignore  README.md
  .github/workflows/             deploy.yml
```

The runtime image installs only `requirements.txt` (no `anthropic`/`beautifulsoup4`).

- GitHub Actions (pattern from `gridstatic`): run tests → build multi-arch (`linux/amd64,linux/arm64`) image
  → push `ghcr.io/<owner>/french-srs-bot:latest` and `:<sha>`.
- Kubernetes: namespace `french`; Deployment with `replicas: 1` and `strategy: Recreate` (never two pollers);
  Secret `french-srs-bot-secrets`. Plain manifests, no Helm.
- `.gitignore`: `data/`, `.env`, `*.secret.env`, caches.
- The GHCR package must be made public once (like the gridstatic images) so the cluster can pull without
  an image pull secret. It contains no secrets.
