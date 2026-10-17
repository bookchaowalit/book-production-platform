"""Add V1B render output registration."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_render_outputs"
down_revision = "0001_v1_core"
branch_labels = None
depends_on = None


def uuid_column() -> sa.types.TypeEngine:
    return sa.Uuid(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "render_outputs",
        sa.Column("id", uuid_column(), nullable=False),
        sa.Column("project_id", uuid_column(), nullable=False),
        sa.Column("edl_id", uuid_column(), nullable=False),
        sa.Column("job_id", uuid_column(), nullable=True),
        sa.Column("storage_key", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["edl_id"], ["edls.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["processing_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("render_outputs")
