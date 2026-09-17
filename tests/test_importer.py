from pathlib import Path

import pytest

from french_srs_bot import db
from french_srs_bot.importer.kwiziq import Entry, ParsedTheme, parse_theme, source_ref_from_url
from french_srs_bot.importer.suggest import Suggestion, build_theme_file
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
    theme = read_theme_file(REPO_ROOT / "examples" / "example-theme.yaml")
    assert load_theme(conn, theme) == len(theme.items)
    conn.execute("UPDATE french.cards SET introduced_at = now(), due = now(), fsrs_state = 2")
    theme.items[0].dutch.append("extra")
    load_theme(conn, theme)
    assert conn.execute("SELECT count(*) AS n FROM french.cards WHERE fsrs_state = 2").fetchone()["n"] == 2 * len(theme.items)
    first = db.get_card(conn, conn.execute("SELECT min(id) AS id FROM french.cards").fetchone()["id"])
    assert "extra" in first.dutch
