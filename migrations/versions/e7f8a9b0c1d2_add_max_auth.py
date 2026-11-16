"""add MAX login: max_id on app_users and pending MAX auth sessions

Revision ID: e7f8a9b0c1d2
Revises: d2e3f4a5b6c7
Create Date: 2026-11-16 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7f8a9b0c1d2'
down_revision: Union[str, None] = 'd2e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('app_users', sa.Column('max_id', sa.BigInteger(), nullable=True))
    op.create_index(
        'ix_app_users_max_id', 'app_users', ['max_id'], unique=True
    )

    op.create_table(
        'app_pending_max_auth',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('max_id', sa.BigInteger(), nullable=True),
        sa.Column('max_name', sa.String(length=255), nullable=True),
        sa.Column('max_username', sa.String(length=100), nullable=True),
        sa.Column('max_avatar_url', sa.String(length=500), nullable=True),
        sa.Column('max_language', sa.String(length=10), nullable=True),
        sa.Column('consent_version', sa.String(length=20), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('confirmed_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_app_pending_max_auth_id', 'app_pending_max_auth', ['id'], unique=False
    )
    op.create_index(
        'ix_app_pending_max_auth_session_id',
        'app_pending_max_auth',
        ['session_id'],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index('ix_app_pending_max_auth_session_id', table_name='app_pending_max_auth')
    op.drop_index('ix_app_pending_max_auth_id', table_name='app_pending_max_auth')
    op.drop_table('app_pending_max_auth')
    op.drop_index('ix_app_users_max_id', table_name='app_users')
    op.drop_column('app_users', 'max_id')
