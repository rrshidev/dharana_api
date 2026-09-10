"""add password_reset_sent_at to app_users

Revision ID: d1f2a3b4c5d6
Revises: c8d4e5f6a7b9
Create Date: 2026-09-10 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1f2a3b4c5d6'
down_revision: Union[str, None] = 'c8d4e5f6a7b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('app_users', sa.Column('password_reset_sent_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('app_users', 'password_reset_sent_at')