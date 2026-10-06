"""Слияние дублирующихся учётных записей.

Ключевой регресс: привязка Telegram к уже залогиненному пользователю, когда у
telegram-учётки есть подписка, ранее падала с IntegrityError
(user_id в app_user_subscriptions NOT NULL занулялся ORM при удалении source).
"""

from datetime import datetime, timedelta

import pytest


@pytest.fixture
async def client():
    from httpx import ASGITransport, AsyncClient

    from app.database import Base, engine
    from app.main import app

    await engine.dispose()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed_user(**fields):
    """Создаёт пользователя напрямую в БД, возвращает id."""
    from app.database import async_session
    from app.models.models import User

    async with async_session() as session:
        user = User(**fields)
        session.add(user)
        await session.commit()
        return user.id


async def _seed_pending(code: str, telegram_id: int, **fields):
    from app.database import async_session
    from app.models.models import PendingTelegramAuth

    async with async_session() as session:
        session.add(PendingTelegramAuth(code=code, telegram_id=telegram_id, **fields))
        await session.commit()


async def _get_user(uid: int):
    from sqlalchemy import select

    from app.database import async_session
    from app.models.models import User, UserSubscription

    async with async_session() as session:
        user = (await session.execute(select(User).where(User.id == uid))).scalar_one_or_none()
        sub = (await session.execute(
            select(UserSubscription).where(UserSubscription.user_id == uid)
        )).scalar_one_or_none()
        return user, sub


async def test_telegram_verify_merges_subscription(client):
    """Регресс: merge с подпиской у telegram-учётки не роняет NOT NULL."""
    from app.services.auth_service import create_access_token

    target_id = await _seed_user(name="Main", email="main@test.ru", email_verified=True)
    source_id = await _seed_user(name="TG user", telegram_id=123456, username="tg_user")
    await _seed_pending(
        "777777", 123456,
        telegram_name="TG user", telegram_username="tg_user",
    )

    from app.database import async_session
    from app.models.models import UserSubscription

    async with async_session() as s:
        s.add(UserSubscription(
            user_id=source_id,
            is_premium=True,
            subscription_type="premium",
            subscription_end=datetime.utcnow() + timedelta(days=30),
        ))
        await s.commit()

    token = create_access_token(target_id)
    r = await client.post(
        "/api/v1/auth/telegram/verify",
        json={"code": "777777"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["user"]["telegram_id"] == 123456

    source_user, _ = await _get_user(source_id)
    assert source_user is None  # source удалён

    target_user, target_sub = await _get_user(target_id)
    assert target_user.telegram_id == 123456
    assert target_sub is not None
    assert target_sub.user_id == target_id
    assert target_sub.is_premium is True


async def test_telegram_verify_merges_favorites_and_stats(client):
    from app.services.auth_service import create_access_token

    target_id = await _seed_user(name="Main", email="main@test.ru")
    source_id = await _seed_user(
        name="TG user", telegram_id=555, username="tg_user",
        total_practices=7, total_practice_minutes=300, total_practice_days=4,
        longest_streak=9,
    )
    await _seed_pending("111111", 555, telegram_username="tg_user")

    from app.database import async_session
    from app.models.models import Favorite, PracticeSession

    async with async_session() as s:
        s.add(Favorite(user_id=source_id, asana_name="Тадасана"))
        s.add(Favorite(user_id=source_id, asana_name="Собака"))
        s.add(Favorite(user_id=target_id, asana_name="Собака"))  # дубликат
        s.add(PracticeSession(user_id=source_id, practice_type="asana", total_duration_seconds=600))
        await s.commit()

    token = create_access_token(target_id)
    r = await client.post(
        "/api/v1/auth/telegram/verify",
        json={"code": "111111"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text

    from sqlalchemy import func, select

    from app.database import async_session as s2
    from app.models.models import Favorite, PracticeSession, User

    async with s2() as s:
        target = (await s.execute(select(User).where(User.id == target_id))).scalar_one()
        fav_names = set((await s.execute(
            select(Favorite.asana_name).where(Favorite.user_id == target_id)
        )).scalars().all())
        practice_count = (await s.execute(
            select(func.count()).select_from(PracticeSession).where(PracticeSession.user_id == target_id)
        )).scalar()
        source = (await s.execute(select(User).where(User.id == source_id))).scalar_one_or_none()

    assert fav_names == {"Тадасана", "Собака"}  # дубликат удалён
    assert practice_count == 1
    assert source is None
    assert target.total_practices == 7
    assert target.total_practice_minutes == 300
    assert target.total_practice_days == 4
    assert target.longest_streak == 9
    assert target.name == "Main"  # целевой профиль не перезаписан


async def test_telegram_verify_keeps_target_identity(client):
    """Когда у target уже занят username, source поля не перезаписывают его,
    а его конфликтное имя чистится. Telegram переезжает к target."""
    from app.services.auth_service import create_access_token

    target_id = await _seed_user(name="Main", email="main@test.ru", username="main_user")
    source_id = await _seed_user(telegram_id=999, username="tg_user")
    await _seed_pending("222222", 999, telegram_username="tg_user")

    token = create_access_token(target_id)
    r = await client.post(
        "/api/v1/auth/telegram/verify",
        json={"code": "222222"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text

    target_user, _ = await _get_user(target_id)
    source_user, _ = await _get_user(source_id)
    assert source_user is None
    assert target_user.telegram_id == 999
    assert target_user.username == "main_user"  # target не потерял своё


async def test_telegram_verify_premium_wins_over_free(client):
    """Слияние подписок: платная у source побеждает бесплатную у target."""
    from app.services.auth_service import create_access_token

    target_id = await _seed_user(name="Main", email="main@test.ru")
    source_id = await _seed_user(telegram_id=111)
    await _seed_pending("333333", 111)

    from app.database import async_session
    from app.models.models import UserSubscription

    async with async_session() as s:
        s.add(UserSubscription(user_id=target_id, is_premium=False, subscription_type="free"))
        s.add(UserSubscription(
            user_id=source_id,
            is_premium=True,
            subscription_type="premium",
            subscription_end=datetime.utcnow() + timedelta(days=365),
        ))
        await s.commit()

    token = create_access_token(target_id)
    r = await client.post(
        "/api/v1/auth/telegram/verify",
        json={"code": "333333"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text

    from sqlalchemy import select

    from app.database import async_session as s2
    from app.models.models import User, UserSubscription

    async with s2() as s:
        subs = (await s.execute(
            select(UserSubscription).where(UserSubscription.user_id == target_id)
        )).scalars().all()
        source = (await s.execute(
            select(User).where(User.id == source_id)
        )).scalar_one_or_none()

    assert source is None
    assert len(subs) == 1  # осталась одна подписка
    assert subs[0].is_premium is True  # платная сохранилась
    assert subs[0].subscription_end is not None