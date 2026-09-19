from datetime import time

from french_srs_bot import messages, session
from french_srs_bot.grading import Grade, GradeResult
from french_srs_bot.models import CardView, DayStats


def make_card(direction="fr_nl", gender="f", hint=None):
    return CardView(
        card_id=1, item_id=1, direction=direction, french=["l'école"], dutch=["de school"],
        gender=gender, hint=hint, introduced_at=None, srs=None,
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
