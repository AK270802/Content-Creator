import uuid
from datetime import datetime
from pydantic import BaseModel, Field
from app.models.edit_plan import EditPlanStatus, EditSegmentAction


class EditPlanSegmentSchema(BaseModel):
    id: uuid.UUID | None = None
    scene_id: uuid.UUID
    action: EditSegmentAction
    reason: str | None = None
    caption: str | None = None
    text_overlay: str | None = None
    start_ts: float | None = None
    end_ts: float | None = None
    order: int = 0
    brightness: float | None = None
    contrast: float | None = None
    saturation: float | None = None
    fade_in: float | None = None
    fade_out: float | None = None
    effect: str | None = None
    # Computed absolute timestamps (populated by _plan_to_schema using scene join)
    start: float = 0.0
    end: float = 0.0
    thumbnail_url: str | None = None

    model_config = {"from_attributes": True}


class EditPlanSchema(BaseModel):
    id: uuid.UUID
    video_id: uuid.UUID
    status: EditPlanStatus
    llm_generated: bool
    version: int = 1
    parent_version_id: uuid.UUID | None = None
    parent_plan_id: uuid.UUID | None = None  # frontend alias for parent_version_id
    revision_instruction: str | None = None
    created_at: datetime
    updated_at: datetime
    segments: list[EditPlanSegmentSchema] = []

    model_config = {"from_attributes": True}


class EditPlanSegmentUpdate(BaseModel):
    id: uuid.UUID
    action: EditSegmentAction | None = None
    caption: str | None = None
    text_overlay: str | None = None
    start_ts: float | None = None
    end_ts: float | None = None
    order: int | None = None
    brightness: float | None = None
    contrast: float | None = None
    saturation: float | None = None
    fade_in: float | None = None
    fade_out: float | None = None
    effect: str | None = None


class EditPlanSegmentCreate(BaseModel):
    scene_id: uuid.UUID
    action: EditSegmentAction = EditSegmentAction.KEEP
    caption: str | None = None
    text_overlay: str | None = None
    start_ts: float
    end_ts: float
    order: int = 0
    brightness: float | None = None
    contrast: float | None = None
    saturation: float | None = None
    fade_in: float | None = None
    fade_out: float | None = None
    effect: str | None = None


class EditPlanPatchRequest(BaseModel):
    segments: list[EditPlanSegmentUpdate] = []
    create: list[EditPlanSegmentCreate] = []
    delete_ids: list[uuid.UUID] = []


class ReviseRequest(BaseModel):
    instruction: str = Field(..., min_length=1, max_length=2000)


# ── Internal schema used by planning/revision service ──────────────────────────

class LLMPlanSegment(BaseModel):
    scene_id: uuid.UUID
    action: EditSegmentAction
    reason: str = ""
    caption: str = ""
    order: int = 0


class LLMPlan(BaseModel):
    segments: list[LLMPlanSegment]
