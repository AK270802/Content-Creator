"""Engagement / virality scoring (7-signal model).

Scores a video using stored analysis data (scenes, transcript, timeline events).
No re-processing required — all inputs are from the standard pipeline run.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.video import Scene, Segment
from app.models.timeline import TimelineEvent
from app.config import settings

log = logging.getLogger(__name__)


@dataclass
class SignalScore:
    name: str
    score: float        # 0–100
    weight: float
    explanation: str


@dataclass
class EngagementResult:
    video_id: str
    total_score: float          # 0–100 weighted average
    grade: str                  # A / B / C / D / F
    signals: list[SignalScore]
    suggestions: list[str]
    strong_hooks: list[dict]    # [{start, end, reason, confidence, source}]
    metadata: dict = field(default_factory=dict)


# ── signal scorers ────────────────────────────────────────────────────────────

def _score_hook_strength(segments: list[Any]) -> SignalScore:
    if not segments:
        return SignalScore("Hook Strength", 20, 0.20, "No transcript available")
    first_30 = [s for s in segments if s.start_time < 30]
    word_count = sum(len(s.text.split()) for s in first_30)
    text = " ".join(s.text for s in first_30).lower()
    score = min(word_count * 1.5, 50)
    bonus = 0
    if any(w in text for w in ["?", "!", "you", "this", "how", "why", "what"]):
        bonus += 25
    if any(w in text for w in ["secret", "never", "always", "best", "worst", "real"]):
        bonus += 15
    if word_count < 10:
        explanation = "Weak hook — less than 10 words in first 30s"
    elif score + bonus >= 70:
        explanation = "Strong hook with engaging language"
    else:
        explanation = "Moderate hook — consider a stronger opening"
    return SignalScore("Hook Strength", min(score + bonus, 100), 0.20, explanation)


def _score_content_density(segments: list[Any], duration: float) -> SignalScore:
    if not segments or duration <= 0:
        return SignalScore("Content Density", 30, 0.15, "No transcript available")
    total_words = sum(len(s.text.split()) for s in segments)
    wpm = (total_words / duration) * 60
    if 100 <= wpm <= 160:
        score, explanation = 90, f"{wpm:.0f} wpm — ideal content density"
    elif 80 <= wpm < 100 or 160 < wpm <= 200:
        score, explanation = 65, f"{wpm:.0f} wpm — slightly outside ideal range"
    elif wpm < 80:
        score, explanation = 30, f"{wpm:.0f} wpm — too slow, consider cutting silences"
    else:
        score, explanation = 40, f"{wpm:.0f} wpm — very fast, may be hard to follow"
    return SignalScore("Content Density", score, 0.15, explanation)


def _score_visual_quality(scenes: list[Any]) -> SignalScore:
    if not scenes:
        return SignalScore("Visual Quality", 50, 0.15, "No scene analysis available")
    total, penalized = len(scenes), 0
    for scene in scenes:
        flags = scene.quality_flags or {}
        if isinstance(flags, dict):
            penalized += sum(1 for v in flags.values() if v)
        elif isinstance(flags, list):
            penalized += len(flags)
    penalty_rate = penalized / (total * 4)
    score = max(0, 100 - penalty_rate * 100)
    if score >= 80:
        explanation = "Good visual quality throughout"
    elif score >= 60:
        explanation = "Some quality issues (blur, framing) detected"
    else:
        explanation = "Multiple quality issues — consider cutting or regrading affected scenes"
    return SignalScore("Visual Quality", score, 0.15, explanation)


def _score_topic_variety(events: list[Any]) -> SignalScore:
    if not events:
        return SignalScore("Topic Variety", 30, 0.10, "No timeline events available")
    all_tags: set[str] = set()
    for ev in events:
        tags = ev.tags or []
        if isinstance(tags, dict):
            all_tags.update(str(v) for v in tags.values())
        elif isinstance(tags, list):
            all_tags.update(str(t) for t in tags)
    unique_topics = len(all_tags)
    score = min(unique_topics * 8, 100)
    return SignalScore("Topic Variety", score, 0.10, f"{unique_topics} unique topics/tags detected")


def _score_pacing(scenes: list[Any], duration: float) -> SignalScore:
    if not scenes or duration <= 0:
        return SignalScore("Pacing", 40, 0.15, "No scene data available")
    cuts_per_min = (len(scenes) / duration) * 60
    if 3 <= cuts_per_min <= 8:
        score, explanation = 90, f"{cuts_per_min:.1f} cuts/min — great pacing"
    elif 1 <= cuts_per_min < 3:
        score, explanation = 55, f"{cuts_per_min:.1f} cuts/min — pacing is slow"
    elif 8 < cuts_per_min <= 15:
        score, explanation = 70, f"{cuts_per_min:.1f} cuts/min — slightly fast but acceptable"
    else:
        score, explanation = 35, f"{cuts_per_min:.1f} cuts/min — pacing needs adjustment"
    return SignalScore("Pacing", score, 0.15, explanation)


def _score_emotional_arc(segments: list[Any]) -> SignalScore:
    if not segments:
        return SignalScore("Emotional Arc", 20, 0.10, "No transcript available")
    text = " ".join(s.text.lower() for s in segments)
    high = ["amazing", "incredible", "wow", "love", "hate", "fear",
            "excited", "shocked", "terrible", "brilliant", "fail", "win"]
    med = ["good", "bad", "feel", "think", "believe", "hope",
           "worry", "happy", "sad", "angry", "proud"]
    high_count = sum(text.count(w) for w in high)
    med_count = sum(text.count(w) for w in med)
    density = (high_count * 2 + med_count) / max(len(text.split()), 1)
    score = min(density * 500, 100)
    if score >= 70:
        explanation = "Strong emotional language — high resonance potential"
    elif score >= 40:
        explanation = "Moderate emotional content"
    else:
        explanation = "Low emotional language — consider more engaging framing"
    return SignalScore("Emotional Arc", score, 0.10, explanation)


def _score_retention_risk(segments: list[Any], duration: float) -> SignalScore:
    if not segments or duration <= 0:
        return SignalScore("Retention Risk", 70, 0.15, "No transcript available")
    gaps: list[float] = []
    for i in range(1, len(segments)):
        gap = segments[i].start_time - segments[i - 1].end_time
        if gap > 0:
            gaps.append(gap)
    long_silences = sum(1 for g in gaps if g > 3)
    total_silence = sum(g for g in gaps if g > 1)
    silence_ratio = total_silence / duration
    score = max(0, min(100, 100 - long_silences * 8 - silence_ratio * 60))
    if long_silences == 0:
        explanation = "No problematic silences detected"
    elif long_silences <= 3:
        explanation = f"{long_silences} long silences — consider trimming"
    else:
        explanation = f"{long_silences} long silences — significant retention risk"
    return SignalScore("Retention Risk", score, 0.15, explanation)


def _find_strong_hooks(segments: list[Any], events: list[Any], n: int = 3) -> list[dict]:
    moments: list[dict] = []
    for ev in sorted(events, key=lambda e: e.confidence, reverse=True)[:n * 2]:
        moments.append({
            "start": ev.start_ts,
            "end": ev.end_ts,
            "reason": ev.description or "High-confidence visual event",
            "confidence": ev.confidence,
            "source": "visual",
        })
    triggers = ["?", "!", "never", "always", "secret", "truth", "actually",
                "but wait", "here's", "the thing is", "you need to"]
    for seg in segments:
        if any(t in seg.text.lower() for t in triggers):
            moments.append({
                "start": seg.start_time,
                "end": seg.end_time,
                "reason": f"Engaging phrase: {seg.text[:60]}",
                "confidence": 0.7,
                "source": "transcript",
            })
    deduped: list[dict] = []
    for m in sorted(moments, key=lambda x: x["confidence"], reverse=True):
        if not any(abs(m["start"] - d["start"]) < 10 for d in deduped):
            deduped.append(m)
        if len(deduped) >= n:
            break
    return deduped


def _to_grade(score: float) -> str:
    if score >= 85: return "A"
    if score >= 70: return "B"
    if score >= 55: return "C"
    if score >= 40: return "D"
    return "F"


def _build_suggestions(signals: list[SignalScore]) -> list[str]:
    suggestions = []
    for sig in sorted([s for s in signals if s.score < 60], key=lambda s: s.score)[:3]:
        suggestions.append({
            "Hook Strength": "Open with a bold question or surprising statement to improve hook",
            "Content Density": "Trim silences and filler words to tighten content density",
            "Visual Quality": "Cut or grade scenes flagged for blur or poor framing",
            "Pacing": "Add more cuts or B-roll to improve pacing",
            "Emotional Arc": "Use stronger emotional language and storytelling beats",
            "Retention Risk": "Remove long pauses to reduce audience drop-off",
        }.get(sig.name, f"Improve {sig.name}: {sig.explanation}"))
    return suggestions


# ── public API ────────────────────────────────────────────────────────────────

async def score_engagement(
    video_id: str,
    db: AsyncSession,
    duration: float | None = None,
) -> EngagementResult:
    scenes_q = await db.execute(select(Scene).where(Scene.video_id == video_id))
    scenes = list(scenes_q.scalars())

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

    if duration is None:
        if scenes:
            duration = max(s.end_time for s in scenes)
        elif segments:
            duration = segments[-1].end_time
        else:
            duration = 0.0

    signals = [
        _score_hook_strength(segments),
        _score_content_density(segments, duration),
        _score_visual_quality(scenes),
        _score_topic_variety(events),
        _score_pacing(scenes, duration),
        _score_emotional_arc(segments),
        _score_retention_risk(segments, duration),
    ]

    total = sum(s.score * s.weight for s in signals) / sum(s.weight for s in signals)

    return EngagementResult(
        video_id=video_id,
        total_score=round(total, 1),
        grade=_to_grade(total),
        signals=signals,
        suggestions=_build_suggestions(signals),
        strong_hooks=_find_strong_hooks(segments, events),
        metadata={
            "scene_count": len(scenes),
            "segment_count": len(segments),
            "event_count": len(events),
            "duration": duration,
        },
    )
