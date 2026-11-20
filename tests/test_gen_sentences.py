"""tools/gen_sentences.py: the sentences per goal from the Obsidian chapter notes, as theme YAML."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from french_srs_bot.importer.theme_file import read_theme_file

TOOL = Path(__file__).resolve().parents[1] / "tools" / "gen_sentences.py"
_spec = importlib.util.spec_from_file_location("gen_sentences", TOOL)
gen = importlib.util.module_from_spec(_spec)
sys.modules["gen_sentences"] = gen  # dataclasses look their module up while being defined
_spec.loader.exec_module(gen)

U6 = """---
type: hoofdstuk
unite: 6
titel: Au quotidien
---
# Unité 6 — Au quotidien
## Communication
### Parler de ses habitudes
- document: A — Une journée (p. 86)
- doel: [[B3-01 Over gewoontes praten]]
- zinnen: Je me lève à sept heures. · Il y a combien de pièces ?
- rollenspel: Vertel over je dag.
### Dire l'heure
- doel: [[B3-02 De tijd zeggen]]
- zinnen: Il est quelle heure ? · Il est neuf heures du matin.
## Production orale
- zinnen: Dit hoort niet bij een doel.
"""

U7 = """---
type: hoofdstuk
unite: 7
titel: Chez moi !
---
# Unité 7 — Chez moi !
## Communication
### S'informer sur un logement
- document: A — Un nouveau logement (p. 102)
- doel: [[B4-01 Informeren naar woonruimte]]
- zinnen: Quelle est la surface ? · La maison fait 100 m² (mètres carrés). ·  Il y a combien de pièces ? ·
- rollenspel: Je zoekt een huis.
### Décrire son logement
- doel: [[B4-02 Je woning beschrijven]]
- zinnen: Au rez-de-chaussée, il y a une cuisine et un salon. · Quelle est la surface ?
## Production orale
- zinnen: Ook dit niet.
"""


@pytest.fixture
def vault(tmp_path):
    lessons = tmp_path / "vault" / "Frans" / "Lessen"
    lessons.mkdir(parents=True)
    (lessons / "Unité 6 — Au quotidien.md").write_text(U6, encoding="utf-8")
    (lessons / "Unité 7 — Chez moi.md").write_text(U7, encoding="utf-8")
    (lessons / "Losse notitie.md").write_text("# Geen hoofdstuk\n- zinnen: Niet dit.\n", encoding="utf-8")
    return tmp_path / "vault"


@pytest.fixture
def data_dir(tmp_path):
    data = tmp_path / "data"
    (data / "A1.6").mkdir(parents=True)
    (data / "A1.6" / "heure.yaml").write_text(
        "source: manual\nsource_ref: a1.6/heure\nlevel: A1.1\nname: \"A1.6 · L'heure\"\nposition: 14\n"
        "items:\n  - french: [Il est neuf heures du matin]\n    dutch: [Het is negen uur 's ochtends]\n",
        encoding="utf-8",
    )
    (data / "load-a1.6.sql").write_text("-- geen yaml\n", encoding="utf-8")
    return data


def test_collect_gives_sentences_per_chapter_per_goal(vault):
    chapters = gen.collect(vault)
    assert [(c.unite, c.note) for c in chapters] == [(6, "Unité 6 — Au quotidien"), (7, "Unité 7 — Chez moi")]
    u7 = chapters[1]
    assert [g.title for g in u7.goals] == ["S'informer sur un logement", "Décrire son logement"]
    assert u7.goals[0].sentences == [
        "Quelle est la surface ?",
        "La maison fait 100 m² (mètres carrés).",
        "Il y a combien de pièces ?",
    ]


def test_known_french_reads_existing_yaml_normalized(data_dir):
    assert gen.known_french(data_dir) == {"il est neuf heures du matin"}


def test_write_themes_writes_one_theme_per_goal(vault, tmp_path):
    out = tmp_path / "out"
    result = gen.write_themes(vault, out, unites={7}, chapter="Zinnen U7", start_position=2001, known=set())
    files = sorted(out.glob("*.yaml"))
    assert len(files) == 2
    first = yaml.safe_load((out / "u7-s-informer-sur-un-logement.yaml").read_text(encoding="utf-8"))
    assert first == {
        "source": "vault",
        "source_ref": "sentences/u7/s-informer-sur-un-logement",
        "level": "A1.2",
        "name": "Zinnen U7 · S'informer sur un logement",
        "position": 2001,
        "lesson": "Unité 7 — Chez moi#S'informer sur un logement",
        "kind": "sentence",
        "items": [
            {"french": ["Quelle est la surface ?"], "dutch": ["TODO"]},
            {"french": ["La maison fait 100 m² (mètres carrés)."], "dutch": ["TODO"]},
            {"french": ["Il y a combien de pièces ?"], "dutch": ["TODO"]},
        ],
    }
    second = yaml.safe_load((out / "u7-decrire-son-logement.yaml").read_text(encoding="utf-8"))
    assert second["position"] == 2002
    # the surface question came up in the first goal already
    assert [i["french"] for i in second["items"]] == [["Au rez-de-chaussée, il y a une cuisine et un salon."]]
    assert [(s.goal, s.french) for s in result.skipped] == [("Décrire son logement", "Quelle est la surface ?")]
    assert [(t.name, t.count) for t in result.written] == [
        ("Zinnen U7 · S'informer sur un logement", 3),
        ("Zinnen U7 · Décrire son logement", 1),
    ]


def test_written_file_reads_back_once_translated(vault, tmp_path):
    out = tmp_path / "out"
    gen.write_themes(vault, out, unites={7}, chapter="Zinnen U7", start_position=2001, known=set())
    path = out / "u7-s-informer-sur-un-logement.yaml"
    with pytest.raises(ValueError, match="nog geen vertaling"):
        read_theme_file(path)
    path.write_text(path.read_text(encoding="utf-8").replace("TODO", "Een vertaling"), encoding="utf-8")
    theme = read_theme_file(path)
    assert theme.kind == "sentence"
    assert theme.items[2].french == ["Il y a combien de pièces ?"]


def test_known_and_repeated_sentences_are_skipped_across_chapters(vault, tmp_path, data_dir):
    out = tmp_path / "out"
    known = gen.known_french(data_dir)
    result = gen.write_themes(
        vault, out, unites={6, 7}, chapter="Zinnen U4-6", start_position=1001, known=known
    )
    skipped = [(s.unite, s.french) for s in result.skipped]
    assert (6, "Il est neuf heures du matin.") in skipped  # already in data/
    assert (7, "Il y a combien de pièces ?") in skipped  # already in U6 this run
    positions = [t.position for t in result.written]
    assert positions == list(range(1001, 1001 + len(positions)))


def test_refuses_to_overwrite_translated_file(vault, tmp_path):
    out = tmp_path / "out"
    gen.write_themes(vault, out, unites={7}, chapter="Zinnen U7", start_position=2001, known=set())
    # rerunning over untranslated files is fine
    gen.write_themes(vault, out, unites={7}, chapter="Zinnen U7", start_position=2001, known=set())
    path = out / "u7-decrire-son-logement.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("TODO", "Vertaald"), encoding="utf-8")
    with pytest.raises(gen.AlreadyTranslated, match="u7-decrire-son-logement.yaml"):
        gen.write_themes(vault, out, unites={7}, chapter="Zinnen U7", start_position=2001, known=set())
    assert "Vertaald" in path.read_text(encoding="utf-8")


def test_cli(vault, tmp_path, data_dir):
    out = data_dir / "sentences" / "u7"
    args = [sys.executable, str(TOOL), str(vault), str(out), "--unites", "7",
            "--chapter", "Zinnen U7", "--start-position", "2001", "--data-dir", str(data_dir)]
    run = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
    assert run.returncode == 0, run.stderr
    assert "Zinnen U7 · S'informer sur un logement: 3" in run.stdout
    assert "overgeslagen" in run.stdout
    # a rerun does not count its own earlier output as already known
    rerun = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
    assert rerun.returncode == 0, rerun.stderr
    assert "Zinnen U7 · S'informer sur un logement: 3" in rerun.stdout
    path = out / "u7-s-informer-sur-un-logement.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("TODO", "Vertaald"), encoding="utf-8")
    refused = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
    assert refused.returncode != 0
    assert "al vertaald" in refused.stderr
