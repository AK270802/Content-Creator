import uuid
from datetime import datetime
from pydantic import BaseModel, model_validator
from app.models.enums import VideoStatus


class VideoCreateResponse(BaseModel):
    video_id: uuid.UUID
    status: VideoStatus
    message: str = "Video upload accepted. Processing started."


class VideoResponse(BaseModel):
    """Full video record — returned by GET /videos and GET /videos/{id}."""
    id: uuid.UUID
    filename: str | None = None
    status: VideoStatus
    error: str | None = None
    duration: float | None = None
    created_at: datetime
    updated_at: datetime
    playback_url: str | None = None
    thumbnail_url: str | None = None

    model_config = {"from_attributes": False}

    @classmethod
    def from_orm(cls, obj) -> "VideoResponse":
        from app.services.storage import storage_service
        playback_url = storage_service.presign_url(obj.s3_key) if getattr(obj, "s3_key", None) else None
        # Prefer proxy for editor preview when available
        proxy_url = storage_service.presign_url(obj.proxy_key) if getattr(obj, "proxy_key", None) else None
        thumbnail_url = storage_service.presign_url(obj.thumbnail_key) if getattr(obj, "thumbnail_key", None) else None
        return cls(
            id=obj.id,
            filename=obj.filename,
            status=obj.status,
            error=obj.error_message,
            duration=obj.duration_seconds,
            created_at=obj.created_at,
            updated_at=obj.updated_at,
            playback_url=proxy_url or playback_url,
            thumbnail_url=thumbnail_url,
        )


class VideoStatusResponse(BaseModel):
    """Status-only response kept for the /status polling endpoint."""
    video_id: uuid.UUID
    status: VideoStatus
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SceneResponse(BaseModel):
    id: uuid.UUID
    scene_number: int
    start_time: float
    end_time: float
    thumbnail_url: str | None = None
    start: float = 0.0
    end: float = 0.0
    index: int = 0

    model_config = {"from_attributes": True}

    @model_validator(mode="after")
    def _set_aliases(self) -> "SceneResponse":
        self.start = self.start_time
        self.end = self.end_time
        self.index = self.scene_number
        return self


class SegmentResponse(BaseModel):
    id: uuid.UUID
    start_time: float
    end_time: float
    text: str
    speaker: str | None = None
    start: float = 0.0
    end: float = 0.0

    model_config = {"from_attributes": True}

    @model_validator(mode="after")
    def _set_aliases(self) -> "SegmentResponse":
        self.start = self.start_time
        self.end = self.end_time
        return self


class TranscriptResponse(BaseModel):
    video_id: uuid.UUID
    total: int
    page: int
    page_size: int
    segments: list[SegmentResponse]
