"""Add gnudb_submissions table + persist disc offsets/leadout on jobs

Revision ID: c5e6f7a8b9c0
Revises: b2c3d4e5f6a7
Create Date: 2026-04-25 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c5e6f7a8b9c0"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # GnuDB submit needs offsets + leadout to build xmcd; persist them
    # so we can submit complete jobs whose disc is no longer in the drive.
    op.add_column("jobs", sa.Column("disc_offsets", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("disc_leadout", sa.Integer(), nullable=True))

    op.create_table(
        "gnudb_submissions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.Text(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("disc_id", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("submit_mode", sa.Text(), nullable=False),
        sa.Column("response_code", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("xmcd_body", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_gnudb_submissions_job_id", "gnudb_submissions", ["job_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_gnudb_submissions_job_id", table_name="gnudb_submissions")
    op.drop_table("gnudb_submissions")
    op.drop_column("jobs", "disc_leadout")
    op.drop_column("jobs", "disc_offsets")
