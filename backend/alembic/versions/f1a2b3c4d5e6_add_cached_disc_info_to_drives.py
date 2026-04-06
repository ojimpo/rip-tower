"""Add cached disc info to drives

Revision ID: f1a2b3c4d5e6
Revises: d4d4b49b2b88
Create Date: 2026-04-06 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = 'd4d4b49b2b88'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('drives', sa.Column('cached_disc_id', sa.Text(), nullable=True))
    op.add_column('drives', sa.Column('cached_artist', sa.Text(), nullable=True))
    op.add_column('drives', sa.Column('cached_album', sa.Text(), nullable=True))
    op.add_column('drives', sa.Column('cached_track_count', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('drives', 'cached_track_count')
    op.drop_column('drives', 'cached_album')
    op.drop_column('drives', 'cached_artist')
    op.drop_column('drives', 'cached_disc_id')
