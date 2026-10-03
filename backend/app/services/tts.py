"""Speech synthesis for dubbing — modular providers.

Providers (configured via env TTS_PROVIDER):
- edge: Microsoft Edge TTS when edge-tts is installed
- openai: OpenAI-compatible /audio/speech endpoint
- stub: writes silence WAV for pipeline testing
"""

from __future__ import annotations

import os
import wave
from dataclasses import dataclass

from loguru import logger

EDGE_VOICES = {
    "en": "en-US-JennyNeural",
    "hi": "hi-IN-SwaraNeural",
    "mr": "mr-IN-AarohiNeural",
}


@dataclass
class DubResult:
    audio_path: str
    provider: str
    voice: str
    duration_s: float
    message: str


def _write_silence_wav(path: str, duration_s: float, rate: int = 24000) -> float:
    n = int(rate * max(duration_s, 0.1))
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * n)
    return n / rate


def synthesize_speech(
    text: str,
    target_language: str,
    output_path: str,
    *,
    voice_clone: bool = False,
) -> DubResult:
    """Synthesize speech. Prefer edge-tts, then OpenAI speech, else silence stub."""
    if voice_clone:
        logger.warning("Voice clone requested but no clone provider configured — using standard voice")

    try:
        from app.config import settings
        provider = (settings.tts_provider or "auto").lower()
        tts_model = settings.tts_model
        vllm = settings.vllm_base_url
        planning = settings.planning_model_base_url
    except Exception:
        provider, tts_model, vllm, planning = "auto", "tts-1", "", ""

    voice = EDGE_VOICES.get(target_language, EDGE_VOICES["en"])

    if provider in ("auto", "edge"):
        try:
            import asyncio
            import edge_tts

            async def _run():
                communicate = edge_tts.Communicate(text, voice)
                await communicate.save(output_path)

            asyncio.run(_run())
            dur = max(1.0, len(text.split()) / 2.5)
            try:
                import subprocess
                import json
                r = subprocess.run(
                    ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", output_path],
                    capture_output=True, text=True, check=True,
                )
                dur = float(json.loads(r.stdout)["format"]["duration"])
            except Exception:
                pass
            return DubResult(output_path, "edge", voice, dur, "Synthesized with edge-tts")
        except Exception as exc:
            logger.debug(f"edge-tts unavailable: {exc}")

    base = (vllm or planning or "").rstrip("/")
    if provider in ("auto", "openai") and base:
        try:
            from openai import OpenAI
            client = OpenAI(
                base_url=f"{base}/v1" if not base.endswith("/v1") else base,
                api_key=os.environ.get("OPENAI_API_KEY", "local"),
            )
            with client.audio.speech.with_streaming_response.create(
                model=tts_model,
                voice="alloy",
                input=text[:4000],
            ) as response:
                response.stream_to_file(output_path)
            return DubResult(
                output_path, "openai", "alloy",
                max(1.0, len(text.split()) / 2.5),
                "OpenAI-compatible TTS",
            )
        except Exception as exc:
            logger.debug(f"OpenAI TTS unavailable: {exc}")

    dur = _write_silence_wav(output_path, max(1.0, len(text.split()) / 2.5))
    return DubResult(
        output_path,
        "stub",
        voice,
        dur,
        "TTS stub (silence). Install edge-tts or set TTS_PROVIDER for real audio.",
    )


def synthesize_segment_batch(
    segments: list[dict],
    target_language: str,
    workdir: str,
) -> list[DubResult]:
    results = []
    for i, seg in enumerate(segments):
        text = seg.get("translated_text") or seg.get("text") or ""
        if not text.strip():
            continue
        out = os.path.join(workdir, f"dub_{i:03d}.mp3")
        try:
            results.append(synthesize_speech(text, target_language, out))
        except Exception as exc:
            logger.warning(f"Segment {i} TTS failed: {exc}")
            wav = os.path.join(workdir, f"dub_{i:03d}.wav")
            results.append(synthesize_speech(text, target_language, wav))
    return results
