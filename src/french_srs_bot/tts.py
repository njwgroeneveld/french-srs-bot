"""French speech with piper: text in, OGG/Opus bytes out that Telegram plays as a voice memo."""

from __future__ import annotations

import logging
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr
from piper import PiperVoice, SynthesisConfig

from .config import TtsSettings

log = logging.getLogger(__name__)

# Opus accepts only these sample rates. The medium voice delivers 22050 Hz, so its audio
# has to go to 24000 first; without that libsndfile refuses the file.
OPUS_RATES = (8000, 12000, 16000, 24000, 48000)


def opus_rate(rate: int) -> int:
    """The lowest Opus-supported rate that is not below `rate`."""
    return min((r for r in OPUS_RATES if r >= rate), default=max(OPUS_RATES))


@lru_cache(maxsize=2)
def _load(voice: str, voices_dir: str) -> PiperVoice:
    """Load the ONNX model. Costs ~6s on the Pi, so it is reused after that."""
    path = Path(voices_dir) / f"{voice}.onnx"
    log.info("loading piper voice %s", path)
    return PiperVoice.load(path)


def synthesize(text: str, settings: TtsSettings) -> bytes:
    """French text -> OGG/Opus. Blocking and CPU-heavy: call it in a thread."""
    voice = _load(settings.voice, str(settings.voices_dir))
    config = SynthesisConfig(length_scale=settings.length_scale)
    audio = np.concatenate(
        [
            np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16)
            for chunk in voice.synthesize(text, syn_config=config)
        ]
    )
    rate = voice.config.sample_rate
    target = opus_rate(rate)
    if target != rate:
        audio = soxr.resample(audio, rate, target)
    buffer = BytesIO()
    sf.write(buffer, audio, target, format="OGG", subtype="OPUS")
    return buffer.getvalue()
