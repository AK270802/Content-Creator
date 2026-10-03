"""Silence and filler-word detection.

Analyzes stored transcript segments to find:
- Silent gaps between spoken segments
- Filler words (um, uh, like, you know, etc.)

All inputs from stored transcript data — no re-processing required.
"""
from __future__ import annotations

import re
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.video import Segment

log = logging.getLogger(__name__)

FILLER_WORDS = frozenset([
    "um", "uh", "umm", "uhh", "er", "err",
    "like", "you know", "kind of", "kinda", "sort of",
    "basically", "literally", "honestly", "i mean",
    "so", "and so", "actually", "essentially",
])

DEFAULT_SILENCE_THRESHOLD_S = 1.5
LONG_SILENCE_THRESHOLD_S = 3.0


@dataclass
class SilenceRange:
    start: float
    end: float
    duration: float
    severity: str  # "short" | "long"


@dataclass
class FillerOccurrence:
    start: float
    end: float
    text: str
    segment_id: str


@dataclass
class SilenceDetectionResult:
    video_id: str
    silences: list[SilenceRange]
    fillers: list[FillerOccurrence]
    total_silence_s: float
    total_filler_s: float
    saveable_s: float
    wpm_before: float
    wpm_after: float


def _detect_silences(
    segments: list[Any],
    threshold: float = DEFAULT_SILENCE_THRESHOLD_S,
) -> list[SilenceRange]:
    silences: list[SilenceRange] = []
    for i in range(1, len(segments)):
        gap_start = segments[i - 1].end_time
        gap_end = segments[i].start_time
        gap = gap_end - gap_start
        if gap >= threshold:
            silences.append(SilenceRange(
                start=gap_start,
                end=gap_end,
                duration=gap,
                severity="long" if gap >= LONG_SILENCE_THRESHOLD_S else "short",
            ))
    return silences


def _detect_fillers(segments: list[Any]) -> list[FillerOccurrence]:
    occurrences: list[FillerOccurrence] = []
    for seg in segments:
        text_lower = re.sub(r"[^\w\s]", "", seg.text.lower().strip())
        for phrase in sorted(FILLER_WORDS, key=len, reverse=True):
            if phrase in text_lower:
                seg_words = text_lower.split()
                filler_words = phrase.split()
                if len(filler_words) >= len(seg_words) * 0.6:
                    occurrences.append(FillerOccurrence(
                        start=seg.start_time,
                        end=seg.end_time,
                        text=seg.text,
                        segment_id=str(seg.id),
                    ))
                    break
    return occurrences


async def detect_silence_and_fillers(
    video_id: str,
    db: AsyncSession,
    *,
    silence_threshold: float = DEFAULT_SILENCE_THRESHOLD_S,
) -> SilenceDetectionResult:
    segments_q = await db.execute(
        select(Segment).where(Segment.video_id == video_id).order_by(Segment.start_time)
    )
    segments = list(segments_q.scalars())

    if not segments:
        return SilenceDetectionResult(
            video_id=video_id, silences=[], fillers=[],
            total_silence_s=0, total_filler_s=0,
            saveable_s=0, wpm_before=0, wpm_after=0,
        )

    silences = _detect_silences(segments, silence_threshold)
    fillers = _detect_fillers(segments)

    total_silence = sum(s.duration for s in silences)
    total_filler = sum(f.end - f.start for f in fillers)
    saveable = total_silence + total_filler

    duration = segments[-1].end_time
    total_words = sum(len(s.text.split()) for s in segments)
    wpm_before = (total_words / duration * 60) if duration > 0 else 0

    filler_ids = {f.segment_id for f in fillers}
    kept_words = sum(len(s.text.split()) for s in segments if str(s.id) not in filler_ids)
    effective_duration = max(duration - saveable, 1)
    wpm_after = kept_words / effective_duration * 60

    return SilenceDetectionResult(
        video_id=video_id,
        silences=silences,
        fillers=fillers,
        total_silence_s=round(total_silence, 2),
        total_filler_s=round(total_filler, 2),
        saveable_s=round(saveable, 2),
        wpm_before=round(wpm_before, 1),
        wpm_after=round(wpm_after, 1),
    )
