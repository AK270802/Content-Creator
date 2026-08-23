"""Add edit_plans and edit_plan_segments tables

Revision ID: 003
Revises: 002
Create Date: 2026-08-23
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "edit_plans",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("video_id", UUID(as_uuid=True), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.Enum("draft", "approved", "rendering", "complete", "failed", name="editplanstatus"), nullable=False, server_default="draft"),
        sa.Column("llm_generated", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_edit_plans_video_id", "edit_plans", ["video_id"])

    op.create_table(
        "edit_plan_segments",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("edit_plan_id", UUID(as_uuid=True), sa.ForeignKey("edit_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scene_id", UUID(as_uuid=True), sa.ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.Enum("keep", "cut", name="editsegmentaction"), nullable=False, server_default="keep"),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("caption", sa.String(1024), nullable=True),
        sa.Column("order", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index("ix_edit_plan_segments_edit_plan_id", "edit_plan_segments", ["edit_plan_id"])


def downgrade() -> None:
    op.drop_table("edit_plan_segments")
    op.drop_table("edit_plans")
    op.execute("DROP TYPE IF EXISTS editsegmentaction")
    op.execute("DROP TYPE IF EXISTS editplanstatus")
