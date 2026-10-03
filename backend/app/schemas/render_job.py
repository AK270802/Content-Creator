"""Render job + approve request schemas with export presets."""

import uuid
from datetime import datetime
from pydantic import BaseModel, Field
from app.models.render_job import RenderStatus


class ExportOptions(BaseModel):
    preset_id: str = Field(default="youtube_1080p", description="Platform export preset id")
    aspect_ratio: str | None = Field(default=None, description="Override preset aspect ratio")
    burn_captions: bool = True
    brand_kit_id: uuid.UUID | None = None
    prefer_faces: bool = True
    resolution: str | None = Field(default=None, description="Optional 720p|1080p|1440p|4k override")


class RenderJobResponse(BaseModel):
    id: uuid.UUID
    edit_plan_id: uuid.UUID
    video_id: uuid.UUID
    status: RenderStatus
    output_url: str | None = None
    error_message: str | None = None
    preset_id: str | None = None
    aspect_ratio: str | None = None
    burn_captions: bool | None = True
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime

    class Config:
        from_attributes = True
