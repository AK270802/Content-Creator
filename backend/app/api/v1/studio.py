"""Studio APIs: export presets, thumbnails, brand kits, localization stubs."""

from __future__ import annotations

import os
import tempfile
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from slowapi import Limiter
from slowapi.util import get_remote_address
from loguru import logger

from app.config import settings
from app.db.session import get_db
from app.dependencies import get_current_user_id
from app.models.video import Video, Scene
from app.models.brand_kit import BrandKit, GeneratedThumbnail
from app.services.storage import storage_service
from app.services.export_presets import list_presets, get_preset
from app.services import analytics

limiter = Limiter(key_func=get_remote_address)
router = APIRouter(tags=["studio"])


# ── Export presets ─────────────────────────────────────────────────────────────

@router.get("/export-presets")
async def get_export_presets(user_id: str = Depends(get_current_user_id)):
    return {"presets": list_presets()}


@router.get("/export-presets/{preset_id}")
async def get_export_preset(preset_id: str, user_id: str = Depends(get_current_user_id)):
    try:
        return get_preset(preset_id).__dict__
    except KeyError:
        raise HTTPException(status_code=404, detail="Preset not found")


# ── Thumbnails ─────────────────────────────────────────────────────────────────

class ThumbnailOut(BaseModel):
    id: uuid.UUID
    video_id: uuid.UUID
    url: str
    variant: str
    timestamp: float | None = None
    label: str | None = None


class RegenerateThumbnailsRequest(BaseModel):
    count: int = Field(default=4, ge=1, le=8)
    timestamps: list[float] | None = None


@router.get("/videos/{video_id}/thumbnails", response_model=list[ThumbnailOut])
async def list_thumbnails(
    video_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    video = await db.scalar(select(Video).where(Video.id == video_id, Video.user_id == user_id))
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    rows = (
        await db.execute(
            select(GeneratedThumbnail)
            .where(GeneratedThumbnail.video_id == video_id)
            .order_by(GeneratedThumbnail.created_at.desc())
        )
    ).scalars().all()

    out: list[ThumbnailOut] = []
    for row in rows:
        try:
            url = storage_service.presign_url(row.storage_key)
        except Exception:
            continue
        out.append(ThumbnailOut(
            id=row.id, video_id=row.video_id, url=url,
            variant=row.variant, timestamp=row.timestamp, label=row.label,
        ))
    return out


@router.post(
    "/videos/{video_id}/thumbnails/regenerate",
    response_model=list[ThumbnailOut],
    status_code=status.HTTP_202_ACCEPTED,
)
@limiter.limit(settings.rate_limit_agent)
async def regenerate_thumbnails(
    request: Request,
    video_id: uuid.UUID,
    body: RegenerateThumbnailsRequest | None = None,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    video = await db.scalar(select(Video).where(Video.id == video_id, Video.user_id == user_id))
    if not video or not video.s3_key:
        raise HTTPException(status_code=404, detail="Video not found")

    body = body or RegenerateThumbnailsRequest()
    from app.services.thumbnails import thumbnail_service

    scenes = (
        await db.execute(select(Scene).where(Scene.video_id == video_id).order_by(Scene.scene_number))
    ).scalars().all()
    scene_dicts = [{"start_time": s.start_time, "end_time": s.end_time} for s in scenes]
    timestamps = body.timestamps or thumbnail_service.pick_highlight_timestamps(scene_dicts, body.count)

    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = os.path.join(tmpdir, video.filename or "source.mp4")
        storage_service.download_to_file(video.s3_key, video_path)
        results = thumbnail_service.generate_ai_thumbnail_options(
            video_path, str(video_id), timestamps, count=body.count,
        )

    out: list[ThumbnailOut] = []
    for result in results:
        row = GeneratedThumbnail(
            video_id=video_id,
            storage_key=result.key,
            variant=result.variant,
            timestamp=result.timestamp,
            label=f"Regenerated @ {result.timestamp:.1f}s",
        )
        db.add(row)
        await db.flush()
        out.append(ThumbnailOut(
            id=row.id, video_id=video_id,
            url=storage_service.presign_url(result.key),
            variant=result.variant, timestamp=result.timestamp, label=row.label,
        ))

    analytics.capture(user_id, "thumbnails_regenerated", {
        "video_id": str(video_id), "count": len(out),
    })
    return out


# ── Brand kits ─────────────────────────────────────────────────────────────────

class BrandKitCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    primary_color: str | None = None
    secondary_color: str | None = None
    accent_color: str | None = None
    font_family: str | None = None
    caption_preset: str | None = None
    is_default: bool = False
    extras: dict | None = None


class BrandKitUpdate(BaseModel):
    name: str | None = None
    primary_color: str | None = None
    secondary_color: str | None = None
    accent_color: str | None = None
    font_family: str | None = None
    caption_preset: str | None = None
    is_default: bool | None = None
    extras: dict | None = None


class BrandKitOut(BaseModel):
    id: uuid.UUID
    name: str
    primary_color: str | None = None
    secondary_color: str | None = None
    accent_color: str | None = None
    font_family: str | None = None
    logo_url: str | None = None
    watermark_url: str | None = None
    caption_preset: str | None = None
    is_default: bool = False
    extras: dict | None = None
    created_at: datetime

    class Config:
        from_attributes = True


def _kit_to_out(kit: BrandKit) -> BrandKitOut:
    logo_url = storage_service.presign_url(kit.logo_key) if kit.logo_key else None
    watermark_url = storage_service.presign_url(kit.watermark_key) if kit.watermark_key else None
    return BrandKitOut(
        id=kit.id, name=kit.name,
        primary_color=kit.primary_color, secondary_color=kit.secondary_color,
        accent_color=kit.accent_color, font_family=kit.font_family,
        logo_url=logo_url, watermark_url=watermark_url,
        caption_preset=kit.caption_preset, is_default=kit.is_default,
        extras=kit.extras, created_at=kit.created_at,
    )


@router.get("/brand-kits", response_model=list[BrandKitOut])
async def list_brand_kits(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    rows = (
        await db.execute(
            select(BrandKit).where(BrandKit.user_id == user_id).order_by(BrandKit.created_at.desc())
        )
    ).scalars().all()
    return [_kit_to_out(k) for k in rows]


@router.post("/brand-kits", response_model=BrandKitOut, status_code=status.HTTP_201_CREATED)
async def create_brand_kit(
    body: BrandKitCreate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    if body.is_default:
        existing = (
            await db.execute(select(BrandKit).where(BrandKit.user_id == user_id, BrandKit.is_default.is_(True)))
        ).scalars().all()
        for k in existing:
            k.is_default = False

    kit = BrandKit(user_id=user_id, **body.model_dump())
    db.add(kit)
    await db.flush()
    analytics.capture(user_id, "brand_kit_created", {"kit_id": str(kit.id)})
    return _kit_to_out(kit)


@router.patch("/brand-kits/{kit_id}", response_model=BrandKitOut)
async def update_brand_kit(
    kit_id: uuid.UUID,
    body: BrandKitUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    kit = await db.scalar(select(BrandKit).where(BrandKit.id == kit_id, BrandKit.user_id == user_id))
    if not kit:
        raise HTTPException(status_code=404, detail="Brand kit not found")
    data = body.model_dump(exclude_unset=True)
    if data.get("is_default"):
        existing = (
            await db.execute(select(BrandKit).where(BrandKit.user_id == user_id, BrandKit.is_default.is_(True)))
        ).scalars().all()
        for k in existing:
            k.is_default = False
    for k, v in data.items():
        setattr(kit, k, v)
    await db.flush()
    return _kit_to_out(kit)


@router.delete("/brand-kits/{kit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_brand_kit(
    kit_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    kit = await db.scalar(select(BrandKit).where(BrandKit.id == kit_id, BrandKit.user_id == user_id))
    if not kit:
        raise HTTPException(status_code=404, detail="Brand kit not found")
    await db.delete(kit)


@router.post("/brand-kits/{kit_id}/logo", response_model=BrandKitOut)
@limiter.limit(settings.rate_limit_agent)
async def upload_brand_logo(
    request: Request,
    kit_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    """Upload a brand kit logo (PNG/JPEG/WebP/SVG) to MinIO."""
    kit = await db.scalar(select(BrandKit).where(BrandKit.id == kit_id, BrandKit.user_id == user_id))
    if not kit:
        raise HTTPException(status_code=404, detail="Brand kit not found")

    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(status_code=400, detail="file required")

    content = await upload.read()  # type: ignore[union-attr]
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Logo must be ≤ 5 MB")

    filename = getattr(upload, "filename", "logo.png") or "logo.png"
    ext = os.path.splitext(filename)[1].lower() or ".png"
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".svg"):
        raise HTTPException(status_code=400, detail="Unsupported image type")

    content_type = getattr(upload, "content_type", None) or "image/png"
    key = f"brand-kits/{user_id}/{kit_id}/logo{ext}"
    storage_service.upload_bytes(key, content, content_type=content_type)
    kit.logo_key = key
    await db.flush()
    analytics.capture(user_id, "brand_logo_uploaded", {"kit_id": str(kit_id)})
    return _kit_to_out(kit)


# ── Localization stubs (translation / dubbing) ─────────────────────────────────

SUPPORTED_LOCALES = ["en", "hi", "mr"]


class TranslateRequest(BaseModel):
    target_language: str = Field(..., description="en | hi | mr")
    source_language: str | None = "auto"
    review_before_dub: bool = True


class TranslateJobOut(BaseModel):
    job_id: str
    video_id: uuid.UUID
    status: str
    target_language: str
    message: str
    preview_segments: list[dict] = []


class DubRequest(BaseModel):
    target_language: str
    voice_clone: bool = False
    voice_clone_consent: bool = False
    translate_job_id: str | None = None


@router.post(
    "/videos/{video_id}/translate",
    response_model=TranslateJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
@limiter.limit(settings.rate_limit_agent)
async def translate_video(
    request: Request,
    video_id: uuid.UUID,
    body: TranslateRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    """Translate transcript via configured LLM (vLLM/Ollama) with stub fallback."""
    video = await db.scalar(select(Video).where(Video.id == video_id, Video.user_id == user_id))
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    if body.target_language not in SUPPORTED_LOCALES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported language. Supported: {', '.join(SUPPORTED_LOCALES)}",
        )

    from app.models.video import Segment
    from app.models.localization import LocalizationJob
    from app.services.translation import translate_segments

    segs = (
        await db.execute(
            select(Segment).where(Segment.video_id == video_id).order_by(Segment.start_time).limit(60)
        )
    ).scalars().all()

    payload = [{"start": s.start_time, "end": s.end_time, "text": s.text} for s in segs]
    results, used_llm = translate_segments(
        payload, body.target_language, body.source_language or "auto",
    )
    preview = [
        {
            "start": r.start,
            "end": r.end,
            "source_text": r.source_text,
            "translated_text": r.translated_text,
            "status": r.status,
        }
        for r in results
    ]

    job = LocalizationJob(
        video_id=video_id,
        user_id=user_id,
        job_type="translate",
        target_language=body.target_language,
        status="preview_ready",
        provider="llm" if used_llm else "stub",
        result={"segments": preview},
    )
    db.add(job)
    await db.flush()

    analytics.capture(user_id, "translate_requested", {
        "video_id": str(video_id), "target": body.target_language,
        "job_id": str(job.id), "used_llm": used_llm,
    })
    return TranslateJobOut(
        job_id=str(job.id),
        video_id=video_id,
        status="preview_ready",
        target_language=body.target_language,
        message=(
            f"Translated {len(preview)} segments via {'LLM' if used_llm else 'stub fallback'}. "
            "Review before dubbing."
        ),
        preview_segments=preview,
    )


@router.post(
    "/videos/{video_id}/dub",
    response_model=TranslateJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
@limiter.limit(settings.rate_limit_agent)
async def dub_video(
    request: Request,
    video_id: uuid.UUID,
    body: DubRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    """TTS dubbing. Requires explicit consent for voice cloning."""
    video = await db.scalar(select(Video).where(Video.id == video_id, Video.user_id == user_id))
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    if body.target_language not in SUPPORTED_LOCALES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported language. Supported: {', '.join(SUPPORTED_LOCALES)}",
        )
    if body.voice_clone and not body.voice_clone_consent:
        raise HTTPException(
            status_code=400,
            detail="Voice cloning requires explicit consent (voice_clone_consent=true).",
        )

    from app.models.localization import LocalizationJob
    from app.services.tts import synthesize_speech
    from app.services.storage import storage_service as _storage

    # Load prior translation if provided
    segments: list[dict] = []
    if body.translate_job_id:
        try:
            tid = uuid.UUID(body.translate_job_id)
            prior = await db.scalar(
                select(LocalizationJob).where(
                    LocalizationJob.id == tid,
                    LocalizationJob.user_id == user_id,
                    LocalizationJob.video_id == video_id,
                )
            )
            if prior and prior.result and prior.result.get("segments"):
                segments = prior.result["segments"]
        except Exception:
            pass

    if not segments:
        from app.models.video import Segment
        from app.services.translation import translate_segments
        segs = (
            await db.execute(
                select(Segment).where(Segment.video_id == video_id).order_by(Segment.start_time).limit(20)
            )
        ).scalars().all()
        payload = [{"start": s.start_time, "end": s.end_time, "text": s.text} for s in segs]
        results, _ = translate_segments(payload, body.target_language)
        segments = [
            {"start": r.start, "end": r.end, "translated_text": r.translated_text, "source_text": r.source_text}
            for r in results
        ]

    # Synthesize first few segments into one combined preview audio
    joined = " ".join(
        (s.get("translated_text") or s.get("source_text") or "")[:200]
        for s in segments[:8]
    )
    audio_key = None
    provider = "stub"
    message = "Dubbing completed."
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "dub_preview.mp3")
        try:
            dub = synthesize_speech(
                joined or "Hello",
                body.target_language,
                out,
                voice_clone=body.voice_clone,
            )
            provider = dub.provider
            message = dub.message
            if os.path.exists(out) and os.path.getsize(out) > 0:
                audio_key = f"dubs/{video_id}/{uuid.uuid4().hex[:8]}.mp3"
                # edge may write mp3; stub writes wav — detect
                if not out.endswith(".mp3") and os.path.exists(out.replace(".mp3", ".wav")):
                    out = out.replace(".mp3", ".wav")
                    audio_key = audio_key.replace(".mp3", ".wav")
                ctype = "audio/mpeg" if out.endswith(".mp3") else "audio/wav"
                if os.path.exists(out):
                    _storage.upload_file(audio_key, out, content_type=ctype)
                else:
                    audio_key = None
        except Exception as exc:
            logger.warning(f"Dub synthesize failed: {exc}")
            message = f"Dubbing fallback: {exc}"

    job = LocalizationJob(
        video_id=video_id,
        user_id=user_id,
        job_type="dub",
        target_language=body.target_language,
        status="complete" if audio_key else "queued_stub",
        provider=provider,
        result={"segments": segments[:20]},
        audio_key=audio_key,
    )
    db.add(job)
    await db.flush()

    analytics.capture(user_id, "dub_requested", {
        "video_id": str(video_id),
        "target": body.target_language,
        "voice_clone": body.voice_clone,
        "job_id": str(job.id),
        "provider": provider,
    })
    return TranslateJobOut(
        job_id=str(job.id),
        video_id=video_id,
        status=job.status,
        target_language=body.target_language,
        message=message + " Original audio remains on a separate editable track.",
        preview_segments=segments[:10],
    )


# ── Captions export helpers ────────────────────────────────────────────────────

@router.get("/videos/{video_id}/captions/export")
async def export_captions(
    video_id: uuid.UUID,
    format: str = "srt",
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    """Export transcript as SRT or VTT."""
    from fastapi.responses import PlainTextResponse
    from app.models.video import Segment

    video = await db.scalar(select(Video).where(Video.id == video_id, Video.user_id == user_id))
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    if format not in ("srt", "vtt"):
        raise HTTPException(status_code=400, detail="format must be srt or vtt")

    segs = (
        await db.execute(
            select(Segment).where(Segment.video_id == video_id).order_by(Segment.start_time)
        )
    ).scalars().all()

    def ts(s: float, vtt: bool = False) -> str:
        h = int(s // 3600)
        m = int((s % 3600) // 60)
        sec = int(s % 60)
        ms = int((s - int(s)) * 1000)
        sep = "." if vtt else ","
        return f"{h:02d}:{m:02d}:{sec:02d}{sep}{ms:03d}"

    if format == "vtt":
        lines = ["WEBVTT", ""]
        for i, seg in enumerate(segs, 1):
            lines += [
                f"{ts(seg.start_time, True)} --> {ts(seg.end_time, True)}",
                seg.text,
                "",
            ]
        return PlainTextResponse("\n".join(lines), media_type="text/vtt")

    lines = []
    for i, seg in enumerate(segs, 1):
        lines += [
            str(i),
            f"{ts(seg.start_time)} --> {ts(seg.end_time)}",
            seg.text,
            "",
        ]
    return PlainTextResponse("\n".join(lines), media_type="application/x-subrip")
