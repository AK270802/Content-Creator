import uuid
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Request, status, Body
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
from app.schemas.edit_plan import EditPlanSchema, EditPlanSegmentSchema, EditPlanPatchRequest, EditPlanSegmentCreate, ReviseRequest
from app.schemas.render_job import RenderJobResponse, ExportOptions
from app.services.storage import storage_service
from app.services import analytics

limiter = Limiter(key_func=get_remote_address)
router = APIRouter(tags=["edit-plans"])


# ── revision daily cap ─────────────────────────────────────────────────────────

async def _check_revision_cap(user_id: str, video_id: str) -> None:
    try:
        import redis.asyncio as aioredis
        cap_key = f"revise_cap:{user_id}:{date.today().isoformat()}"
        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            daily_count = await client.incr(cap_key)
            if daily_count == 1:
                await client.expire(cap_key, 86400)
            if daily_count > settings.revision_daily_cap:
                analytics.capture(user_id, "revision_cap_hit", {
                    "video_id": video_id,
                    "daily_cap": settings.revision_daily_cap,
                    "daily_count": daily_count,
                })
                raise HTTPException(
                    status_code=429,
                    detail=f"Daily revision limit of {settings.revision_daily_cap} reached. Try again tomorrow.",
                )
        finally:
            await client.aclose()
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(f"Revision cap check failed (failing open): {exc}")


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


async def _load_scene_map(video_id: uuid.UUID, db: AsyncSession) -> dict[uuid.UUID, Scene]:
    result = await db.execute(select(Scene).where(Scene.video_id == video_id))
    return {s.id: s for s in result.scalars().all()}


def _plan_to_schema(
    plan: EditPlan,
    segments: list[EditPlanSegment],
    scene_map: dict[uuid.UUID, Scene] | None = None,
) -> EditPlanSchema:
    seg_schemas: list[EditPlanSegmentSchema] = []
    for seg in segments:
        scene = scene_map.get(seg.scene_id) if scene_map else None
        start = seg.start_ts if seg.start_ts is not None else (scene.start_time if scene else 0.0)
        end = seg.end_ts if seg.end_ts is not None else (scene.end_time if scene else 0.0)
        thumbnail_url = None
        if scene and scene.thumbnail_key:
            try:
                thumbnail_url = storage_service.presign_url(scene.thumbnail_key)
            except Exception:
                pass
        seg_schemas.append(EditPlanSegmentSchema(
            id=seg.id,
            scene_id=seg.scene_id,
            action=seg.action,
            reason=seg.reason,
            caption=seg.caption,
            text_overlay=seg.text_overlay,
            start_ts=seg.start_ts,
            end_ts=seg.end_ts,
            order=seg.order,
            brightness=seg.brightness,
            contrast=seg.contrast,
            saturation=seg.saturation,
            fade_in=seg.fade_in,
            fade_out=seg.fade_out,
            effect=seg.effect,
            start=start,
            end=end,
            thumbnail_url=thumbnail_url,
        ))
    return EditPlanSchema(
        id=plan.id,
        video_id=plan.video_id,
        status=plan.status,
        llm_generated=plan.llm_generated,
        version=plan.version,
        parent_version_id=plan.parent_version_id,
        parent_plan_id=plan.parent_version_id,
        revision_instruction=plan.revision_instruction,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
        segments=seg_schemas,
    )


# ── list all versions ──────────────────────────────────────────────────────────

@router.get("/videos/{video_id}/edit-plans", response_model=list[EditPlanSchema])
@limiter.limit(settings.rate_limit_default)
async def list_edit_plans(
    request: Request,
    video_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    await _get_video_for_user(video_id, user_id, db)
    scene_map = await _load_scene_map(video_id, db)
    result = await db.execute(
        select(EditPlan).where(EditPlan.video_id == video_id).order_by(EditPlan.version)
    )
    plans = result.scalars().all()
    out = []
    for plan in plans:
        segs = await _load_segments(plan.id, db)
        out.append(_plan_to_schema(plan, segs, scene_map))
    return out


# ── latest version ─────────────────────────────────────────────────────────────

@router.get("/videos/{video_id}/edit-plan", response_model=EditPlanSchema)
@limiter.limit(settings.rate_limit_default)
async def get_edit_plan(
    request: Request,
    video_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    await _get_video_for_user(video_id, user_id, db)
    scene_map = await _load_scene_map(video_id, db)
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
    return _plan_to_schema(plan, segs, scene_map)


# ── single version ─────────────────────────────────────────────────────────────

@router.get("/edit-plans/{plan_id}", response_model=EditPlanSchema)
@limiter.limit(settings.rate_limit_default)
async def get_edit_plan_by_id(
    request: Request,
    plan_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    plan = await _get_plan_for_user(plan_id, user_id, db)
    scene_map = await _load_scene_map(plan.video_id, db)
    segs = await _load_segments(plan.id, db)
    return _plan_to_schema(plan, segs, scene_map)


# ── patch ──────────────────────────────────────────────────────────────────────

@router.patch("/edit-plans/{plan_id}", response_model=EditPlanSchema)
@limiter.limit(settings.rate_limit_default)
async def patch_edit_plan(
    request: Request,
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

    # Delete segments
    if body.delete_ids:
        del_result = await db.execute(
            select(EditPlanSegment).where(
                EditPlanSegment.edit_plan_id == plan.id,
                EditPlanSegment.id.in_(body.delete_ids),
            )
        )
        for seg in del_result.scalars().all():
            await db.delete(seg)

    # Update existing segments
    if body.segments:
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
            if update.brightness is not None:
                seg.brightness = update.brightness
            if update.contrast is not None:
                seg.contrast = update.contrast
            if update.saturation is not None:
                seg.saturation = update.saturation
            if update.fade_in is not None:
                seg.fade_in = update.fade_in
            if update.fade_out is not None:
                seg.fade_out = update.fade_out
            if update.effect is not None:
                seg.effect = update.effect

    # Create new segments (e.g. from splits)
    for new_seg in body.create:
        ps = EditPlanSegment(
            edit_plan_id=plan.id,
            scene_id=new_seg.scene_id,
            action=new_seg.action,
            caption=new_seg.caption,
            text_overlay=new_seg.text_overlay,
            start_ts=new_seg.start_ts,
            end_ts=new_seg.end_ts,
            order=new_seg.order,
            brightness=new_seg.brightness,
            contrast=new_seg.contrast,
            saturation=new_seg.saturation,
            fade_in=new_seg.fade_in,
            fade_out=new_seg.fade_out,
            effect=new_seg.effect,
        )
        db.add(ps)

    await db.flush()
    analytics.capture(user_id, "edit_plan_edited", {
        "plan_id": str(plan_id),
        "segments_updated": len(body.segments),
        "segments_created": len(body.create),
        "segments_deleted": len(body.delete_ids),
    })
    logger.info(f"Edit plan {plan_id} patched: {len(body.segments)} segment(s)")
    scene_map = await _load_scene_map(plan.video_id, db)
    segs = await _load_segments(plan.id, db)
    return _plan_to_schema(plan, segs, scene_map)


# ── approve ────────────────────────────────────────────────────────────────────

@router.post(
    "/edit-plans/{plan_id}/approve",
    response_model=RenderJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
@limiter.limit(settings.rate_limit_agent)
async def approve_edit_plan(
    request: Request,
    plan_id: uuid.UUID,
    options: ExportOptions = Body(default_factory=ExportOptions),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    plan = await _get_plan_for_user(plan_id, user_id, db)
    if plan.status != EditPlanStatus.DRAFT:
        raise HTTPException(
            status_code=409,
            detail=f"Plan is already '{plan.status}' — only DRAFT plans can be approved",
        )

    opts = options
    # Validate preset exists
    if opts.preset_id and opts.preset_id != "source":
        from app.services.export_presets import get_preset
        try:
            preset = get_preset(opts.preset_id)
            aspect = opts.aspect_ratio or preset.aspect_ratio
        except KeyError:
            raise HTTPException(status_code=400, detail=f"Unknown export preset: {opts.preset_id}")
    else:
        aspect = opts.aspect_ratio

    if opts.brand_kit_id:
        from app.models.brand_kit import BrandKit
        kit_result = await db.execute(
            select(BrandKit).where(
                BrandKit.id == opts.brand_kit_id,
                BrandKit.user_id == user_id,
            )
        )
        if not kit_result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Brand kit not found")

    plan.status = EditPlanStatus.APPROVED
    await db.flush()

    job = RenderJob(
        edit_plan_id=plan.id,
        video_id=plan.video_id,
        status=RenderStatus.PENDING,
        preset_id=opts.preset_id,
        aspect_ratio=aspect,
        burn_captions=opts.burn_captions,
        brand_kit_id=opts.brand_kit_id,
        options={"prefer_faces": opts.prefer_faces, "resolution": opts.resolution},
    )
    db.add(job)
    await db.flush()

    from app.tasks.render_pipeline import render_video
    render_video.delay(str(job.id))

    analytics.capture(user_id, "edit_plan_approved", {
        "plan_id": str(plan_id), "preset_id": opts.preset_id,
    })
    analytics.capture(user_id, "render_started", {
        "render_job_id": str(job.id), "video_id": str(plan.video_id),
        "preset_id": opts.preset_id,
    })
    logger.info(f"Edit plan {plan_id} approved; render job {job.id} queued preset={opts.preset_id}")
    return RenderJobResponse(
        id=job.id, edit_plan_id=job.edit_plan_id, video_id=job.video_id,
        status=job.status, output_url=None, error_message=None,
        preset_id=job.preset_id, aspect_ratio=job.aspect_ratio,
        burn_captions=job.burn_captions,
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
    request: Request,
    video_id: uuid.UUID,
    plan_id: uuid.UUID,
    body: ReviseRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    await _get_video_for_user(video_id, user_id, db)
    await _check_revision_cap(user_id, str(video_id))
    current_plan = await _get_plan_for_user(plan_id, user_id, db)
    current_segs = await _load_segments(plan_id, db)

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
        revision_instruction=body.instruction,
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

    scene_map = {s.id: s for s in scenes}
    new_segs = await _load_segments(new_plan.id, db)
    return _plan_to_schema(new_plan, new_segs, scene_map)


# ── render job poll ────────────────────────────────────────────────────────────

@router.get("/render-jobs/{job_id}", response_model=RenderJobResponse)
@limiter.limit(settings.rate_limit_default)
async def get_render_job(
    request: Request,
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
        preset_id=getattr(job, "preset_id", None),
        aspect_ratio=getattr(job, "aspect_ratio", None),
        burn_captions=getattr(job, "burn_captions", True),
        started_at=job.started_at, completed_at=job.completed_at, created_at=job.created_at,
    )


@router.get("/videos/{video_id}/render-jobs", response_model=list[RenderJobResponse])
@limiter.limit(settings.rate_limit_default)
async def list_render_jobs(
    request: Request,
    video_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    """List render jobs for a video (newest first)."""
    await _get_video_for_user(video_id, user_id, db)
    result = await db.execute(
        select(RenderJob)
        .where(RenderJob.video_id == video_id)
        .order_by(RenderJob.created_at.desc())
        .limit(20)
    )
    jobs = result.scalars().all()
    out: list[RenderJobResponse] = []
    for job in jobs:
        output_url = None
        if job.status == RenderStatus.COMPLETE and job.output_key:
            output_url = storage_service.presign_url(job.output_key)
        out.append(RenderJobResponse(
            id=job.id, edit_plan_id=job.edit_plan_id, video_id=job.video_id,
            status=job.status, output_url=output_url, error_message=job.error_message,
            preset_id=job.preset_id, aspect_ratio=job.aspect_ratio,
            burn_captions=job.burn_captions,
            started_at=job.started_at, completed_at=job.completed_at, created_at=job.created_at,
        ))
    return out
