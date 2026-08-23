import uuid
import enum
from datetime import datetime
from sqlalchemy import String, Text, Integer, Enum as SAEnum, ForeignKey, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.video import Base


class EditPlanStatus(str, enum.Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    RENDERING = "rendering"
    COMPLETE = "complete"
    FAILED = "failed"


class EditSegmentAction(str, enum.Enum):
    KEEP = "keep"
    CUT = "cut"


class EditPlan(Base):
    __tablename__ = "edit_plans"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    status: Mapped[EditPlanStatus] = mapped_column(
        SAEnum(EditPlanStatus), nullable=False, default=EditPlanStatus.DRAFT
    )
    llm_generated: Mapped[bool] = mapped_column(nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), onupdate=datetime.utcnow, nullable=False
    )

    segments: Mapped[list["EditPlanSegment"]] = relationship(
        "EditPlanSegment", back_populates="edit_plan",
        cascade="all, delete-orphan", order_by="EditPlanSegment.order"
    )


class EditPlanSegment(Base):
    __tablename__ = "edit_plan_segments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    edit_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("edit_plans.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    scene_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenes.id", ondelete="CASCADE"),
        nullable=False
    )
    action: Mapped[EditSegmentAction] = mapped_column(
        SAEnum(EditSegmentAction), nullable=False, default=EditSegmentAction.KEEP
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    edit_plan: Mapped["EditPlan"] = relationship("EditPlan", back_populates="segments")
