import os
import uuid
import tempfile
from datetime import datetime, timezone
from loguru import logger

from app.worker import celery_app
from app.models.render_job import RenderStatus


def _get_db_sync():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.config import settings
    sync_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    engine = create_engine(sync_url, pool_pre_ping=True)
    return sessionmaker(bind=engine)()


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="tasks.render_video",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
)
def render_video(self, render_job_id: str) -> dict:
    """
    Render task: fetches approved EditPlan → extracts kept clips
    → concat + crossfade + captions → uploads to MinIO → updates RenderJob.
    Retries 3 times with exponential back-off; marks failed on exhaustion.
    """
    logger.info(f"Render start: job={render_job_id}")
    session = _get_db_sync()

    try:
        from app.models.render_job import RenderJob
        from app.models.edit_plan import EditPlan, EditPlanSegment, EditSegmentAction, EditPlanStatus
        from app.models.video import Video, Scene
        from app.services.storage import storage_service
        from app.services.render import render_service

        job = session.query(RenderJob).filter(
            RenderJob.id == uuid.UUID(render_job_id)
        ).first()
        if not job:
            logger.error(f"RenderJob {render_job_id} not found")
            return {"status": "not_found"}

        job.status = RenderStatus.PROCESSING
        job.started_at = datetime.now(timezone.utc)
        session.commit()

        # Load edit plan + segments + scenes
        plan = session.query(EditPlan).filter(EditPlan.id == job.edit_plan_id).first()
        if not plan:
            raise ValueError(f"EditPlan {job.edit_plan_id} not found")

        video = session.query(Video).filter(Video.id == job.video_id).first()
        if not video:
            raise ValueError(f"Video {job.video_id} not found")

        kept_segs = (
            session.query(EditPlanSegment)
            .filter(
                EditPlanSegment.edit_plan_id == plan.id,
                EditPlanSegment.action == EditSegmentAction.KEEP,
            )
            .order_by(EditPlanSegment.order)
            .all()
        )

        if not kept_segs:
            raise ValueError("Edit plan has no kept segments to render")

        # Build segment list with scene timing
        segments_data = []
        for ps in kept_segs:
            scene = session.query(Scene).filter(Scene.id == ps.scene_id).first()
            if scene:
                # Honour sub-scene trim points; clamp to scene bounds for safety
                start = ps.start_ts if ps.start_ts is not None else scene.start_time
                end = ps.end_ts if ps.end_ts is not None else scene.end_time
                segments_data.append({
                    "start_time": max(scene.start_time, min(start, scene.end_time)),
                    "end_time": max(scene.start_time, min(end, scene.end_time)),
                    "caption": ps.caption or "",
                    "text_overlay": ps.text_overlay or "",
                    "brightness": ps.brightness if ps.brightness is not None else 1.0,
                    "contrast": ps.contrast if ps.contrast is not None else 1.0,
                    "saturation": ps.saturation if ps.saturation is not None else 1.0,
                    "fade_in": ps.fade_in or 0.0,
                    "fade_out": ps.fade_out or 0.0,
                    "effect": ps.effect or "none",
                })

        with tempfile.TemporaryDirectory() as tmpdir:
            # Download source video
            video_path = os.path.join(tmpdir, video.filename)
            storage_service.download_to_file(video.s3_key, video_path)

            output_path = os.path.join(tmpdir, f"rendered_{render_job_id}.mp4")

            logo_path = None
            if job.brand_kit_id:
                from app.models.brand_kit import BrandKit
                kit = (
                    session.query(BrandKit)
                    .filter(
                        BrandKit.id == job.brand_kit_id,
                        BrandKit.user_id == video.user_id,
                    )
                    .first()
                )
                if kit and kit.logo_key:
                    logo_path = os.path.join(tmpdir, "logo.png")
                    try:
                        storage_service.download_to_file(kit.logo_key, logo_path)
                    except Exception as logo_exc:
                        logger.warning(f"Brand logo download failed: {logo_exc}")
                        logo_path = None
                elif job.brand_kit_id:
                    logger.warning(
                        f"Brand kit {job.brand_kit_id} skipped — not owned by video user"
                    )

            opts = job.options or {}
            render_service.render(
                video_path,
                segments_data,
                output_path,
                preset_id=job.preset_id,
                aspect_ratio=job.aspect_ratio,
                burn_captions=bool(job.burn_captions) if job.burn_captions is not None else True,
                prefer_faces=bool(opts.get("prefer_faces", True)),
                logo_path=logo_path,
            )

            # Upload rendered file
            from app.config import settings
            output_key = f"{settings.render_output_prefix}/{job.video_id}/{render_job_id}.mp4"
            storage_service.upload_file(output_key, output_path, content_type="video/mp4")

        job.status = RenderStatus.COMPLETE
        job.output_key = output_key
        job.completed_at = datetime.now(timezone.utc)
        plan.status = EditPlanStatus.COMPLETE
        session.commit()

        logger.info(f"Render complete: job={render_job_id} key={output_key}")
        return {"status": "complete", "output_key": output_key}

    except Exception as exc:
        logger.error(f"Render failed job={render_job_id}: {exc}")
        try:
            from app.models.render_job import RenderJob
            from app.models.edit_plan import EditPlan, EditPlanStatus
            j = session.query(RenderJob).filter(
                RenderJob.id == uuid.UUID(render_job_id)
            ).first()
            if j:
                j.status = RenderStatus.FAILED
                j.error_message = str(exc)[:2048]
                j.completed_at = datetime.now(timezone.utc)
                plan = session.query(EditPlan).filter(EditPlan.id == j.edit_plan_id).first()
                if plan:
                    from app.models.edit_plan import EditPlanStatus
                    plan.status = EditPlanStatus.FAILED
                session.commit()
        except Exception:
            pass

        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
        return {"status": "failed", "error": str(exc)}
    finally:
        session.close()
