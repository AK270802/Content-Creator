"""Brand kit model — reusable logos, colors, fonts, caption presets."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import String, Text, ForeignKey, text, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.video import Base


class BrandKit(Base):
    __tablename__ = "brand_kits"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    primary_color: Mapped[str | None] = mapped_column(String(32), nullable=True)
    secondary_color: Mapped[str | None] = mapped_column(String(32), nullable=True)
    accent_color: Mapped[str | None] = mapped_column(String(32), nullable=True)
    font_family: Mapped[str | None] = mapped_column(String(128), nullable=True)
    logo_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    watermark_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    caption_preset: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Free-form extras: lower_third, safe_margins, etc.
    extras: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.utcnow, nullable=False
    )


class GeneratedThumbnail(Base):
    """AI / scene thumbnail variants persisted for the Thumbnail Studio."""

    __tablename__ = "generated_thumbnails"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    variant: Mapped[str] = mapped_column(String(32), nullable=False, server_default="ai")
    timestamp: Mapped[float | None] = mapped_column(nullable=True)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
