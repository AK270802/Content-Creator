import uuid
from datetime import datetime
from pydantic import BaseModel
from app.models.render_job import RenderStatus


class RenderJobResponse(BaseModel):
    id: uuid.UUID
    edit_plan_id: uuid.UUID
    video_id: uuid.UUID
    status: RenderStatus
    output_url: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime

    class Config:
        from_attributes = True
