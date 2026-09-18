FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The French voice (63 MB) is baked in: the pod runs with a read-only filesystem, and this
# way a restart never depends on an external download. Must match settings.yaml's tts.voice.
ARG PIPER_VOICE=fr_FR-siwis-medium
RUN python -c "from pathlib import Path; from piper.download_voices import download_voice; d = Path('/app/voices'); d.mkdir(parents=True, exist_ok=True); download_voice('$PIPER_VOICE', d)"

COPY src ./src
COPY db ./db
COPY settings.yaml .

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER 1000
CMD ["python", "-m", "french_srs_bot"]
