"""add consent_at and consent_version to app_users

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-11-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('app_users', sa.Column('consent_at', sa.DateTime(), nullable=True))
    op.add_column('app_users', sa.Column('consent_version', sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column('app_users', 'consent_version')
    op.drop_column('app_users', 'consent_at')
