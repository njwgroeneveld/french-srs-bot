"""tools/gen_load_sql.py: the SQL Niels pastes into Supabase, run here against the test database."""

import importlib.util
from pathlib import Path

import pytest
import yaml

from french_srs_bot import db

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def _load_tool(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen_load_sql = _load_tool("gen_load_sql")


def _write_yaml(path: Path, data: dict) -> str:
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return str(path)


def _sentence_theme(ref, name, position, sentences):
    return {
        "source": "vault",
        "source_ref": ref,
        "level": "A1.2",
        "name": name,
        "position": position,
        "lesson": f"Unité 7 — Chez moi#{name.split(' · ')[1]}",
        "kind": "sentence",
        "items": [{"french": [fr], "dutch": [nl]} for fr, nl in sentences],
    }


@pytest.fixture
def sentence_sql(tmp_path):
    paths = [
        _write_yaml(
            tmp_path / "logement.yaml",
            _sentence_theme(
                "sentences/u7/s-informer-sur-un-logement",
                "Zinnen U7 · S'informer sur un logement",
                2001,
                [("Il y a combien de pièces ?", "Hoeveel kamers zijn er?"),
                 ("Quelle est la surface ?", "Wat is de oppervlakte?")],
            ),
        ),
        _write_yaml(
            tmp_path / "meubles.yaml",
            _sentence_theme(
                "sentences/u7/decrire-un-logement",
                "Zinnen U7 · Décrire un logement",
                2002,
                [("Au rez-de-chaussée, il y a une cuisine.", "Op de begane grond is een keuken.")],
            ),
        ),
    ]
    out = tmp_path / "load.sql"
    gen_load_sql.main(str(out), paths)
    return out.read_text(encoding="utf-8")


def test_sentence_sql_contains_migration_008_and_the_checks(sentence_sql):
    assert "CHECK (kind IN ('vocab', 'grammar', 'sentence'))" in sentence_sql
    assert "CHECK (batch_kind IS NULL OR batch_kind IN ('vocab', 'grammar', 'sentence'))" in sentence_sql
    assert (
        "INSERT INTO french.schema_migrations (version) VALUES ('008_sentence') ON CONFLICT DO NOTHING;"
        in sentence_sql
    )
    assert "'sentence'" in sentence_sql
    assert "VALUES ('nl_fr')" in sentence_sql
    assert "AND due < now() + interval '7 days'" in sentence_sql
    assert "('gap')" not in sentence_sql


def test_sentence_sql_loads_items_and_one_card_per_user(conn, sentence_sql):
    db.claim_owner(conn, telegram_user_id=42, name="Niels")
    conn.execute("INSERT INTO french.users (telegram_user_id, name) VALUES (99, 'Inga')")
    for _ in range(2):  # twice: the second run must not add anything
        conn.execute(sentence_sql)
    items = conn.execute(
        "SELECT i.kind, i.french[1] AS french FROM french.items i ORDER BY i.id"
    ).fetchall()
    assert [row["kind"] for row in items] == ["sentence"] * 3
    cards = conn.execute(
        "SELECT user_id, item_id, direction FROM french.cards ORDER BY user_id, item_id"
    ).fetchall()
    assert len(cards) == 2 * 3
    assert {row["direction"] for row in cards} == {"nl_fr"}
    themes = conn.execute("SELECT chapter FROM french.themes").fetchall()
    assert {row["chapter"] for row in themes} == {"Zinnen U7"}


def test_grammar_sql_after_008_keeps_sentences_allowed(conn, tmp_path):
    """The grammar SQL rewrites the kind CHECKs; it must not narrow them back to two kinds."""
    theme_id = db.upsert_theme(
        conn, source="vault", source_ref="sentences/u7/x", level="A1.2",
        name="Zinnen U7 · X", position=2001,
    )
    db.upsert_item(
        conn, theme_id=theme_id, position=1, french=["Il y a combien de pièces ?"],
        dutch=["Hoeveel kamers zijn er?"], english=None, gender=None, hint=None, kind="sentence",
    )
    grammar = _write_yaml(
        tmp_path / "grammar.yaml",
        {
            "source": "vault",
            "source_ref": "grammar/pour",
            "level": "A1.1",
            "name": "Grammaire · pour",
            "position": 900,
            "kind": "grammar",
            "choices": ["pour", "parce que"],
            "items": [{"sentence": "Je viens ___ apprendre.", "gap": "pour", "dutch": ["Ik kom om te leren."]}],
        },
    )
    out = tmp_path / "grammar.sql"
    gen_load_sql.main(str(out), [grammar])
    conn.execute(out.read_text(encoding="utf-8"))  # fails if the CHECK no longer allows 'sentence'
    # and the CHECK still lets new sentences in afterwards
    db.upsert_item(
        conn, theme_id=theme_id, position=2, french=["Quelle est la surface ?"],
        dutch=["Wat is de oppervlakte?"], english=None, gender=None, hint=None, kind="sentence",
    )
    kinds = conn.execute("SELECT kind, count(*) AS n FROM french.items GROUP BY kind ORDER BY kind").fetchall()
    assert [(row["kind"], row["n"]) for row in kinds] == [("grammar", 1), ("sentence", 2)]
