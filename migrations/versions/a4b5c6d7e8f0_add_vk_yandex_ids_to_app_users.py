"""add vk_id and yandex_id to app_users

Revision ID: a4b5c6d7e8f0
Revises: e2c4d6a8f0b2
Create Date: 2026-10-04 01:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4b5c6d7e8f0'
down_revision: Union[str, None] = 'e2c4d6a8f0b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('app_users', sa.Column('vk_id', sa.String(length=255), nullable=True))
    op.create_index('ix_app_users_vk_id', 'app_users', ['vk_id'], unique=True)
    op.add_column('app_users', sa.Column('yandex_id', sa.String(length=255), nullable=True))
    op.create_index('ix_app_users_yandex_id', 'app_users', ['yandex_id'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_app_users_yandex_id', table_name='app_users')
    op.drop_column('app_users', 'yandex_id')
    op.drop_index('ix_app_users_vk_id', table_name='app_users')
    op.drop_column('app_users', 'vk_id')