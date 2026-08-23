import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from loguru import logger
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings
from app.db.session import get_db
from app.dependencies import get_current_user_id
from app.models.video import Video
from app.models.edit_plan import EditPlan, EditPlanSegment, EditPlanStatus
from app.models.render_job import RenderJob, RenderStatus
from app.schemas.edit_plan import EditPlanSchema, EditPlanPatchRequest
from app.schemas.render_job import RenderJobResponse
from app.services.storage import storage_service

limiter = Limiter(key_func=get_remote_address)
router = APIRouter(tags=["edit-plans"])


async def _get_video_for_user(video_id: uuid.UUID, user_id: str, db: AsyncSession) -> Video:
    result = await db.execute(select(Video).where(Video.id == video_id, Video.user_id == user_id))
    video = result.scalar_one_or_none()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    return video


async def _get_plan_for_user(plan_id: uuid.UUID, user_id: str, db: AsyncSession) -> EditPlan:
    result = await db.execute(
        select(EditPlan)
        .join(Video, EditPlan.video_id == Video.id)
        .where(EditPlan.id == plan_id, Video.user_id == user_id)
    )
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Edit plan not found")
    return plan


@router.get("/videos/{video_id}/edit-plan", response_model=EditPlanSchema)
@limiter.limit(settings.rate_limit_default)
async def get_edit_plan(
    request,
    video_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    await _get_video_for_user(video_id, user_id, db)

    result = await db.execute(
        select(EditPlan).where(EditPlan.video_id == video_id).order_by(EditPlan.created_at.desc())
    )
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="No edit plan generated yet for this video")

    segs_result = await db.execute(
        select(EditPlanSegment)
        .where(EditPlanSegment.edit_plan_id == plan.id)
        .order_by(EditPlanSegment.order)
    )
    segments = segs_result.scalars().all()

    return EditPlanSchema(
        id=plan.id,
        video_id=plan.video_id,
        status=plan.status,
        llm_generated=plan.llm_generated,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
        segments=list(segments),
    )


@router.patch("/edit-plans/{plan_id}", response_model=EditPlanSchema)
@limiter.limit(settings.rate_limit_default)
async def patch_edit_plan(
    request,
    plan_id: uuid.UUID,
    body: EditPlanPatchRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    plan = await _get_plan_for_user(plan_id, user_id, db)

    if plan.status != EditPlanStatus.DRAFT:
        raise HTTPException(
            status_code=409,
            detail=f"Plan cannot be edited in status '{plan.status}' — only DRAFT plans are editable",
        )

    segment_ids = {u.id for u in body.segments}
    segs_result = await db.execute(
        select(EditPlanSegment).where(
            EditPlanSegment.edit_plan_id == plan.id,
            EditPlanSegment.id.in_(segment_ids),
        )
    )
    seg_map = {s.id: s for s in segs_result.scalars().all()}

    for update in body.segments:
        seg = seg_map.get(update.id)
        if not seg:
            raise HTTPException(status_code=404, detail=f"Segment {update.id} not found in this plan")
        if update.action is not None:
            seg.action = update.action
        if update.caption is not None:
            seg.caption = update.caption
        if update.order is not None:
            seg.order = update.order

    await db.flush()
    logger.info(f"Edit plan {plan_id} patched by {user_id}: {len(body.segments)} segment(s) updated")

    segs_all_result = await db.execute(
        select(EditPlanSegment)
        .where(EditPlanSegment.edit_plan_id == plan.id)
        .order_by(EditPlanSegment.order)
    )
    return EditPlanSchema(
        id=plan.id,
        video_id=plan.video_id,
        status=plan.status,
        llm_generated=plan.llm_generated,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
        segments=list(segs_all_result.scalars().all()),
    )


@router.post(
    "/edit-plans/{plan_id}/approve",
    response_model=RenderJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
@limiter.limit(settings.rate_limit_agent)
async def approve_edit_plan(
    request,
    plan_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    plan = await _get_plan_for_user(plan_id, user_id, db)

    if plan.status != EditPlanStatus.DRAFT:
        raise HTTPException(
            status_code=409,
            detail=f"Plan is already '{plan.status}' — only DRAFT plans can be approved",
        )

    plan.status = EditPlanStatus.APPROVED
    await db.flush()

    job = RenderJob(
        edit_plan_id=plan.id,
        video_id=plan.video_id,
        status=RenderStatus.PENDING,
    )
    db.add(job)
    await db.flush()

    from app.tasks.render_pipeline import render_video
    render_video.delay(str(job.id))

    logger.info(f"Edit plan {plan_id} approved by {user_id}; render job {job.id} queued")
    return RenderJobResponse(
        id=job.id,
        edit_plan_id=job.edit_plan_id,
        video_id=job.video_id,
        status=job.status,
        output_url=None,
        error_message=None,
        started_at=job.started_at,
        completed_at=job.completed_at,
        created_at=job.created_at,
    )


@router.get("/render-jobs/{job_id}", response_model=RenderJobResponse)
@limiter.limit(settings.rate_limit_default)
async def get_render_job(
    request,
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    result = await db.execute(
        select(RenderJob)
        .join(Video, RenderJob.video_id == Video.id)
        .where(RenderJob.id == job_id, Video.user_id == user_id)
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Render job not found")

    output_url = None
    if job.status == RenderStatus.COMPLETE and job.output_key:
        output_url = storage_service.presign_url(job.output_key)

    return RenderJobResponse(
        id=job.id,
        edit_plan_id=job.edit_plan_id,
        video_id=job.video_id,
        status=job.status,
        output_url=output_url,
        error_message=job.error_message,
        started_at=job.started_at,
        completed_at=job.completed_at,
        created_at=job.created_at,
    )
