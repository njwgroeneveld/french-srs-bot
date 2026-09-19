import argparse
from pathlib import Path
from types import SimpleNamespace

import pydantic
import pytest

from french_srs_bot import db
from french_srs_bot.importer import __main__ as importer_cli
from french_srs_bot.importer.kwiziq import Entry, ParsedTheme, parse_theme, source_ref_from_url
from french_srs_bot.importer.suggest import Suggestion, Suggestions, build_theme_file, request_suggestions
from french_srs_bot.importer.theme_file import load_theme, read_theme_file, write_theme_file

REPO_ROOT = Path(__file__).resolve().parents[1]

# Self-written HTML with the same structure as a kwiziq theme page (not a copy of kwiziq content).
THEME_HTML = """
<html><body>
<div class="heading subheading"><h1>Kitchen vocabulary in French</h1></div>
<table class="table table-vocab-list"><thead><tr><th></th><th></th><th></th></tr></thead><tbody>
<tr><td><span class="txt--lang-foreign">la cuillère</span></td><td><span class="audio"></span></td>
    <td><span class="txt--lang-native">the spoon</span></td></tr>
<tr><td><span class="txt--lang-foreign">cuisinier (m)</span></td><td></td>
    <td><span class="txt--lang-native">cook</span></td></tr>
<tr><td><span class="txt--lang-foreign">cuisinière</span></td><td></td>
    <td><span class="txt--lang-native">cook (fem)</span></td></tr>
</tbody></table>
</body></html>
"""


def test_parse_theme():
    parsed = parse_theme(THEME_HTML)
    assert parsed.name == "Kitchen vocabulary"
    assert parsed.entries == [
        Entry("la cuillère", "the spoon"),
        Entry("cuisinier", "cook"),
        Entry("cuisinière", "cook (fem)"),
    ]


def test_parse_theme_without_rows_fails():
    with pytest.raises(ValueError):
        parse_theme("<h1>Empty in French</h1>")


def test_source_ref_from_url():
    assert source_ref_from_url("https://french.kwiziq.com/learn/theme/128") == "theme/128"
    with pytest.raises(ValueError):
        source_ref_from_url("https://french.kwiziq.com/revision/grammar/x")


def test_build_theme_file_maps_suggestions():
    parsed = ParsedTheme("Kitchen", [Entry("cuisinier", "cook"), Entry("cuisinière", "cook (fem)")])
    suggestions = [
        Suggestion(french="cuisinier", dutch=["de kok"], gender="m", hint=""),
        Suggestion(french="cuisinière", dutch=["de kok", "de kokkin"], gender="f", hint="vrouwelijk"),
    ]
    theme = build_theme_file(parsed, suggestions, source_ref="theme/9", level="A0", position=2)
    assert theme.items[0].hint is None and theme.items[0].gender == "m"
    assert theme.items[1].dutch == ["de kok", "de kokkin"] and theme.items[1].hint == "vrouwelijk"


def test_build_theme_file_rejects_mismatch():
    parsed = ParsedTheme("Kitchen", [Entry("cuisinier", "cook")])
    with pytest.raises(ValueError):
        build_theme_file(parsed, [Suggestion(french="autre", dutch=["x"], gender="none", hint="")],
                         source_ref="theme/9", level="A0", position=1)


def test_yaml_round_trip(tmp_path):
    theme = read_theme_file(REPO_ROOT / "examples" / "example-theme.yaml")
    target = tmp_path / "A0" / "copy.yaml"
    write_theme_file(target, theme)
    assert read_theme_file(target) == theme


def test_lesson_reference_round_trip_and_load(conn, tmp_path):
    theme = read_theme_file(REPO_ROOT / "examples" / "example-theme.yaml")
    assert theme.lesson is None
    theme.lesson = "Frans/Lessen/2026-W38 A1.1 U4 Transport"
    target = tmp_path / "lesson.yaml"
    write_theme_file(target, theme)
    assert read_theme_file(target).lesson == theme.lesson
    load_theme(conn, theme)
    row = conn.execute("SELECT lesson FROM french.themes WHERE source_ref = %s", (theme.source_ref,)).fetchone()
    assert row["lesson"] == theme.lesson


def test_read_theme_file_rejects_non_string_lesson(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "source: x\nsource_ref: t/1\nlevel: A0\nname: X\nposition: 1\nlesson: 38\n"
        "items:\n  - french: [le chien]\n    dutch: [de hond]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="lesson"):
        read_theme_file(bad)


def test_read_theme_file_validates_gender(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "source: x\nsource_ref: t/1\nlevel: A0\nname: X\nposition: 1\n"
        "items:\n  - french: [le chien]\n    dutch: [de hond]\n    gender: male\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="gender"):
        read_theme_file(bad)


def test_load_theme_twice_keeps_progress(conn):
    # Cards exist per user, so an import without a user produces none at all.
    db.claim_owner(conn, telegram_user_id=42, name="Niels")
    user_id = db.all_users(conn)[0].id
    theme = read_theme_file(REPO_ROOT / "examples" / "example-theme.yaml")
    assert load_theme(conn, theme).loaded == len(theme.items)
    conn.execute("UPDATE french.cards SET introduced_at = now(), due = now(), fsrs_state = 2")
    theme.items[0].dutch.append("extra")
    load_theme(conn, theme)
    assert conn.execute("SELECT count(*) AS n FROM french.cards WHERE fsrs_state = 2").fetchone()["n"] == 2 * len(theme.items)
    first = db.get_card(
        conn, conn.execute("SELECT min(id) AS id FROM french.cards").fetchone()["id"], user_id=user_id
    )
    assert "extra" in first.dutch


def _fake_client(parse):
    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=parse)))


PARSED = ParsedTheme("Kitchen", [Entry("cuisinier", "cook")])


def test_request_suggestions_wraps_validation_error():
    try:
        Suggestions.model_validate({})
    except pydantic.ValidationError as exc:
        error = exc

    def parse(**kwargs):
        raise error

    with pytest.raises(RuntimeError, match="no usable suggestions") as info:
        request_suggestions(_fake_client(parse), PARSED)
    assert info.value.__cause__ is error


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_request_suggestions_rejects_refusal_and_truncation(stop_reason):
    output = Suggestions(items=[]) if stop_reason == "max_tokens" else None
    response = SimpleNamespace(stop_reason=stop_reason, parsed_output=output)
    with pytest.raises(RuntimeError, match=stop_reason):
        request_suggestions(_fake_client(lambda **kwargs: response), PARSED)


def test_build_theme_file_tolerates_normalisation():
    parsed = ParsedTheme("School", [Entry("l'école", "the school")])
    suggestions = [Suggestion(french=" l’école ", dutch=["de school"], gender="f", hint="")]
    theme = build_theme_file(parsed, suggestions, source_ref="theme/9", level="A0", position=1)
    assert theme.items[0].french == ["l'école"]


def test_build_theme_file_rejects_different_length():
    parsed = ParsedTheme("Kitchen", [Entry("cuisinier", "cook")])
    with pytest.raises(ValueError):
        build_theme_file(parsed, [], source_ref="theme/9", level="A0", position=1)


def _theme_yaml(tmp_path, items):
    path = tmp_path / "theme.yaml"
    header = "source: x\nsource_ref: t/1\nlevel: A0\nname: X\nposition: 1\nitems:\n"
    path.write_text(header + items, encoding="utf-8")
    return path


def test_read_theme_file_rejects_bare_string(tmp_path):
    path = _theme_yaml(tmp_path, "  - french: le chien\n    dutch: [de hond]\n")
    with pytest.raises(ValueError, match=r"item 1.*french"):
        read_theme_file(path)


def test_read_theme_file_rejects_non_string_answer(tmp_path):
    path = _theme_yaml(tmp_path, "  - french: [on]\n    dutch: [men]\n")
    with pytest.raises(ValueError, match=r"item 1.*french"):
        read_theme_file(path)


def test_read_theme_file_rejects_non_string_english(tmp_path):
    path = _theme_yaml(tmp_path, "  - french: [oui]\n    dutch: [ja]\n    english: yes\n")
    with pytest.raises(ValueError, match=r"item 1.*english"):
        read_theme_file(path)


def test_read_theme_file_rejects_duplicate_french(tmp_path):
    path = _theme_yaml(
        tmp_path, "  - french: [le chien]\n    dutch: [de hond]\n  - french: [le chien]\n    dutch: [de reu]\n"
    )
    with pytest.raises(ValueError, match=r"theme.yaml.*item 2.*le chien"):
        read_theme_file(path)


def test_load_theme_reports_stale_items(conn):
    theme = read_theme_file(REPO_ROOT / "examples" / "example-theme.yaml")
    load_theme(conn, theme)
    removed = theme.items.pop(0)
    result = load_theme(conn, theme)
    assert result.loaded == len(theme.items)
    assert result.stale == [removed.french[0]]
    assert conn.execute("SELECT count(*) AS n FROM french.items").fetchone()["n"] == len(theme.items) + 1


def test_fetch_refuses_to_overwrite_existing_file(tmp_path, monkeypatch):
    target = tmp_path / "A0" / "theme-128.yaml"
    target.parent.mkdir()
    target.write_text("reviewed", encoding="utf-8")

    def no_network(url):
        raise AssertionError("fetch must stop before downloading or calling Claude")

    monkeypatch.setattr(importer_cli, "fetch_html", no_network)
    args = argparse.Namespace(
        url="https://french.kwiziq.com/learn/theme/128", level="A0", position=1, out=str(tmp_path), force=False
    )
    with pytest.raises(SystemExit) as info:
        importer_cli.cmd_fetch(args)
    assert "--force" in str(info.value.code)
    assert target.read_text(encoding="utf-8") == "reviewed"
