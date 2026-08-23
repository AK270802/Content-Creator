import uuid
import os
import tempfile
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status, Query
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
from app.schemas.video import (
    VideoCreateResponse, VideoStatusResponse,
    SceneResponse, SegmentResponse, TranscriptResponse,
)
from app.services.storage import storage_service
from app.services import analytics

limiter = Limiter(key_func=get_remote_address)
router = APIRouter(prefix="/videos", tags=["videos"])

ALLOWED_CONTENT_TYPES = {
    "video/mp4", "video/quicktime", "video/x-msvideo",
    "video/x-matroska", "video/webm", "video/mpeg",
}
MAX_FILE_SIZE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB


@router.post("/upload", status_code=status.HTTP_202_ACCEPTED, response_model=VideoCreateResponse)
@limiter.limit(settings.rate_limit_upload)
async def upload_video(
    request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type: {file.content_type}",
        )

    safe_filename = os.path.basename(file.filename or "upload.mp4")
    video_id = uuid.uuid4()
    s3_key = f"videos/{user_id}/{video_id}/{safe_filename}"

    # Stream upload to MinIO via temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(safe_filename)[1]) as tmp:
        total = 0
        chunk_size = 1024 * 1024  # 1 MB
        while chunk := await file.read(chunk_size):
            if total + len(chunk) > MAX_FILE_SIZE_BYTES:
                os.unlink(tmp.name)
                raise HTTPException(status_code=413, detail="File too large (max 2 GB)")
            tmp.write(chunk)
            total += len(chunk)
        tmp_path = tmp.name

    try:
        storage_service.ensure_bucket()
        storage_service.upload_file(s3_key, tmp_path, content_type=file.content_type)
    finally:
        os.unlink(tmp_path)

    video = Video(
        id=video_id,
        user_id=user_id,
        filename=safe_filename,
        original_filename=file.filename or safe_filename,
        status=VideoStatus.PENDING,
        s3_key=s3_key,
        file_size_bytes=total,
    )
    db.add(video)
    await db.flush()

    from app.tasks.video_pipeline import process_video
    process_video.delay(str(video_id))

    analytics.capture(user_id, "video_uploaded", {
        "video_id": str(video_id),
        "file_size_bytes": total,
        "content_type": file.content_type,
    })
    logger.info(f"Video {video_id} uploaded by {user_id}, task queued")
    return VideoCreateResponse(video_id=video_id, status=VideoStatus.PENDING)


@router.get("/{video_id}/status", response_model=VideoStatusResponse)
@limiter.limit(settings.rate_limit_default)
async def get_video_status(
    request,
    video_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    result = await db.execute(select(Video).where(Video.id == video_id, Video.user_id == user_id))
    video = result.scalar_one_or_none()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    if video.status == VideoStatus.READY:
        analytics.capture(user_id, "video_upload_completed", {"video_id": str(video_id)})
    return video


@router.get("/{video_id}/scenes", response_model=list[SceneResponse])
@limiter.limit(settings.rate_limit_default)
async def get_scenes(
    request,
    video_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    result = await db.execute(select(Video).where(Video.id == video_id, Video.user_id == user_id))
    video = result.scalar_one_or_none()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    if video.status != VideoStatus.READY:
        raise HTTPException(status_code=409, detail=f"Video not ready (status: {video.status})")

    scenes_result = await db.execute(
        select(Scene).where(Scene.video_id == video_id).order_by(Scene.scene_number)
    )
    scenes = scenes_result.scalars().all()

    output = []
    for sc in scenes:
        thumbnail_url = None
        if sc.thumbnail_key:
            thumbnail_url = storage_service.presign_url(sc.thumbnail_key)
        output.append(SceneResponse(
            id=sc.id,
            scene_number=sc.scene_number,
            start_time=sc.start_time,
            end_time=sc.end_time,
            thumbnail_url=thumbnail_url,
        ))
    return output


@router.get("/{video_id}/transcript", response_model=TranscriptResponse)
@limiter.limit(settings.rate_limit_default)
async def get_transcript(
    request,
    video_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    result = await db.execute(select(Video).where(Video.id == video_id, Video.user_id == user_id))
    video = result.scalar_one_or_none()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    if video.status != VideoStatus.READY:
        raise HTTPException(status_code=409, detail=f"Video not ready (status: {video.status})")

    offset = (page - 1) * page_size
    segs_result = await db.execute(
        select(Segment)
        .where(Segment.video_id == video_id)
        .order_by(Segment.start_time)
        .offset(offset)
        .limit(page_size)
    )
    segments = segs_result.scalars().all()

    count_result = await db.execute(
        select(Segment).where(Segment.video_id == video_id)
    )
    total = len(count_result.scalars().all())

    return TranscriptResponse(
        video_id=video_id,
        total=total,
        page=page,
        page_size=page_size,
        segments=[SegmentResponse.model_validate(s) for s in segments],
    )
