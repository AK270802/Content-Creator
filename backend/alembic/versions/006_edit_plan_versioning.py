"""006 edit plan versioning

Revision ID: 006
Revises: 005
Create Date: 2026-08-23
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("edit_plans", sa.Column("version", sa.Integer, nullable=False, server_default="1"))
    op.add_column(
        "edit_plans",
        sa.Column(
            "parent_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("edit_plans.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_edit_plans_video_id_version", "edit_plans", ["video_id", "version"])


def downgrade() -> None:
    op.drop_index("ix_edit_plans_video_id_version", table_name="edit_plans")
    op.drop_column("edit_plans", "parent_version_id")
    op.drop_column("edit_plans", "version")
