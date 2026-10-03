"""Platform-aware export presets (YouTube, Shorts, Reels, TikTok, etc.).

Specs live here as data so they can be updated without hardcoding into render paths.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Literal


AspectRatio = Literal["16:9", "9:16", "1:1", "4:5"]


@dataclass(frozen=True)
class ExportPreset:
    id: str
    label: str
    platform: str
    aspect_ratio: AspectRatio
    width: int
    height: int
    max_duration_s: float | None
    fps: int
    video_bitrate: str
    audio_bitrate: str
    caption_safe_margin: float  # fraction of frame height from bottom
    description: str


EXPORT_PRESETS: dict[str, ExportPreset] = {
    "youtube_1080p": ExportPreset(
        id="youtube_1080p",
        label="YouTube 1080p",
        platform="youtube",
        aspect_ratio="16:9",
        width=1920,
        height=1080,
        max_duration_s=None,
        fps=30,
        video_bitrate="8M",
        audio_bitrate="192k",
        caption_safe_margin=0.12,
        description="Standard landscape YouTube upload",
    ),
    "youtube_4k": ExportPreset(
        id="youtube_4k",
        label="YouTube 4K",
        platform="youtube",
        aspect_ratio="16:9",
        width=3840,
        height=2160,
        max_duration_s=None,
        fps=30,
        video_bitrate="35M",
        audio_bitrate="320k",
        caption_safe_margin=0.12,
        description="4K landscape YouTube upload",
    ),
    "youtube_shorts": ExportPreset(
        id="youtube_shorts",
        label="YouTube Shorts",
        platform="youtube_shorts",
        aspect_ratio="9:16",
        width=1080,
        height=1920,
        max_duration_s=60.0,
        fps=30,
        video_bitrate="8M",
        audio_bitrate="192k",
        caption_safe_margin=0.18,
        description="Vertical Shorts (≤60s)",
    ),
    "instagram_reels": ExportPreset(
        id="instagram_reels",
        label="Instagram Reels",
        platform="instagram",
        aspect_ratio="9:16",
        width=1080,
        height=1920,
        max_duration_s=90.0,
        fps=30,
        video_bitrate="8M",
        audio_bitrate="192k",
        caption_safe_margin=0.22,
        description="Vertical Reels (≤90s)",
    ),
    "instagram_feed": ExportPreset(
        id="instagram_feed",
        label="Instagram Feed 1:1",
        platform="instagram",
        aspect_ratio="1:1",
        width=1080,
        height=1080,
        max_duration_s=60.0,
        fps=30,
        video_bitrate="6M",
        audio_bitrate="192k",
        caption_safe_margin=0.15,
        description="Square Instagram feed video",
    ),
    "instagram_portrait": ExportPreset(
        id="instagram_portrait",
        label="Instagram 4:5",
        platform="instagram",
        aspect_ratio="4:5",
        width=1080,
        height=1350,
        max_duration_s=60.0,
        fps=30,
        video_bitrate="6M",
        audio_bitrate="192k",
        caption_safe_margin=0.15,
        description="Portrait Instagram feed",
    ),
    "tiktok": ExportPreset(
        id="tiktok",
        label="TikTok",
        platform="tiktok",
        aspect_ratio="9:16",
        width=1080,
        height=1920,
        max_duration_s=180.0,
        fps=30,
        video_bitrate="8M",
        audio_bitrate="192k",
        caption_safe_margin=0.22,
        description="Vertical TikTok (≤3 min)",
    ),
    "facebook_reels": ExportPreset(
        id="facebook_reels",
        label="Facebook Reels",
        platform="facebook",
        aspect_ratio="9:16",
        width=1080,
        height=1920,
        max_duration_s=90.0,
        fps=30,
        video_bitrate="8M",
        audio_bitrate="192k",
        caption_safe_margin=0.2,
        description="Vertical Facebook Reels",
    ),
    "linkedin": ExportPreset(
        id="linkedin",
        label="LinkedIn",
        platform="linkedin",
        aspect_ratio="16:9",
        width=1920,
        height=1080,
        max_duration_s=600.0,
        fps=30,
        video_bitrate="6M",
        audio_bitrate="192k",
        caption_safe_margin=0.12,
        description="Landscape LinkedIn video",
    ),
    "source": ExportPreset(
        id="source",
        label="Source / Original",
        platform="generic",
        aspect_ratio="16:9",
        width=0,
        height=0,
        max_duration_s=None,
        fps=30,
        video_bitrate="8M",
        audio_bitrate="192k",
        caption_safe_margin=0.12,
        description="Keep source resolution; no reframe",
    ),
}


def list_presets() -> list[dict]:
    return [asdict(p) for p in EXPORT_PRESETS.values()]


def get_preset(preset_id: str) -> ExportPreset:
    if preset_id not in EXPORT_PRESETS:
        raise KeyError(f"Unknown export preset: {preset_id}")
    return EXPORT_PRESETS[preset_id]
