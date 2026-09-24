"""add cycles to app_practice_sessions

Revision ID: e2c4d6a8f0b2
Revises: b4c6d8e9f0a1
Create Date: 2026-09-24 12:00:00.000000

Количество циклов/упражнений для практик таймер-ботов (asanas/pranayama).
Обязательное поле с дефолтом 0 для существующих записей.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2c4d6a8f0b2'
down_revision: Union[str, None] = 'b4c6d8e9f0a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('app_practice_sessions',
                  sa.Column('cycles', sa.Integer(),
                            nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('app_practice_sessions', 'cycles')