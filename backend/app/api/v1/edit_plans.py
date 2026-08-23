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
from app.models.video import Video, Scene, Segment
from app.models.edit_plan import EditPlan, EditPlanSegment, EditPlanStatus
from app.models.render_job import RenderJob, RenderStatus
from app.schemas.edit_plan import EditPlanSchema, EditPlanPatchRequest, ReviseRequest
from app.schemas.render_job import RenderJobResponse
from app.services.storage import storage_service
from app.services import analytics

limiter = Limiter(key_func=get_remote_address)
router = APIRouter(tags=["edit-plans"])


# ── helpers ────────────────────────────────────────────────────────────────────

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


async def _load_segments(plan_id: uuid.UUID, db: AsyncSession) -> list[EditPlanSegment]:
    result = await db.execute(
        select(EditPlanSegment)
        .where(EditPlanSegment.edit_plan_id == plan_id)
        .order_by(EditPlanSegment.order)
    )
    return list(result.scalars().all())


def _plan_to_schema(plan: EditPlan, segments: list[EditPlanSegment]) -> EditPlanSchema:
    return EditPlanSchema(
        id=plan.id,
        video_id=plan.video_id,
        status=plan.status,
        llm_generated=plan.llm_generated,
        version=plan.version,
        parent_version_id=plan.parent_version_id,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
        segments=list(segments),
    )


# ── list all versions ──────────────────────────────────────────────────────────

@router.get("/videos/{video_id}/edit-plans", response_model=list[EditPlanSchema])
@limiter.limit(settings.rate_limit_default)
async def list_edit_plans(
    request,
    video_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    await _get_video_for_user(video_id, user_id, db)
    result = await db.execute(
        select(EditPlan).where(EditPlan.video_id == video_id).order_by(EditPlan.version)
    )
    plans = result.scalars().all()
    out = []
    for plan in plans:
        segs = await _load_segments(plan.id, db)
        out.append(_plan_to_schema(plan, segs))
    return out


# ── latest version ─────────────────────────────────────────────────────────────

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
        select(EditPlan).where(EditPlan.video_id == video_id).order_by(EditPlan.version.desc())
    )
    plan = result.scalars().first()
    if not plan:
        raise HTTPException(status_code=404, detail="No edit plan generated yet for this video")
    segs = await _load_segments(plan.id, db)
    analytics.capture(user_id, "edit_plan_generated", {
        "video_id": str(video_id), "plan_id": str(plan.id), "llm_generated": plan.llm_generated,
    })
    return _plan_to_schema(plan, segs)


# ── single version ─────────────────────────────────────────────────────────────

@router.get("/edit-plans/{plan_id}", response_model=EditPlanSchema)
@limiter.limit(settings.rate_limit_default)
async def get_edit_plan_by_id(
    request,
    plan_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    plan = await _get_plan_for_user(plan_id, user_id, db)
    segs = await _load_segments(plan.id, db)
    return _plan_to_schema(plan, segs)


# ── patch ──────────────────────────────────────────────────────────────────────

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
        if update.text_overlay is not None:
            seg.text_overlay = update.text_overlay
        if update.start_ts is not None:
            seg.start_ts = update.start_ts
        if update.end_ts is not None:
            seg.end_ts = update.end_ts
        if update.order is not None:
            seg.order = update.order

    await db.flush()
    analytics.capture(user_id, "edit_plan_edited", {
        "plan_id": str(plan_id), "segments_updated": len(body.segments),
    })
    logger.info(f"Edit plan {plan_id} patched: {len(body.segments)} segment(s)")
    segs = await _load_segments(plan.id, db)
    return _plan_to_schema(plan, segs)


# ── approve ────────────────────────────────────────────────────────────────────

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

    job = RenderJob(edit_plan_id=plan.id, video_id=plan.video_id, status=RenderStatus.PENDING)
    db.add(job)
    await db.flush()

    from app.tasks.render_pipeline import render_video
    render_video.delay(str(job.id))

    analytics.capture(user_id, "edit_plan_approved", {"plan_id": str(plan_id)})
    analytics.capture(user_id, "render_started", {
        "render_job_id": str(job.id), "video_id": str(plan.video_id),
    })
    logger.info(f"Edit plan {plan_id} approved; render job {job.id} queued")
    return RenderJobResponse(
        id=job.id, edit_plan_id=job.edit_plan_id, video_id=job.video_id,
        status=job.status, output_url=None, error_message=None,
        started_at=job.started_at, completed_at=job.completed_at, created_at=job.created_at,
    )


# ── revise ─────────────────────────────────────────────────────────────────────

@router.post(
    "/videos/{video_id}/edit-plans/{plan_id}/revise",
    response_model=EditPlanSchema,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit(settings.rate_limit_agent)
async def revise_edit_plan(
    request,
    video_id: uuid.UUID,
    plan_id: uuid.UUID,
    body: ReviseRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    await _get_video_for_user(video_id, user_id, db)
    current_plan = await _get_plan_for_user(plan_id, user_id, db)
    current_segs = await _load_segments(plan_id, db)

    # Load context for the revision LLM
    scenes_result = await db.execute(
        select(Scene).where(Scene.video_id == video_id).order_by(Scene.scene_number)
    )
    scenes = list(scenes_result.scalars().all())

    from app.models.timeline import TimelineEvent
    te_result = await db.execute(
        select(TimelineEvent).where(TimelineEvent.video_id == video_id).order_by(TimelineEvent.start_ts)
    )
    timeline_events = list(te_result.scalars().all())

    transcript_result = await db.execute(
        select(Segment).where(Segment.video_id == video_id).order_by(Segment.start_time).limit(100)
    )
    transcript = list(transcript_result.scalars().all())

    from app.services.revision import revision_service
    from app.services.planning import PlanningError

    try:
        new_plan_data = revision_service.revise(
            video_id=str(video_id),
            current_plan_segments=current_segs,
            scenes=scenes,
            timeline_events=timeline_events,
            transcript_segments=transcript,
            instruction=body.instruction,
            current_version=current_plan.version,
        )
    except PlanningError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    # Determine next version number
    max_ver_result = await db.execute(
        select(EditPlan.version).where(EditPlan.video_id == video_id).order_by(EditPlan.version.desc())
    )
    max_ver = max_ver_result.scalars().first() or 0

    new_plan = EditPlan(
        video_id=video_id,
        status=EditPlanStatus.DRAFT,
        llm_generated=True,
        version=max_ver + 1,
        parent_version_id=current_plan.id,
    )
    db.add(new_plan)
    await db.flush()

    for seg in new_plan_data.segments:
        ps = EditPlanSegment(
            edit_plan_id=new_plan.id,
            scene_id=seg.scene_id,
            action=seg.action,
            reason=seg.reason,
            caption=seg.caption,
            order=seg.order,
        )
        db.add(ps)
    await db.flush()

    analytics.capture(user_id, "edit_plan_revised", {
        "plan_id": str(new_plan.id),
        "parent_plan_id": str(current_plan.id),
        "plan_version": new_plan.version,
        "instruction_length": len(body.instruction),
    })
    logger.info(f"Revised plan v{new_plan.version} created for video {video_id}")

    new_segs = await _load_segments(new_plan.id, db)
    return _plan_to_schema(new_plan, new_segs)


# ── render job poll ────────────────────────────────────────────────────────────

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
        analytics.capture(user_id, "render_completed", {"render_job_id": str(job_id)})
        analytics.capture(user_id, "export_downloaded", {
            "render_job_id": str(job_id), "output_key": job.output_key,
        })
    elif job.status == RenderStatus.FAILED:
        analytics.capture(user_id, "render_failed", {
            "render_job_id": str(job_id), "error": job.error_message or "",
        })

    return RenderJobResponse(
        id=job.id, edit_plan_id=job.edit_plan_id, video_id=job.video_id,
        status=job.status, output_url=output_url, error_message=job.error_message,
        started_at=job.started_at, completed_at=job.completed_at, created_at=job.created_at,
    )
