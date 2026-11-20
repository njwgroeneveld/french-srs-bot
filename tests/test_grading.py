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


@pytest.mark.parametrize(
    ("answer", "expected_grade"),
    [
        # the digits are an accepted answer of their own, next to the words
        ("352", Grade.CORRECT),
        ("driehonderdtweeënvijftig", Grade.CORRECT),
        # one digit off is another number, not a typo
        ("353", Grade.WRONG),
        ("35", Grade.WRONG),
    ],
)
def test_number_typed_as_digits(answer, expected_grade):
    accepted = ["driehonderdtweeënvijftig", "352"]
    assert grade(answer, accepted, "nl").grade is expected_grade


@pytest.mark.parametrize("answer", ["1000", "1.000", "1 000", "1.000."])
def test_thousands_separators_do_not_matter(answer):
    result = grade(answer, ["duizend", "1000"], "nl")
    assert (result.grade, result.reason) == (Grade.CORRECT, "exact")


def test_a_missing_zero_is_not_a_small_typo():
    # 100 for 1000 is one character off and four long, which the typo rule would forgive.
    # A factor ten is not a slip of the finger.
    result = grade("100", ["duizend", "1000"], "nl")
    assert result.grade is Grade.WRONG
    assert result.expected == "duizend"  # the feedback still shows the word, not the digits


def test_digits_only_count_where_the_card_accepts_them():
    # Cards without a digit answer are untouched: typing a number there stays wrong.
    assert grade("2", ["twee"], "nl").grade is Grade.WRONG


# --- sentences: milder about punctuation, two typos allowed in a long one --------------------

PIECES = ["Il y a combien de pièces ?"]
REZ = ["Au rez-de-chaussée, il y a une cuisine et un salon."]


@pytest.mark.parametrize(
    ("answer", "accepted", "expected_grade", "expected_reason"),
    [
        ("il y a combien de pieces", PIECES, Grade.ALMOST, "accent"),
        ("Il y a combien de pièces?", PIECES, Grade.CORRECT, "exact"),
        ("il y a combien de pièces", PIECES, Grade.CORRECT, "exact"),
        # punctuation inside the sentence does not count
        ("Au rez-de-chaussée il y a une cuisine et un salon", REZ, Grade.CORRECT, "exact"),
        ("Au rez-de-chaussée ,  il y a une cuisine et un salon", REZ, Grade.CORRECT, "exact"),
        # 24 characters after normalising: one or two typos are forgiven, three are not
        ("Il y a combien de pièses", PIECES, Grade.HARD, "typo"),   # c -> s
        ("Il y a conbien de pièses", PIECES, Grade.HARD, "typo"),   # + m -> n
        ("Il y a conbien dr pièses", PIECES, Grade.WRONG, "wrong"), # + e -> r
        # under 20 characters ("c'est loin"): still one typo at most
        ("C'ezt loin", ["C'est loin ?"], Grade.HARD, "typo"),
        ("C'ezt loim", ["C'est loin ?"], Grade.WRONG, "wrong"),
    ],
)
def test_grade_sentence(answer, accepted, expected_grade, expected_reason):
    result = grade(answer, accepted, "fr", sentence=True)
    assert (result.grade, result.reason) == (expected_grade, expected_reason)
    assert result.expected == accepted[0]  # feedback shows the sentence as written


def test_words_keep_the_strict_rules():
    # Without sentence=True a comma is still a typo, and two typos are wrong.
    assert grade("Au rez-de-chaussée il y a une cuisine et un salon", REZ, "fr").grade == Grade.HARD
    assert grade("Il y a conbien de pièses", PIECES, "fr").grade == Grade.WRONG
