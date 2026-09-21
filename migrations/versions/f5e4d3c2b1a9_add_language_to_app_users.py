"""add language to app_users

Revision ID: f5e4d3c2b1a9
Revises: d1f2a3b4c5d6
Create Date: 2026-09-21 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f5e4d3c2b1a9'
down_revision: Union[str, None] = 'd1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('app_users',
                  sa.Column('language', sa.String(length=5),
                            nullable=False, server_default='ru'))


def downgrade() -> None:
    op.drop_column('app_users', 'language')