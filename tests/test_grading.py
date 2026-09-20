import pytest

from french_srs_bot.grading import Grade, grade, levenshtein, normalize, strip_accents


@pytest.mark.parametrize(
    ("answer", "accepted", "lang", "expected_grade", "expected_reason"),
    [
        # exact, case and whitespace insensitive
        ("l'école", ["l'école"], "fr", Grade.CORRECT, "exact"),
        ("  L'École ", ["l'école"], "fr", Grade.CORRECT, "exact"),
        ("le  chien", ["le chien"], "fr", Grade.CORRECT, "exact"),
        # curly apostrophe from a phone keyboard
        ("l’école", ["l'école"], "fr", Grade.CORRECT, "exact"),
        # modifier letter apostrophe
        ("lʼécole", ["l'école"], "fr", Grade.CORRECT, "exact"),
        # decomposed accent (NFD) normalises to the same as precomposed (NFC)
        ("l'école", ["l'école"], "fr", Grade.CORRECT, "exact"),
        # trailing punctuation is ignored
        ("de hond.", ["de hond"], "nl", Grade.CORRECT, "exact"),
        ("de hond,", ["de hond"], "nl", Grade.CORRECT, "exact"),
        # accents
        ("l'ecole", ["l'école"], "fr", Grade.ALMOST, "accent"),
        ("oeuf", ["œuf"], "fr", Grade.ALMOST, "accent"),
        # articles, French
        ("école", ["l'école"], "fr", Grade.HARD, "article"),
        ("la école", ["l'école"], "fr", Grade.HARD, "article"),
        ("chien", ["le chien"], "fr", Grade.HARD, "article"),
        ("la chien", ["le chien"], "fr", Grade.HARD, "article"),
        # articles, Dutch
        ("hond", ["de hond"], "nl", Grade.HARD, "article"),
        ("het hond", ["de hond"], "nl", Grade.HARD, "article"),
        # one-letter typo on a long enough word
        ("mardie", ["mardi"], "fr", Grade.HARD, "typo"),
        ("le chein", ["le chien"], "fr", Grade.WRONG, "wrong"),  # transposition = 2 edits
        # short words get no typo tolerance
        ("dex", ["deux"], "fr", Grade.HARD, "typo"),  # "deux" has 4 letters
        ("un", ["une"], "fr", Grade.WRONG, "wrong"),
        # synonyms: best match wins
        ("de reu", ["de hond", "de reu"], "nl", Grade.CORRECT, "exact"),
        ("grande", ["grand", "grande"], "fr", Grade.CORRECT, "exact"),
        # wrong, empty, question mark
        ("la maison", ["l'école"], "fr", Grade.WRONG, "wrong"),
        ("", ["l'école"], "fr", Grade.WRONG, "wrong"),
        ("?", ["l'école"], "fr", Grade.WRONG, "wrong"),
        # a full sentence, as a translate card grades it
        ("ik neem de fiets om naar mijn werk te gaan.",
         ["Ik neem de fiets om naar mijn werk te gaan"], "nl", Grade.CORRECT, "exact"),
        ("Ik neem de fiets om naar mijn werk te gaen",
         ["Ik neem de fiets om naar mijn werk te gaan"], "nl", Grade.HARD, "typo"),
        ("Je kunt de stad zonder auto bezichtigen",
         ["Je kunt de stad zonder auto bezoeken", "Je kunt de stad zonder auto bezichtigen"],
         "nl", Grade.CORRECT, "exact"),
    ],
)
def test_grade(answer, accepted, lang, expected_grade, expected_reason):
    result = grade(answer, accepted, lang, typo_min_length=4)
    assert (result.grade, result.reason) == (expected_grade, expected_reason)


def test_grade_reports_closest_accepted_answer():
    result = grade("de reu", ["de hond", "de reu"], "nl")
    assert result.expected == "de reu"


def test_grade_without_accepted_answers_is_an_error():
    with pytest.raises(ValueError):
        grade("x", [], "fr")


def test_helpers():
    assert normalize("  Bonjour !! ") == "bonjour"
    assert normalize("?") == "?"
    assert strip_accents("français été") == "francais ete"
    assert levenshtein("mardi", "mardie") == 1
    assert levenshtein("", "abc") == 3


def test_grade_is_a_str_enum():
    assert f"{Grade.CORRECT}" == "correct"
