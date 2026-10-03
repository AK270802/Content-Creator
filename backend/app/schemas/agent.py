import uuid
from pydantic import BaseModel, Field


class AgentEditRequest(BaseModel):
    video_id: uuid.UUID
    instruction: str = Field(..., min_length=5, max_length=2000)


class CutOperation(BaseModel):
    type: str = "cut"
    start_time: float
    end_time: float
    reason: str


class EditPlan(BaseModel):
    video_id: uuid.UUID
    instruction: str
    operations: list[CutOperation]
    summary: str
    estimated_duration_seconds: float | None = None


class AgentEditResponse(BaseModel):
    video_id: uuid.UUID
    edit_plan: EditPlan
    llm_available: bool
