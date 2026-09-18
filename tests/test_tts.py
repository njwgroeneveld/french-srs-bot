from french_srs_bot import tts


def test_a_supported_sample_rate_is_left_alone():
    assert tts.opus_rate(16000) == 16000


def test_22050_is_lifted_to_the_next_supported_rate():
    # Opus knows only 8/12/16/24/48 kHz; the medium voice runs at 22050 Hz.
    assert tts.opus_rate(22050) == 24000


def test_a_rate_above_every_supported_one_falls_back_to_the_highest():
    assert tts.opus_rate(96000) == 48000
