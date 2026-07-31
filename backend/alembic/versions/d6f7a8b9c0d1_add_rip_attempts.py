"""Add rip_attempts table for per-drive health reporting

Revision ID: d6f7a8b9c0d1
Revises: c5e6f7a8b9c0
Create Date: 2026-08-01 09:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d6f7a8b9c0d1"
down_revision: Union[str, None] = "c5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No FK on drive_id/job_id: attempts are diagnostic history and should
    # outlive a deleted job or a drive that has been unplugged for good.
    op.create_table(
        "rip_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("drive_id", sa.Text(), nullable=True),
        sa.Column("job_id", sa.Text(), nullable=True),
        sa.Column("track_num", sa.Integer(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_rip_attempts_drive_created",
        "rip_attempts",
        ["drive_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_rip_attempts_drive_created", table_name="rip_attempts")
    op.drop_table("rip_attempts")
