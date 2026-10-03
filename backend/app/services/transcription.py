from loguru import logger
from app.config import settings


class TranscriptionService:
    def __init__(self) -> None:
        self._model = None

    def _load_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            logger.info(f"Loading Whisper model={settings.whisper_model} device={settings.whisper_device}")
            self._model = WhisperModel(
                settings.whisper_model,
                device=settings.whisper_device,
                compute_type=settings.whisper_compute_type,
            )
        return self._model

    def transcribe(self, audio_path: str) -> list[dict]:
        model = self._load_model()
        segments, info = model.transcribe(audio_path, beam_size=5, word_timestamps=False)
        logger.info(f"Transcribed {audio_path}: detected language={info.language}")
        result = []
        for seg in segments:
            result.append({
                "start": round(seg.start, 3),
                "end": round(seg.end, 3),
                "text": seg.text.strip(),
            })
        return result


transcription_service = TranscriptionService()
