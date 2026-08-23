"""007 segment trim points and text overlay

Revision ID: 007
Revises: 006
Create Date: 2026-08-23
"""
from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("edit_plan_segments", sa.Column("start_ts", sa.Float, nullable=True))
    op.add_column("edit_plan_segments", sa.Column("end_ts", sa.Float, nullable=True))
    op.add_column("edit_plan_segments", sa.Column("text_overlay", sa.String(1024), nullable=True))


def downgrade() -> None:
    op.drop_column("edit_plan_segments", "text_overlay")
    op.drop_column("edit_plan_segments", "end_ts")
    op.drop_column("edit_plan_segments", "start_ts")
