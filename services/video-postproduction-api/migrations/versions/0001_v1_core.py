"""Create the V1A operational tables."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_v1_core"
down_revision = None
branch_labels = None
depends_on = None


def uuid_column() -> sa.types.TypeEngine:
    return sa.Uuid(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", uuid_column(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "media_assets",
        sa.Column("id", uuid_column(), nullable=False),
        sa.Column("project_id", uuid_column(), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index("ix_media_assets_project_id", "media_assets", ["project_id"])
    op.create_index("ix_media_assets_sha256", "media_assets", ["sha256"])
    op.create_table(
        "processing_jobs",
        sa.Column("id", uuid_column(), nullable=False),
        sa.Column("project_id", uuid_column(), nullable=False),
        sa.Column("asset_id", uuid_column(), nullable=False),
        sa.Column("kind", sa.String(length=60), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["asset_id"], ["media_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_processing_jobs_project_id", "processing_jobs", ["project_id"])
    op.create_index("ix_processing_jobs_asset_id", "processing_jobs", ["asset_id"])
    op.create_index("ix_processing_jobs_status", "processing_jobs", ["status"])
    op.create_table(
        "processing_stages",
        sa.Column("id", uuid_column(), nullable=False),
        sa.Column("job_id", uuid_column(), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["processing_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "name", name="uq_processing_stages_job_name"),
    )
    op.create_index("ix_processing_stages_job_id", "processing_stages", ["job_id"])
    op.create_index("ix_processing_stages_status", "processing_stages", ["status"])
    op.create_table(
        "transcripts",
        sa.Column("id", uuid_column(), nullable=False),
        sa.Column("project_id", uuid_column(), nullable=False),
        sa.Column("asset_id", uuid_column(), nullable=False),
        sa.Column("provider", sa.String(length=120), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("language", sa.String(length=20), nullable=True),
        sa.Column("duration", sa.Float(), nullable=True),
        sa.Column("normalized", sa.JSON(), nullable=False),
        sa.Column("content_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["media_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_id", "content_fingerprint", name="uq_transcripts_asset_fingerprint"),
    )
    op.create_index("ix_transcripts_project_id", "transcripts", ["project_id"])
    op.create_index("ix_transcripts_asset_id", "transcripts", ["asset_id"])
    op.create_table(
        "transcript_segments",
        sa.Column("id", uuid_column(), nullable=False),
        sa.Column("transcript_id", uuid_column(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("start", sa.Float(), nullable=False),
        sa.Column("end", sa.Float(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("words", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_transcript_segments_transcript_ordinal", "transcript_segments", ["transcript_id", "ordinal"])
    op.create_table(
        "edls",
        sa.Column("id", uuid_column(), nullable=False),
        sa.Column("project_id", uuid_column(), nullable=False),
        sa.Column("source_asset_id", uuid_column(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=40), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_asset_id"], ["media_assets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_edls_project_id", "edls", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_edls_project_id", table_name="edls")
    op.drop_table("edls")
    op.drop_index("ix_transcript_segments_transcript_ordinal", table_name="transcript_segments")
    op.drop_table("transcript_segments")
    op.drop_index("ix_transcripts_asset_id", table_name="transcripts")
    op.drop_index("ix_transcripts_project_id", table_name="transcripts")
    op.drop_table("transcripts")
    op.drop_index("ix_processing_stages_status", table_name="processing_stages")
    op.drop_index("ix_processing_stages_job_id", table_name="processing_stages")
    op.drop_table("processing_stages")
    op.drop_index("ix_processing_jobs_status", table_name="processing_jobs")
    op.drop_index("ix_processing_jobs_asset_id", table_name="processing_jobs")
    op.drop_index("ix_processing_jobs_project_id", table_name="processing_jobs")
    op.drop_table("processing_jobs")
    op.drop_index("ix_media_assets_sha256", table_name="media_assets")
    op.drop_index("ix_media_assets_project_id", table_name="media_assets")
    op.drop_table("media_assets")
    op.drop_table("projects")
