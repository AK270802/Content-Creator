"""Proxy media generation for lightweight editor preview."""

from __future__ import annotations

import os
import subprocess
import tempfile

from loguru import logger

from app.services.storage import storage_service


def generate_proxy(
    source_path: str,
    video_id: str,
    *,
    height: int = 720,
    crf: int = 28,
) -> str:
    """
    Create a 720p H.264 proxy and upload to MinIO.
    Returns storage key.
    """
    key = f"proxies/{video_id}/preview_720p.mp4"
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "proxy.mp4")
        # Even height for libx264
        h = height - (height % 2)
        vf = f"scale=-2:{h}"
        cmd = [
            "ffmpeg", "-y", "-i", source_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
            "-c:a", "aac", "-b:a", "128k", "-ac", "2",
            "-movflags", "+faststart",
            out,
        ]
        logger.info(f"Generating proxy for {video_id}")
        subprocess.run(cmd, check=True, capture_output=True)
        storage_service.upload_file(key, out, content_type="video/mp4")
    return key
