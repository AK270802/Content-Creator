import os
import uuid
import tempfile
from loguru import logger

from app.worker import celery_app
from app.models.enums import VideoStatus


def _get_db_sync():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.config import settings
    sync_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    engine = create_engine(sync_url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine)
    return Session()


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30, name="tasks.process_video")
def process_video(self, video_id: str) -> dict:
    """
    Full ingest pipeline:
      download → scene detect → vision tag → audio extract
      → transcribe → embed → edit plan → mark READY
    """
    logger.info(f"Pipeline start: video={video_id}")
    session = _get_db_sync()

    try:
        from app.models.video import Video, Scene, Segment
        from app.services.storage import storage_service
        from app.services.scene import scene_service
        from app.services.vision import vision_service
        from app.services.transcription import transcription_service
        from app.services.embedding import embedding_service

        video = session.query(Video).filter(Video.id == uuid.UUID(video_id)).first()
        if not video:
            logger.error(f"Video {video_id} not found in DB")
            return {"status": "not_found"}

        video.status = VideoStatus.PROCESSING
        session.commit()

        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = os.path.join(tmpdir, video.filename)
            audio_path = os.path.join(tmpdir, "audio.wav")

            # ── Step 1: Download ─────────────────────────────────────────────
            logger.info(f"Downloading {video.s3_key}")
            storage_service.download_to_file(video.s3_key, video_path)

            # ── Step 2: Scene detection ──────────────────────────────────────
            scenes_data = scene_service.detect_scenes(video_path)
            scene_objs = []
            for sd in scenes_data:
                sc = Scene(
                    video_id=uuid.UUID(video_id),
                    scene_number=sd["scene_number"],
                    start_time=sd["start_time"],
                    end_time=sd["end_time"],
                )
                session.add(sc)
            session.flush()
            session.refresh(video)
            scene_objs = (
                session.query(Scene)
                .filter(Scene.video_id == uuid.UUID(video_id))
                .all()
            )
            session.commit()

            # ── Step 3: Vision tagging (non-fatal per scene) ─────────────────
            for sc in scene_objs:
                tags = vision_service.tag_scene(
                    video_path, sc.start_time, sc.end_time
                )
                sc.description = tags["description"]
                sc.visual_tags = tags["visual_tags"]
                sc.quality_flags = tags["quality_flags"]
            session.commit()

            # ── Step 4: Extract audio ────────────────────────────────────────
            import ffmpeg as _ffmpeg
            (
                _ffmpeg
                .input(video_path)
                .output(audio_path, vn=None, acodec="pcm_s16le", ar=16000, ac=1)
                .overwrite_output()
                .run(quiet=True)
            )

            # ── Step 5: Transcription ────────────────────────────────────────
            transcript = transcription_service.transcribe(audio_path)
            segment_objs = []
            for seg_data in transcript:
                seg = Segment(
                    video_id=uuid.UUID(video_id),
                    start_time=seg_data["start"],
                    end_time=seg_data["end"],
                    text=seg_data["text"],
                )
                session.add(seg)
                segment_objs.append(seg_data)
            session.commit()

            # ── Step 6: Embed segments ───────────────────────────────────────
            if transcript:
                embedding_service.upsert_segments(video_id, transcript)

            # ── Step 7: Edit planning (added in piece 2, stubbed here) ───────
            _run_edit_planning(session, video_id, scene_objs, segment_objs)

        # ── Done ─────────────────────────────────────────────────────────────
        video.status = VideoStatus.READY
        session.commit()
        logger.info(f"Pipeline complete: video={video_id}")
        return {"status": "ready", "video_id": video_id}

    except Exception as exc:
        logger.error(f"Pipeline failed video={video_id}: {exc}")
        try:
            from app.models.video import Video
            v = session.query(Video).filter(Video.id == uuid.UUID(video_id)).first()
            if v:
                v.status = VideoStatus.FAILED
                v.error_message = str(exc)[:2048]
                session.commit()
        except Exception:
            pass
        raise self.retry(exc=exc)
    finally:
        session.close()


def _run_edit_planning(session, video_id: str, scene_objs, segment_data: list[dict]) -> None:
    """Generate an EditPlan after all scenes are tagged and transcript is embedded."""
    from app.models.edit_plan import EditPlan, EditPlanSegment
    from app.services.planning import planning_service, PlanningError
    from app.models.video import Segment

    # Re-query fresh Segment ORM objects (we only have raw dicts here)
    seg_objs = session.query(Segment).filter(
        Segment.video_id == video_id if not isinstance(video_id, str)
        else Segment.video_id == __import__("uuid").UUID(video_id)
    ).all()

    try:
        plan_data = planning_service.generate_plan(str(video_id), scene_objs, seg_objs)
        import uuid as _uuid
        edit_plan = EditPlan(
            video_id=_uuid.UUID(video_id) if isinstance(video_id, str) else video_id,
            llm_generated=bool(planning_service._last_llm_used),
        )
        session.add(edit_plan)
        session.flush()

        for seg in plan_data.segments:
            ps = EditPlanSegment(
                edit_plan_id=edit_plan.id,
                scene_id=seg.scene_id,
                action=seg.action,
                reason=seg.reason,
                caption=seg.caption,
                order=seg.order,
            )
            session.add(ps)
        session.commit()
        logger.info(f"EditPlan {edit_plan.id} created for video {video_id}")
    except PlanningError as e:
        logger.error(f"Planning failed for video {video_id}: {e} — video still marked READY")
    except Exception as e:
        logger.error(f"Unexpected planning error for video {video_id}: {e}")

