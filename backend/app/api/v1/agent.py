import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from loguru import logger
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings
from app.db.session import get_db
from app.dependencies import get_current_user_id
from app.models.video import Video, Scene, Segment
from app.models.enums import VideoStatus
from app.schemas.agent import AgentEditRequest, AgentEditResponse
from app.services.agent import run_agent

limiter = Limiter(key_func=get_remote_address)
router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/edit", response_model=AgentEditResponse)
@limiter.limit(settings.rate_limit_agent)
async def agent_edit(
    request,
    body: AgentEditRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    result = await db.execute(
        select(Video).where(Video.id == body.video_id, Video.user_id == user_id)
    )
    video = result.scalar_one_or_none()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    if video.status != VideoStatus.READY:
        raise HTTPException(status_code=409, detail=f"Video not ready (status: {video.status})")

    scenes_result = await db.execute(
        select(Scene).where(Scene.video_id == body.video_id).order_by(Scene.scene_number)
    )
    scenes = [
        {"scene_number": s.scene_number, "start_time": s.start_time, "end_time": s.end_time}
        for s in scenes_result.scalars().all()
    ]

    segs_result = await db.execute(
        select(Segment).where(Segment.video_id == body.video_id).order_by(Segment.start_time)
    )
    transcript = [
        {"start": s.start_time, "end": s.end_time, "text": s.text}
        for s in segs_result.scalars().all()
    ]

    edit_plan, llm_used = run_agent(
        video_id=str(body.video_id),
        instruction=body.instruction,
        transcript=transcript,
        scenes=scenes,
    )
    logger.info(f"Agent edit complete for {body.video_id}, llm_used={llm_used}")
    return AgentEditResponse(
        video_id=body.video_id,
        edit_plan=edit_plan,
        llm_available=llm_used,
    )
