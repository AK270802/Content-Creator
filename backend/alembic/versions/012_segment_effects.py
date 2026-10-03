"""Segment visual effects columns.

Revision ID: 012
Revises: 011
"""
from alembic import op
import sqlalchemy as sa

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("edit_plan_segments", sa.Column("brightness", sa.Float(), nullable=True))
    op.add_column("edit_plan_segments", sa.Column("contrast", sa.Float(), nullable=True))
    op.add_column("edit_plan_segments", sa.Column("saturation", sa.Float(), nullable=True))
    op.add_column("edit_plan_segments", sa.Column("fade_in", sa.Float(), nullable=True))
    op.add_column("edit_plan_segments", sa.Column("fade_out", sa.Float(), nullable=True))
    op.add_column("edit_plan_segments", sa.Column("effect", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("edit_plan_segments", "effect")
    op.drop_column("edit_plan_segments", "fade_out")
    op.drop_column("edit_plan_segments", "fade_in")
    op.drop_column("edit_plan_segments", "saturation")
    op.drop_column("edit_plan_segments", "contrast")
    op.drop_column("edit_plan_segments", "brightness")
