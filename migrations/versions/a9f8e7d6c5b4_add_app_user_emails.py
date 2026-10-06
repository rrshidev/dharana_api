"""add app_user_emails (несколько почт-дверей у пользователя)

Revision ID: a9f8e7d6c5b4
Revises: a4b5c6d7e8f0
Create Date: 2026-10-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a9f8e7d6c5b4'
down_revision: Union[str, None] = 'a4b5c6d7e8f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'app_user_emails',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('app_users.id'), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('email_verified', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_app_user_emails_email', 'app_user_emails', ['email'], unique=True)
    op.create_index('ix_app_user_emails_user_id', 'app_user_emails', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_app_user_emails_user_id', table_name='app_user_emails')
    op.drop_index('ix_app_user_emails_email', table_name='app_user_emails')
    op.drop_table('app_user_emails')