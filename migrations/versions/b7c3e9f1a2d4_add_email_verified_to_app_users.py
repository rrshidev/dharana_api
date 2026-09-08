"""add email_verified to app_users

Revision ID: b7c3e9f1a2d4
Revises: a1b2c3d4e5f6
Create Date: 2026-09-08 22:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c3e9f1a2d4'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('app_users', sa.Column('email_verified', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('app_users', sa.Column('email_verify_sent_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('app_users', 'email_verify_sent_at')
    op.drop_column('app_users', 'email_verified')