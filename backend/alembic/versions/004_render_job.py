"""Add render_jobs table

Revision ID: 004
Revises: 003
Create Date: 2026-08-23
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "render_jobs",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("edit_plan_id", UUID(as_uuid=True), sa.ForeignKey("edit_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("video_id", UUID(as_uuid=True), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.Enum("pending", "processing", "complete", "failed", name="renderstatus"), nullable=False, server_default="pending"),
        sa.Column("output_key", sa.String(1024), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_render_jobs_edit_plan_id", "render_jobs", ["edit_plan_id"])
    op.create_index("ix_render_jobs_video_id", "render_jobs", ["video_id"])


def downgrade() -> None:
    op.drop_table("render_jobs")
    op.execute("DROP TYPE IF EXISTS renderstatus")
