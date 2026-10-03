"""Add export preset fields, brand kits, generated thumbnails, video poster.

Revision ID: 010
Revises: 009
Create Date: 2026-09-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Video poster key for dashboard
    op.add_column("videos", sa.Column("thumbnail_key", sa.String(1024), nullable=True))

    # Render job export options
    op.add_column("render_jobs", sa.Column("preset_id", sa.String(64), nullable=True))
    op.add_column("render_jobs", sa.Column("aspect_ratio", sa.String(16), nullable=True))
    op.add_column("render_jobs", sa.Column("burn_captions", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("render_jobs", sa.Column("brand_kit_id", UUID(as_uuid=True), nullable=True))
    op.add_column("render_jobs", sa.Column("options", JSONB(), nullable=True))

    op.create_table(
        "brand_kits",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.String(255), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("primary_color", sa.String(32), nullable=True),
        sa.Column("secondary_color", sa.String(32), nullable=True),
        sa.Column("accent_color", sa.String(32), nullable=True),
        sa.Column("font_family", sa.String(128), nullable=True),
        sa.Column("logo_key", sa.String(1024), nullable=True),
        sa.Column("watermark_key", sa.String(1024), nullable=True),
        sa.Column("caption_preset", sa.String(64), nullable=True),
        sa.Column("extras", JSONB(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "generated_thumbnails",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("video_id", UUID(as_uuid=True), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("variant", sa.String(32), nullable=False, server_default="ai"),
        sa.Column("timestamp", sa.Float(), nullable=True),
        sa.Column("label", sa.String(255), nullable=True),
        sa.Column("meta", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("generated_thumbnails")
    op.drop_table("brand_kits")
    op.drop_column("render_jobs", "options")
    op.drop_column("render_jobs", "brand_kit_id")
    op.drop_column("render_jobs", "burn_captions")
    op.drop_column("render_jobs", "aspect_ratio")
    op.drop_column("render_jobs", "preset_id")
    op.drop_column("videos", "thumbnail_key")
