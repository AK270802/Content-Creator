"""Add vision columns to scenes

Revision ID: 002
Revises: 001
Create Date: 2026-08-23
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("scenes", sa.Column("description", sa.Text, nullable=True))
    op.add_column("scenes", sa.Column("visual_tags", JSONB, nullable=True))
    op.add_column("scenes", sa.Column("quality_flags", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("scenes", "quality_flags")
    op.drop_column("scenes", "visual_tags")
    op.drop_column("scenes", "description")
