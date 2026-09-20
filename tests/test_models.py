from french_srs_bot.models import PRIMARY, SECONDARY, CardView


def card(direction, **extra):
    base = dict(
        card_id=1,
        item_id=1,
        direction=direction,
        french=["Je prends le vélo pour aller au travail."],
        dutch=["Ik neem de fiets om naar mijn werk te gaan"],
        gender=None,
        hint=None,
        introduced_at=None,
        srs=None,
    )
    return CardView(**{**base, **extra})


def test_vocab_directions_are_unchanged():
    word = dict(french=["le chien"], dutch=["de hond"])
    fr_nl = card("fr_nl", **word)
    nl_fr = card("nl_fr", **word)
    assert (fr_nl.question, fr_nl.accepted, fr_nl.answer_lang) == ("le chien", ["de hond"], "nl")
    assert (nl_fr.question, nl_fr.accepted, nl_fr.answer_lang) == ("de hond", ["le chien"], "fr")


def test_gap_card_asks_the_sentence_and_accepts_the_connector():
    gap = card(
        "gap",
        kind="grammar",
        sentence="Je prends le vélo ___ aller au travail.",
        gap_answer="pour",
        choices=["pour", "mais"],
    )
    assert gap.question == "Je prends le vélo ___ aller au travail."
    assert gap.accepted == ["pour"]
    assert gap.answer_lang == "fr"


def test_translate_card_asks_the_completed_sentence():
    translate = card("translate", kind="grammar", sentence="Je prends le vélo ___ aller au travail.")
    assert translate.question == "Je prends le vélo pour aller au travail." or (
        translate.question == translate.french[0]
    )
    assert translate.accepted == ["Ik neem de fiets om naar mijn werk te gaan"]
    assert translate.answer_lang == "nl"


def test_primary_and_secondary_per_kind():
    assert (PRIMARY["vocab"], SECONDARY["vocab"]) == ("fr_nl", "nl_fr")
    assert (PRIMARY["grammar"], SECONDARY["grammar"]) == ("gap", "translate")
    assert card("gap", kind="grammar").is_primary is True
    assert card("translate", kind="grammar").is_primary is False
    assert card("fr_nl").is_primary is True
