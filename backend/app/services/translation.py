"""Multilingual translation via OpenAI-compatible LLM (vLLM / Ollama / cloud)."""

from __future__ import annotations

import json
from dataclasses import dataclass

from loguru import logger

LANG_NAMES = {"en": "English", "hi": "Hindi", "mr": "Marathi"}


@dataclass
class TranslatedSegment:
    start: float
    end: float
    source_text: str
    translated_text: str
    status: str = "translated"


def _llm_endpoint():
    from app.services.llm_provider import text_endpoint
    return text_endpoint()


def translate_segments(
    segments: list[dict],
    target_language: str,
    source_language: str = "auto",
) -> tuple[list[TranslatedSegment], bool]:
    """Translate transcript segments. Returns (results, used_llm)."""
    target = LANG_NAMES.get(target_language, target_language)
    ep = _llm_endpoint()

    if ep is None or not segments:
        return [
            TranslatedSegment(
                start=s["start"],
                end=s["end"],
                source_text=s["text"],
                translated_text=f"[{target_language}] {s['text']}",
                status="stub",
            )
            for s in segments
        ], False

    batch = segments[:40]
    payload_lines = [{"i": i, "text": s["text"]} for i, s in enumerate(batch)]
    prompt = (
        f"Translate each item's text into {target}. "
        f"Source language hint: {source_language}. "
        'Return ONLY a JSON array of {"i": <int>, "text": "<translated>"} '
        "with the same indices. Keep meaning; do not add commentary.\n\n"
        f"{json.dumps(payload_lines, ensure_ascii=False)}"
    )

    try:
        from app.services.llm_provider import make_client

        client = make_client(ep)
        resp = client.chat.completions.create(
            model=ep.model,
            messages=[
                {"role": "system", "content": "You are a precise translator. Output valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )
        raw = (resp.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:].strip()
        parsed = json.loads(raw)
        by_i = {int(item["i"]): item["text"] for item in parsed if "i" in item and "text" in item}
        out = []
        for i, s in enumerate(batch):
            out.append(TranslatedSegment(
                start=s["start"],
                end=s["end"],
                source_text=s["text"],
                translated_text=by_i.get(i, f"[{target_language}] {s['text']}"),
                status="translated" if i in by_i else "partial",
            ))
        for s in segments[40:]:
            out.append(TranslatedSegment(
                start=s["start"], end=s["end"], source_text=s["text"],
                translated_text=f"[{target_language}] {s['text']}", status="stub",
            ))
        logger.info(f"Translated {len(by_i)}/{len(batch)} segments via {model}")
        return out, True
    except Exception as exc:
        logger.warning(f"Translation LLM failed, using stubs: {exc}")
        return [
            TranslatedSegment(
                start=s["start"], end=s["end"], source_text=s["text"],
                translated_text=f"[{target_language}] {s['text']}", status="stub_fallback",
            )
            for s in segments
        ], False
