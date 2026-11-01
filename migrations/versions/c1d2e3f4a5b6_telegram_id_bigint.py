"""telegram_id -> BIGINT (Telegram IDs вышли за пределы int32)

Revision ID: c1d2e3f4a5b6
Revises: b0c1d2e3f4a5
Create Date: 2026-10-31 12:00:00.000000

22.10.2026 пользователь с telegram_id > 2 147 483 647 не смог войти:
`value out of int32 range`. Переводим все telegram_id на BIGINT.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "b0c1d2e3f4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (table, column, index to restore after SQLite table rebuild)
COLUMNS = [
    ("app_users", "telegram_id", "ix_app_users_telegram_id"),
    ("daily_asana_logs", "telegram_id", "ix_daily_asana_logs_telegram_id"),
    ("app_pending_telegram_auth", "telegram_id", None),
    ("app_payments", "telegram_id", None),
    ("app_broadcast_deliveries", "telegram_id", None),
]


def _alter(to_type: sa.types.TypeEngine) -> None:
    bind = op.get_bind()
    for table, column, index_name in COLUMNS:
        inspector = sa.inspect(bind)
        if not inspector.has_table(table):
            continue
        if column not in {c["name"] for c in inspector.get_columns(table)}:
            continue

        with op.batch_alter_table(table) as batch:
            batch.alter_column(column, existing_type=sa.Integer(), type_=to_type)

        # На SQLite batch_alter_table пересоздаёт таблицу и теряет индексы.
        if index_name:
            inspector = sa.inspect(bind)
            if index_name not in {i["name"] for i in inspector.get_indexes(table)}:
                op.create_index(index_name, table, [column], unique=True)


def upgrade() -> None:
    _alter(sa.BigInteger())


def downgrade() -> None:
    _alter(sa.Integer())
