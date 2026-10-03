"""Scene and AI thumbnail generation via ffmpeg frame extract + MinIO upload."""

from __future__ import annotations

import os
import tempfile
import uuid
from dataclasses import dataclass

from loguru import logger


@dataclass
class ThumbnailResult:
    key: str
    scene_id: str | None = None
    timestamp: float = 0.0
    variant: str = "scene"


def _extract_frame(video_path: str, timestamp: float, out_path: str, width: int = 640) -> None:
    import ffmpeg

    (
        ffmpeg
        .input(video_path, ss=max(0.0, timestamp))
        .filter("scale", width, -2)
        .output(out_path, vframes=1, format="image2", vcodec="mjpeg", qscale=2)
        .overwrite_output()
        .run(quiet=True)
    )


def generate_scene_thumbnail(
    video_path: str,
    video_id: str,
    scene_id: str,
    start_time: float,
    end_time: float,
) -> ThumbnailResult:
    """Extract mid-scene frame and upload to MinIO."""
    from app.services.storage import storage_service

    mid = (start_time + end_time) / 2.0
    key = f"thumbnails/{video_id}/scenes/{scene_id}.jpg"

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "thumb.jpg")
        _extract_frame(video_path, mid, out)
        storage_service.upload_file(key, out, content_type="image/jpeg")

    logger.debug(f"Scene thumbnail uploaded: {key}")
    return ThumbnailResult(key=key, scene_id=scene_id, timestamp=mid, variant="scene")


def generate_video_poster(
    video_path: str,
    video_id: str,
    duration: float | None = None,
) -> ThumbnailResult:
    """Dashboard poster — frame at ~12% into the video (or 1s)."""
    from app.services.storage import storage_service

    ts = 1.0
    if duration and duration > 2:
        ts = min(duration * 0.12, duration - 0.5)
    key = f"thumbnails/{video_id}/poster.jpg"

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "poster.jpg")
        _extract_frame(video_path, ts, out, width=960)
        storage_service.upload_file(key, out, content_type="image/jpeg")

    return ThumbnailResult(key=key, timestamp=ts, variant="poster")


def generate_ai_thumbnail_options(
    video_path: str,
    video_id: str,
    timestamps: list[float],
    count: int = 4,
) -> list[ThumbnailResult]:
    """Generate distinct thumbnail candidates at provided timestamps."""
    from app.services.storage import storage_service

    results: list[ThumbnailResult] = []
    for i, ts in enumerate(timestamps[:count]):
        key = f"thumbnails/{video_id}/ai/{uuid.uuid4().hex[:8]}_{i}.jpg"
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, f"ai_{i}.jpg")
            try:
                _extract_frame(video_path, ts, out, width=1280)
                storage_service.upload_file(key, out, content_type="image/jpeg")
                results.append(ThumbnailResult(key=key, timestamp=ts, variant="ai"))
            except Exception as exc:
                logger.warning(f"AI thumbnail at t={ts} failed: {exc}")
    return results


def pick_highlight_timestamps(
    scenes: list[dict],
    count: int = 4,
) -> list[float]:
    """Pick visually spaced midpoints from scene list for AI thumbnails."""
    if not scenes:
        return [1.0, 5.0, 10.0, 15.0][:count]
    ranked = sorted(scenes, key=lambda s: s.get("end_time", 0) - s.get("start_time", 0), reverse=True)
    picks: list[float] = []
    for sc in ranked:
        mid = (sc["start_time"] + sc["end_time"]) / 2.0
        if all(abs(mid - p) > 2.0 for p in picks):
            picks.append(mid)
        if len(picks) >= count:
            break
    while len(picks) < count and scenes:
        sc = scenes[len(picks) % len(scenes)]
        picks.append((sc["start_time"] + sc["end_time"]) / 2.0)
    return picks[:count]


thumbnail_service = type("ThumbnailService", (), {
    "generate_scene_thumbnail": staticmethod(generate_scene_thumbnail),
    "generate_video_poster": staticmethod(generate_video_poster),
    "generate_ai_thumbnail_options": staticmethod(generate_ai_thumbnail_options),
    "pick_highlight_timestamps": staticmethod(pick_highlight_timestamps),
})()
