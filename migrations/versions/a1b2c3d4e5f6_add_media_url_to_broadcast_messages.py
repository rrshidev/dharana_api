"""add media_url to broadcast messages

Revision ID: a1b2c3d4e5f6
Revises: 86a96f7d21f6
Create Date: 2026-09-05 21:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '86a96f7d21f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('app_broadcast_messages', sa.Column('media_url', sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column('app_broadcast_messages', 'media_url')