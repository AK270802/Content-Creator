"""Smart Clips, Engagement Scoring, and Silence Detection API."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import get_current_user
from app.models.video import Video, VideoStatus
from app.services.smart_clips import detect_smart_clips
from app.services.engagement import score_engagement
from app.services.silence_detection import detect_silence_and_fillers

router = APIRouter(prefix="/videos/{video_id}", tags=["smart-clips"])


async def _get_ready_video(video_id: UUID, user: dict, db: AsyncSession) -> Video:
    result = await db.execute(select(Video).where(Video.id == video_id))
    video = result.scalar_one_or_none()
    if not video:
        raise HTTPException(404, "Video not found")
    if video.user_id != user["sub"] and user.get("role") not in ("super_admin", "org_admin"):
        raise HTTPException(403, "Forbidden")
    if video.status != VideoStatus.READY:
        raise HTTPException(409, f"Video is not ready (status: {video.status})")
    return video


# ── response schemas ──────────────────────────────────────────────────────────

class ClipCandidateOut(BaseModel):
    start: float
    end: float
    score: float
    hook: str
    reason: str
    tags: list[str]


class SmartClipsResponse(BaseModel):
    video_id: str
    total_analyzed: float
    clips: list[ClipCandidateOut]


class SignalScoreOut(BaseModel):
    name: str
    score: float
    weight: float
    explanation: str


class EngagementResponse(BaseModel):
    video_id: str
    total_score: float
    grade: str
    signals: list[SignalScoreOut]
    suggestions: list[str]
    strong_hooks: list[dict]
    metadata: dict


class SilenceRangeOut(BaseModel):
    start: float
    end: float
    duration: float
    severity: str


class FillerOut(BaseModel):
    start: float
    end: float
    text: str
    segment_id: str


class SilenceDetectionResponse(BaseModel):
    video_id: str
    silences: list[SilenceRangeOut]
    fillers: list[FillerOut]
    total_silence_s: float
    total_filler_s: float
    saveable_s: float
    wpm_before: float
    wpm_after: float


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.get("/smart-clips", response_model=SmartClipsResponse)
async def get_smart_clips(
    video_id: UUID,
    target_duration: float = Query(30.0, ge=10, le=300),
    max_clips: int = Query(5, ge=1, le=20),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Detect highlight clips using transcript + LLM."""
    video = await _get_ready_video(video_id, user, db)
    result = await detect_smart_clips(
        str(video.id), db,
        target_duration=target_duration,
        max_clips=max_clips,
    )
    return SmartClipsResponse(
        video_id=str(result.video_id),
        total_analyzed=result.total_analyzed,
        clips=[
            ClipCandidateOut(
                start=c.start, end=c.end, score=c.score,
                hook=c.hook, reason=c.reason, tags=c.tags,
            )
            for c in result.clips
        ],
    )


@router.get("/engagement", response_model=EngagementResponse)
async def get_engagement_score(
    video_id: UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Compute virality/engagement score (7-signal model)."""
    video = await _get_ready_video(video_id, user, db)
    result = await score_engagement(str(video.id), db, duration=video.duration_seconds)
    return EngagementResponse(
        video_id=str(result.video_id),
        total_score=result.total_score,
        grade=result.grade,
        signals=[
            SignalScoreOut(
                name=s.name, score=s.score,
                weight=s.weight, explanation=s.explanation,
            )
            for s in result.signals
        ],
        suggestions=result.suggestions,
        strong_hooks=result.strong_hooks,
        metadata=result.metadata,
    )


@router.get("/silence-detection", response_model=SilenceDetectionResponse)
async def get_silence_detection(
    video_id: UUID,
    silence_threshold: float = Query(1.5, ge=0.5, le=10.0),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Detect silences and filler words in the transcript."""
    video = await _get_ready_video(video_id, user, db)
    result = await detect_silence_and_fillers(
        str(video.id), db,
        silence_threshold=silence_threshold,
    )
    return SilenceDetectionResponse(
        video_id=str(result.video_id),
        silences=[
            SilenceRangeOut(start=s.start, end=s.end, duration=s.duration, severity=s.severity)
            for s in result.silences
        ],
        fillers=[
            FillerOut(start=f.start, end=f.end, text=f.text, segment_id=f.segment_id)
            for f in result.fillers
        ],
        total_silence_s=result.total_silence_s,
        total_filler_s=result.total_filler_s,
        saveable_s=result.saveable_s,
        wpm_before=result.wpm_before,
        wpm_after=result.wpm_after,
    )
