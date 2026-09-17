# french-srs-bot

A personal Telegram bot that quizzes you on French vocabulary with spaced repetition
([FSRS](https://github.com/open-spaced-repetition/py-fsrs)), in both directions (French → Dutch and
Dutch → French). You type the answer; the bot grades it with partial credit (accents, articles, typos)
and schedules the card accordingly.

## How it works

- Three small batches a day (08:00, 13:00, 19:00) and `/practice` whenever you like.
- Daily goal of 10 cards: reviews first, one new word leads each batch, at most 3 new words per day
  unless there are too few reviews to reach the goal.
- A new word goes through learning steps (right away → +4h → +1 day) before FSRS takes over.
- The two directions of a word are separate cards and never come up on the same day.
- Progress lives in Postgres (schema `french`); migrations run automatically at startup.

The full design is in [docs/design.md](docs/design.md).

## Development

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt -e .   # Windows; use .venv/bin on Linux/macOS
cp .env.example .env                                      # fill in TEST_DATABASE_URL at least
.venv/Scripts/python -m pytest
```

Database tests need `TEST_DATABASE_URL` pointing at a database whose name contains `test`
(the fixture drops and recreates schema `french`).

## Importing vocabulary

```bash
python -m french_srs_bot.importer fetch https://french.kwiziq.com/learn/theme/128 --level A0 --position 1
# review and edit data/A0/theme-128.yaml
python -m french_srs_bot.importer load data/A0/theme-128.yaml
```

`fetch` needs `ANTHROPIC_API_KEY` (Claude suggests Dutch translations), `load` needs `DATABASE_URL`.
Imported word lists stay in `data/`, which is git-ignored: they come from a third-party site and are
for personal use only. `examples/example-theme.yaml` shows the format.
Changing an item's first French answer creates a new item; `load` reports the old one as stale
("warning: in database but not in file") and it must be removed manually.

## Deployment

Pushing to `main` runs the tests and publishes `ghcr.io/<owner>/french-srs-bot` (amd64 + arm64).

```bash
kubectl apply -f k8s/namespace.yaml
kubectl -n french create secret generic french-srs-bot-secrets --from-env-file=french-srs-bot.secret.env
kubectl apply -f k8s/deployment.yaml
kubectl -n french rollout restart deployment/french-srs-bot   # after a new image
```
