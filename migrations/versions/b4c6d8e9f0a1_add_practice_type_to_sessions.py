"""add practice_type to app_practice_sessions

Revision ID: b4c6d8e9f0a1
Revises: a3b5c6d7e8f9
Create Date: 2026-09-23 10:00:00.000000

Тип практики для мультипрактичности: asana (существующие), meditation, pranayama.
Существующие записи получат 'asana' — обратная совместимость без правок чтения.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4c6d8e9f0a1'
down_revision: Union[str, None] = 'a3b5c6d7e8f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('app_practice_sessions',
                  sa.Column('practice_type', sa.String(length=16),
                            nullable=False, server_default='asana'))


def downgrade() -> None:
    op.drop_column('app_practice_sessions', 'practice_type')