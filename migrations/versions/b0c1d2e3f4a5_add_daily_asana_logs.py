"""add daily_asana_logs (mirror of bot-written table)

Revision ID: b0c1d2e3f4a5
Revises: a9f8e7d6c5b4
Create Date: 2026-10-08 12:00:00.000000

Таблицу daily_asana_logs в проде уже создал Telegram-бот (create_all),
поэтому create_table выполняется только если таблицы ещё нет —
иначе alembic upgrade упал бы на существующей таблице.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b0c1d2e3f4a5'
down_revision: Union[str, None] = 'a9f8e7d6c5b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("daily_asana_logs"):
        return
    op.create_table(
        "daily_asana_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.Integer(), nullable=False),
        sa.Column("asana_name", sa.String(length=500), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_daily_asana_logs_telegram_id",
        "daily_asana_logs",
        ["telegram_id"],
        unique=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("daily_asana_logs"):
        return
    op.drop_index("ix_daily_asana_logs_telegram_id", table_name="daily_asana_logs")
    op.drop_table("daily_asana_logs")
