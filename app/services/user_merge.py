"""Слияние дублирующихся учётных записей пользователя.

Переносит всё принадлежащее `source` (подписку, избранное, практики, комплексы,
аватары, платежи, доставки рассылок, статистику и пустые поля профиля) в
`target` и подготавливает `source` к удалению. Вызывается до `db.delete(source)`.

Уникальные поля (email, username, telegram_id, google_id, vk_id, yandex_id)
переходят к `target`, только если у него они пусты; иначе остаются у `target`,
а у `source` чистятся — чтобы не наткнуться на UNIQUE при удалении.

Вход email+пароль не должен «умирать»: если у `target` пароля нет, а у `source`
есть — пароль переезжает, а email-дверью становится почта `source` (та, что
юзер вводил при входе). `is_admin`/`is_banned` тоже переносятся — админ не
потеряет права при слиянии своих учёток, а бан нельзя сбросить слиянием.

Все изменения — в одной транзакции вызывающего (коммит делает роут).
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    User,
    UserAvatar,
    Favorite,
    PracticeSession,
    Sequence,
    UserSubscription,
    Payment,
    BroadcastDelivery,
    PendingTelegramAuth,
)

# Поля внешних идентификаторов, на которых стоят UNIQUE-ограничения.
_UNIQUE_FIELDS = ("email", "username", "telegram_id", "google_id", "vk_id", "yandex_id")

# Текстовые/настроечные поля профиля: заполняем у target только пустые.
_PROFILE_FIELDS = (
    "name", "last_name", "avatar_url", "bio", "timezone",
    "language", "timer_language", "daily_asana_time", "last_daily_asana_date",
)


def _better_subscription(a: UserSubscription, b: UserSubscription) -> UserSubscription:
    """Что оставить: платная > бесплатной, при равенстве — более поздний конец."""
    if a.is_premium != b.is_premium:
        return a if a.is_premium else b
    a_end, b_end = a.subscription_end, b.subscription_end
    if a_end and b_end and a_end != b_end:
        return a if a_end > b_end else b
    if a_end and not b_end:
        return a
    if b_end and not a_end:
        return b
    return b


async def merge_users(db: AsyncSession, source: User, target: User) -> dict:
    """Переносит все данные source → target. Возвращает сводку для логов."""
    if source.id == target.id:
        raise ValueError("Cannot merge user with itself")

    summary = {
        "favorites": 0,
        "practice_sessions": 0,
        "sequences": 0,
        "avatars": 0,
        "payments": 0,
        "deliveries": 0,
        "pending": 0,
        "subscription": "none",
        "stats": False,
        "profile": [],
        "moved_ids": [],
        "cleared": [],
    }

    # Избранное: переносим только то, чего ещё нет у target (дубликаты удаляем).
    target_names = set((await db.execute(
        select(Favorite.asana_name).where(Favorite.user_id == target.id)
    )).scalars().all())
    src_favorites = (await db.execute(
        select(Favorite).where(Favorite.user_id == source.id)
    )).scalars().all()
    for fav in src_favorites:
        if fav.asana_name in target_names:
            await db.delete(fav)
            continue
        fav.user_id = target.id
        target_names.add(fav.asana_name)
        summary["favorites"] += 1

    # Практики, комплексы, платежи, доставки рассылок, ожидающие telegram-авторизации.
    for table, key, total_key in (
        (PracticeSession, PracticeSession.user_id, "practice_sessions"),
        (Sequence, Sequence.user_id, "sequences"),
        (Payment, Payment.user_id, "payments"),
        (BroadcastDelivery, BroadcastDelivery.user_id, "deliveries"),
        (PendingTelegramAuth, PendingTelegramAuth.user_id, "pending"),
    ):
        result = await db.execute(
            table.__table__.update().where(key == source.id).values(user_id=target.id)
        )
        summary[total_key] = result.rowcount or 0

    # Аватары: переносим, оставляя ровно один primary у target.
    src_avatars = (await db.execute(
        select(UserAvatar).where(UserAvatar.user_id == source.id)
    )).scalars().all()
    if src_avatars:
        target_has_primary = bool((await db.execute(
            select(UserAvatar.id).where(
                UserAvatar.user_id == target.id, UserAvatar.is_primary == True
            ).limit(1)
        )).scalar_one_or_none())
        await db.execute(
            UserAvatar.__table__.update().where(UserAvatar.user_id == source.id).values(
                user_id=target.id, is_primary=False
            )
        )
        if not target_has_primary:
            await db.execute(
                UserAvatar.__table__.update().where(UserAvatar.id == src_avatars[0].id).values(
                    is_primary=True
                )
            )
        summary["avatars"] = len(src_avatars)

    # Подписка: на пользователя одна строка (UNIQUE). Оставляем «лучшую».
    src_sub = (await db.execute(
        select(UserSubscription).where(UserSubscription.user_id == source.id)
    )).scalar_one_or_none()
    if src_sub:
        tgt_sub = (await db.execute(
            select(UserSubscription).where(UserSubscription.user_id == target.id)
        )).scalar_one_or_none()
        if tgt_sub is None:
            src_sub.user_id = target.id
            summary["subscription"] = "moved"
        else:
            keep = _better_subscription(src_sub, tgt_sub)
            drop = tgt_sub if keep is src_sub else src_sub
            await db.delete(drop)
            if keep is src_sub:
                # Удалить старую строку ДО переназначения — иначе UNIQUE collision.
                await db.flush()
                src_sub.user_id = target.id
            summary["subscription"] = "merged"

    # Вход email+пароль не должен «умирать»: если у target пароля не было, а
    # у source был — забираем пароль и держим email-дверь такой, какой юзер её
    # знал (email source становится почтой для входа, если у target своей нет
    # или она была без пароля). До общего цикла — чтобы освободить source.email.
    if source.hashed_password and not target.hashed_password:
        target.hashed_password = source.hashed_password
        summary["profile"].append("hashed_password")
        if source.email and target.email != source.email:
            adopted_email = source.email
            source.email = None
            await db.flush()
            target.email = adopted_email
            target.email_verified = bool(target.email_verified or source.email_verified)
            summary["moved_ids"].append("email")

    # Уникальные поля: сначала освобождаем занятые source, потом занимаем target.
    moves = {}
    for field in _UNIQUE_FIELDS:
        src_val = getattr(source, field)
        if src_val is None:
            continue
        tgt_val = getattr(target, field)
        if tgt_val is None:
            moves[field] = src_val
            setattr(source, field, None)
        elif tgt_val != src_val:
            setattr(source, field, None)
            summary["cleared"].append(field)
    if moves:
        # Освобождение у source уже сохранено флашом ниже — без UNIQUE-конфликта.
        await db.flush()
        for field, value in moves.items():
            setattr(target, field, value)
            summary["moved_ids"].append(field)
        if "email" in moves:
            target.email_verified = bool(target.email_verified or source.email_verified)

    # Статистика практик: суммируем, рекорды берём по максимуму.
    target.total_practices = (target.total_practices or 0) + (source.total_practices or 0)
    target.total_practice_minutes = (target.total_practice_minutes or 0) + (source.total_practice_minutes or 0)
    target.total_practice_days = (target.total_practice_days or 0) + (source.total_practice_days or 0)
    target.streak_days = max(target.streak_days or 0, source.streak_days or 0)
    target.longest_streak = max(target.longest_streak or 0, source.longest_streak or 0)
    target.current_streak = max(target.current_streak or 0, source.current_streak or 0)
    if source.last_practice_at and (
        target.last_practice_at is None or source.last_practice_at > target.last_practice_at
    ):
        target.last_practice_at = source.last_practice_at
    summary["stats"] = True

    # Профиль: заполняем пустые поля целевого пользователя.
    for field in _PROFILE_FIELDS:
        if getattr(target, field) is None and getattr(source, field) is not None:
            setattr(target, field, getattr(source, field))
            summary["profile"].append(field)

    # Права и статусы: админ не должен «умирать» при слиянии его учёток,
    # а бан — переживать слияние (нельзя сбросить бан, слившись с другим).
    if source.is_admin and not target.is_admin:
        target.is_admin = True
        summary["profile"].append("is_admin")
    if source.is_banned and not target.is_banned:
        target.is_banned = True
        summary["profile"].append("is_banned")

    return summary