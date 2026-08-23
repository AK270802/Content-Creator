"""005 timeline events

Revision ID: 005
Revises: 004
Create Date: 2026-08-23
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "timeline_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("video_id", UUID(as_uuid=True), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_id", sa.Integer, nullable=False),
        sa.Column("start_ts", sa.Float, nullable=False),
        sa.Column("end_ts", sa.Float, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("tags", JSONB, nullable=True),
        sa.Column("confidence", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_timeline_events_video_id", "timeline_events", ["video_id"])


def downgrade() -> None:
    op.drop_index("ix_timeline_events_video_id", table_name="timeline_events")
    op.drop_table("timeline_events")
