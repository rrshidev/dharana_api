"""add timer_language to app_users

Revision ID: a3b5c6d7e8f9
Revises: f5e4d3c2b1a9
Create Date: 2026-09-22 21:00:00.000000

Отдельная ячейка языка для таймер-бота (@timerasana_bot).
Пользователь может практиковать в любом из продуктов, но у каждого бота
свои языковые настройки: основной бот/приложение используют `language`,
таймер — `timer_language`.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3b5c6d7e8f9'
down_revision: Union[str, None] = 'f5e4d3c2b1a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('app_users',
                  sa.Column('timer_language', sa.String(length=5),
                            nullable=False, server_default='ru'))


def downgrade() -> None:
    op.drop_column('app_users', 'timer_language')