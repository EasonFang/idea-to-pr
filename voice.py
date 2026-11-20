"""Voice transcription via local faster-whisper.

Local, free, private, no per-minute API cost. The model is loaded lazily so
importing this module in CI does not require the heavy dependency.

Text and voice converge on the same queue: the worker receives plain text and
cannot (and need not) tell where it came from.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class Transcriber:
    def __init__(self, model_size: str = "small", downloader=None):
        self.model_size = model_size
        self.downloader = downloader  # callable(file_id, dest) -> Path
        self._model = None

    def _get_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel  # lazy import

            logger.info("loading faster-whisper model=%s", self.model_size)
            self._model = WhisperModel(self.model_size, device="auto")
        return self._model

    def transcribe_file(self, path: str | Path) -> str:
        model = self._get_model()
        segments, _info = model.transcribe(str(path))
        return " ".join(segment.text.strip() for segment in segments).strip()

    def transcribe_voice(self, file_id: str) -> str:
        """Download the Telegram voice file, then transcribe it locally."""
        if self.downloader is None:
            raise RuntimeError("no downloader configured for voice files")
        dest = self.downloader(file_id)
        return self.transcribe_file(dest)


class FakeTranscriber:
    """Test double returning fixed text."""

    def __init__(self, text: str = "transcribed text"):
        self.text = text

    def transcribe_voice(self, file_id: str) -> str:
        return self.text
