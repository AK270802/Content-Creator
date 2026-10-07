"""Smart clip selection — transcript + LLM in, ranked highlight clips out."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.video import Segment
from app.models.timeline import TimelineEvent

log = logging.getLogger(__name__)

WINDOW_S = 8 * 60   # 8 min window
OVERLAP_S = 60       # 1 min overlap
DEDUPE_IOU = 0.4     # clips sharing ≥40% overlap = duplicate


@dataclass
class ClipCandidate:
    start: float
    end: float
    score: float          # 0–100
    hook: str             # one-sentence hook/title
    reason: str
    tags: list[str]


@dataclass
class SmartClipsResult:
    video_id: str
    clips: list[ClipCandidate]
    total_analyzed: float  # seconds of speech analyzed


# ── helpers ──────────────────────────────────────────────────────────────────

def _iou(a: ClipCandidate, b: ClipCandidate) -> float:
    start = max(a.start, b.start)
    end = min(a.end, b.end)
    if end <= start:
        return 0.0
    intersection = end - start
    union = (a.end - a.start) + (b.end - b.start) - intersection
    return intersection / union if union > 0 else 0.0


def _deduplicate(candidates: list[ClipCandidate]) -> list[ClipCandidate]:
    candidates.sort(key=lambda c: c.score, reverse=True)
    kept: list[ClipCandidate] = []
    for cand in candidates:
        if not any(_iou(cand, k) >= DEDUPE_IOU for k in kept):
            kept.append(cand)
    return kept


def _build_transcript_text(segments: list[Any], start: float, end: float) -> str:
    lines = []
    for seg in segments:
        if seg.start_time >= start and seg.end_time <= end:
            ts = f"[{seg.start_time:.1f}s]"
            lines.append(f"{ts} {seg.text.strip()}")
    return "\n".join(lines) if lines else ""


def _build_window_prompt(
    window_text: str,
    window_start: float,
    window_end: float,
    target_duration: float,
    max_clips: int,
) -> str:
    return f"""You are a viral content editor. Analyze the transcript below and select the best highlight clips.

Transcript window ({window_start:.0f}s – {window_end:.0f}s):
{window_text}

Instructions:
- Find up to {max_clips} highlight clips worth sharing on social media
- Each clip should be {target_duration:.0f}–{target_duration * 1.5:.0f} seconds long
- Focus on: strong hooks, memorable moments, useful insights, emotional peaks, surprising facts
- Avoid: off-topic tangents, long silences, incomplete thoughts

Respond ONLY with a JSON array. Each item must have:
{{
  "start": <float seconds>,
  "end": <float seconds>,
  "score": <integer 0-100>,
  "hook": "<one-sentence hook title>",
  "reason": "<why this is a highlight>",
  "tags": ["<tag1>", "<tag2>"]
}}

JSON array:"""


async def _call_llm(prompt: str) -> str | None:
    from app.services.llm_provider import text_endpoint
    ep = text_endpoint()
    if ep is None:
        return None
    headers = {"Authorization": f"Bearer {ep.api_key}"} if ep.api_key else {}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{ep.base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json={
                    "model": ep.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 1500,
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        log.warning("LLM call failed: %s", exc)
        return None


def _parse_llm_response(text: str) -> list[ClipCandidate]:
    try:
        start = text.find("[")
        end = text.rfind("]") + 1
        if start < 0 or end <= start:
            return []
        data = json.loads(text[start:end])
        candidates = []
        for item in data:
            try:
                candidates.append(ClipCandidate(
                    start=float(item["start"]),
                    end=float(item["end"]),
                    score=float(item.get("score", 50)),
                    hook=str(item.get("hook", "")),
                    reason=str(item.get("reason", "")),
                    tags=list(item.get("tags", [])),
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return candidates
    except (json.JSONDecodeError, ValueError):
        return []


def _heuristic_clips(
    segments: list[Any],
    events: list[Any],
    target_duration: float,
    max_clips: int,
) -> list[ClipCandidate]:
    """Fallback when LLM unavailable: high-confidence events + word-dense windows."""
    candidates: list[ClipCandidate] = []

    for ev in sorted(events, key=lambda e: e.confidence, reverse=True):
        duration = ev.end_ts - ev.start_ts
        if 0.5 * target_duration <= duration <= 2 * target_duration:
            candidates.append(ClipCandidate(
                start=ev.start_ts,
                end=ev.end_ts,
                score=int(ev.confidence * 80),
                hook=ev.description[:80] if ev.description else "Key moment",
                reason="High-confidence event from visual analysis",
                tags=list(ev.tags or [])[:3],
            ))

    if len(candidates) < max_clips and segments:
        step = target_duration
        total = segments[-1].end_time if segments else 0
        t = 0.0
        while t + target_duration <= total and len(candidates) < max_clips:
            window_segs = [
                s for s in segments
                if s.start_time >= t and s.end_time <= t + target_duration
            ]
            word_count = sum(len(s.text.split()) for s in window_segs)
            if word_count > 30:
                candidates.append(ClipCandidate(
                    start=t,
                    end=t + target_duration,
                    score=min(int(word_count / 2), 60),
                    hook="Dense content segment",
                    reason=f"{word_count} words in {target_duration:.0f}s window",
                    tags=["transcript"],
                ))
            t += step

    return _deduplicate(candidates)[:max_clips]


# ── public API ────────────────────────────────────────────────────────────────

async def detect_smart_clips(
    video_id: str,
    db: AsyncSession,
    *,
    target_duration: float = 30.0,
    max_clips: int = 5,
) -> SmartClipsResult:
    """Select highlight clips from stored transcript and timeline events."""
    segments_q = await db.execute(
        select(Segment).where(Segment.video_id == video_id).order_by(Segment.start_time)
    )
    segments = list(segments_q.scalars())

    events_q = await db.execute(
        select(TimelineEvent)
        .where(
            TimelineEvent.video_id == video_id,
            TimelineEvent.confidence >= settings.timeline_confidence_threshold,
        )
        .order_by(TimelineEvent.start_ts)
    )
    events = list(events_q.scalars())

    if not segments and not events:
        return SmartClipsResult(video_id=video_id, clips=[], total_analyzed=0)

    total_duration = segments[-1].end_time if segments else 0.0

    from app.services.llm_provider import text_endpoint
    if text_endpoint() is None:
        clips = _heuristic_clips(segments, events, target_duration, max_clips)
        return SmartClipsResult(video_id=video_id, clips=clips, total_analyzed=total_duration)

    all_candidates: list[ClipCandidate] = []
    window_start = 0.0

    while window_start < total_duration:
        window_end = min(window_start + WINDOW_S, total_duration)
        text = _build_transcript_text(segments, window_start, window_end)

        if text:
            prompt = _build_window_prompt(
                text, window_start, window_end, target_duration, max_clips
            )
            raw = await _call_llm(prompt)
            if raw:
                candidates = _parse_llm_response(raw)
                for c in candidates:
                    c.start = max(c.start, 0)
                    c.end = min(c.end, total_duration)
                all_candidates.extend(candidates)

        if window_end >= total_duration:
            break
        window_start += WINDOW_S - OVERLAP_S

    if not all_candidates:
        clips = _heuristic_clips(segments, events, target_duration, max_clips)
    else:
        clips = _deduplicate(all_candidates)[:max_clips]

    clips.sort(key=lambda c: c.score, reverse=True)
    return SmartClipsResult(video_id=video_id, clips=clips, total_analyzed=total_duration)
