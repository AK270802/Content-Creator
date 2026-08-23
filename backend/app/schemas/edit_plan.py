import uuid
from datetime import datetime
from pydantic import BaseModel, model_validator
from app.models.edit_plan import EditPlanStatus, EditSegmentAction


class EditPlanSegmentSchema(BaseModel):
    scene_id: uuid.UUID
    action: EditSegmentAction
    reason: str | None = None
    caption: str | None = None
    order: int = 0

    class Config:
        from_attributes = True


class EditPlanSchema(BaseModel):
    id: uuid.UUID
    video_id: uuid.UUID
    status: EditPlanStatus
    llm_generated: bool
    created_at: datetime
    updated_at: datetime
    segments: list[EditPlanSegmentSchema] = []

    class Config:
        from_attributes = True


class EditPlanSegmentUpdate(BaseModel):
    id: uuid.UUID
    action: EditSegmentAction | None = None
    caption: str | None = None
    order: int | None = None


class EditPlanPatchRequest(BaseModel):
    segments: list[EditPlanSegmentUpdate]


# ── Internal schema used by planning service ──────────────────────────────────
class LLMPlanSegment(BaseModel):
    scene_id: uuid.UUID
    action: EditSegmentAction
    reason: str = ""
    caption: str = ""
    order: int = 0


class LLMPlan(BaseModel):
    segments: list[LLMPlanSegment]
