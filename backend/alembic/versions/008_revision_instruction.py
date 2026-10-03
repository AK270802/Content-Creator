"""Add revision_instruction to edit_plans

Revision ID: 002
Revises: 001
Create Date: 2026-09-16
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "edit_plans",
        sa.Column("revision_instruction", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("edit_plans", "revision_instruction")
