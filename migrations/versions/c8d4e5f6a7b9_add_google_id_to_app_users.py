"""add google_id to app_users

Revision ID: c8d4e5f6a7b9
Revises: b7c3e9f1a2d4
Create Date: 2026-09-09 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8d4e5f6a7b9'
down_revision: Union[str, None] = 'b7c3e9f1a2d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('app_users', sa.Column('google_id', sa.String(length=255), nullable=True))
    op.create_index('ix_app_users_google_id', 'app_users', ['google_id'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_app_users_google_id', table_name='app_users')
    op.drop_column('app_users', 'google_id')