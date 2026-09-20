from datetime import time

from french_srs_bot import messages, session
from french_srs_bot.grading import Grade, GradeResult
from french_srs_bot.models import CardView, DayStats, ThemeProgress


def make_card(direction="fr_nl", gender="f", hint=None):
    return CardView(
        card_id=1, item_id=1, direction=direction, french=["l'école"], dutch=["de school"],
        gender=gender, hint=hint, introduced_at=None, srs=None,
    )


def grammar_card(direction):
    return CardView(
        card_id=1,
        item_id=1,
        direction=direction,
        french=["Je prends le vélo pour aller au travail."],
        dutch=["Ik neem de fiets om naar mijn werk te gaan"],
        gender=None,
        hint="parce qu' voor een klinker",
        introduced_at=None,
        srs=None,
        kind="grammar",
        sentence="Je prends le vélo ___ aller au travail.",
        gap_answer="pour",
        rule="pour + infinitief = doel",
        choices=["pour", "parce que", "mais"],
    )


def test_prompt_shows_question_in_the_right_direction():
    assert "l&#x27;école" in messages.prompt(make_card("fr_nl"))
    assert "de school" in messages.prompt(make_card("nl_fr"))


def test_prompt_shows_hint_only_for_nl_fr():
    assert "vrouwelijk" in messages.prompt(make_card("nl_fr", hint="vrouwelijk"))
    assert "vrouwelijk" not in messages.prompt(make_card("fr_nl", hint="vrouwelijk"))


def test_intro_mentions_gender():
    assert "(vrouwelijk)" in messages.intro(make_card())


def test_feedback_per_reason():
    card = make_card()
    assert messages.feedback(GradeResult(Grade.CORRECT, "exact", "l'école"), card) == "✅ Parfait !"
    assert "accenten" in messages.feedback(GradeResult(Grade.ALMOST, "accent", "l'école"), card)
    assert "lidwoord" in messages.feedback(GradeResult(Grade.HARD, "article", "l'école"), card)
    assert "typefout" in messages.feedback(GradeResult(Grade.HARD, "typo", "l'école"), card)
    assert "juiste antwoord" in messages.feedback(GradeResult(Grade.WRONG, "wrong", "l'école"), card)


def test_summary_variants():
    assert "Dagdoel gehaald" in messages.summary(10, 10, 0, 3, more_available=False)
    assert "Streak: 3 dagen" in messages.summary(10, 10, 0, 3, more_available=False)
    assert "nog 5 herhalingen" in messages.summary(4, 10, 5, 0, more_available=True)
    assert "niets meer" in messages.summary(4, 10, 0, 0, more_available=False)


def test_summary_offers_more_when_a_new_card_is_still_available():
    text = messages.summary(4, 10, 0, 0, more_available=True)
    assert "niets meer" not in text
    assert "Zin in meer? /practice" in text


def test_reminder():
    assert "3/10" in messages.reminder(DayStats(total=3, new=1, reviews=2), 10)


def test_user_text_is_escaped():
    card = CardView(1, 1, "fr_nl", ["<b>x</b>"], ["y"], None, None, None, None)
    assert "<b>x</b>" not in messages.prompt(card)


def test_welcome_lists_configured_batch_times():
    assert "07:30, 12:00 en 18:15" in messages.welcome([time(7, 30), time(12), time(18, 15)])
    assert "om 09:00." in messages.welcome([time(9)])


def test_welcome_mentions_stand():
    assert "/stand" in messages.welcome([time(9)])


def test_peer_reached_goal_names_the_other_and_your_own_score():
    text = messages.peer_reached_goal("Inga", done=12, goal=30)

    assert "Inga" in text and "12" in text and "30" in text


def test_peer_reached_goal_says_something_different_when_you_are_done_too():
    both = messages.peer_reached_goal("Inga", done=30, goal=30)
    behind = messages.peer_reached_goal("Inga", done=12, goal=30)

    assert both != behind


def test_standings_lists_every_user_with_their_own_goal():
    rows = [
        session.Standing(name="Niels", cards=168, days_reached=5, goal=30),
        session.Standing(name="Inga", cards=142, days_reached=6, goal=15),
    ]

    text = messages.standings(rows)

    assert "Niels" in text and "168" in text and "5" in text and "30" in text
    assert "Inga" in text and "142" in text and "6" in text and "15" in text


def test_help_lists_every_command_and_the_personal_goal():
    text = messages.help_text((time(8), time(19)), goal=30)

    assert "/practice" in text and "/stand" in text and "/help" in text
    assert "08:00" in text and "19:00" in text
    assert "30 kaarten" in text


def test_announcement_numbers_only_go_up_and_are_unique():
    numbers = [number for number, _text in messages.ANNOUNCEMENTS]

    assert numbers == sorted(set(numbers))


def test_only_announcements_above_the_last_seen_one_are_offered():
    highest = max(number for number, _text in messages.ANNOUNCEMENTS)

    assert messages.unseen_announcements(0) == list(messages.ANNOUNCEMENTS)
    assert messages.unseen_announcements(highest) == []


def test_study_order_marks_where_you_are():
    themes = [
        ThemeProgress(position=1, name="Transport", cards=24, started=24),
        ThemeProgress(position=2, name="Se déplacer", cards=40, started=7),
        ThemeProgress(position=3, name="Nombres", cards=20, started=0),
    ]

    text = messages.study_order(themes)

    assert "✅ Transport — 24/24" in text
    assert "▶️ Se déplacer — 7/40" in text
    assert "⬜ Nombres — 0/20" in text


def test_study_order_survives_an_empty_database():
    assert messages.study_order([]) != ""


def test_gap_prompt_shows_the_sentence_with_the_gap():
    card = grammar_card("gap")
    text = messages.prompt(card)
    assert "___" in text
    assert "Vul het ontbrekende" in text


def test_gap_feedback_shows_the_rule_and_the_full_sentence():
    card = grammar_card("gap")
    correct = messages.gap_feedback(True, card)
    assert "<b>pour</b>" in correct
    assert "pour + infinitief" in correct
    wrong = messages.gap_feedback(False, card)
    assert "Het is" in wrong and "pour" in wrong


def test_translate_prompt_asks_for_the_translation():
    card = grammar_card("translate")
    text = messages.prompt(card)
    assert "🇫🇷 → 🇳🇱" in text
    assert "Je prends le vélo pour aller au travail." in text


def test_override_texts():
    assert "goed" in messages.BUTTON_OVERRIDE.lower()
    assert messages.override_applied()
    assert messages.override_refused()


def test_no_grammar_due_points_at_the_words():
    text = messages.no_grammar_due()
    assert "/practice" in text
