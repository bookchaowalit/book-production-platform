"""Add per-project transcription quality selection."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_transcription_quality"
down_revision = "0002_render_outputs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("transcription_quality", sa.String(length=20), server_default="fast", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("projects", "transcription_quality")
