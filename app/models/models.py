from datetime import datetime, date, time
from typing import Optional

from sqlalchemy import (
    Column, Integer, String, Time, Boolean, DateTime, Date, Text, ForeignKey,
    Float, JSON,
)
from sqlalchemy.orm import relationship

from app.database import Base


class User(Base):
    __tablename__ = "app_users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=True, index=True)
    telegram_id = Column(Integer, unique=True, nullable=True, index=True)
    hashed_password = Column(String(255), nullable=True)
    name = Column(String(255), nullable=True)
    username = Column(String(100), unique=True, nullable=True, index=True)
    bio = Column(Text, nullable=True)
    avatar_url = Column(String(500), nullable=True)

    total_practice_minutes = Column(Integer, default=0)
    total_practice_days = Column(Integer, default=0)
    last_practice_at = Column(DateTime, nullable=True)
    current_streak = Column(Integer, default=0)
    longest_streak = Column(Integer, default=0)

    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    is_banned = Column(Boolean, default=False)
    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    subscription = relationship("UserSubscription", back_populates="user", uselist=False)
    avatars = relationship("UserAvatar", back_populates="user", cascade="all, delete-orphan")
    favorites = relationship("Favorite", back_populates="user", cascade="all, delete-orphan")
    practice_sessions = relationship("PracticeSession", back_populates="user", cascade="all, delete-orphan")
    sequences = relationship("Sequence", back_populates="user", cascade="all, delete-orphan")


class UserAvatar(Base):
    __tablename__ = "app_user_avatars"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("app_users.id"), nullable=False)
    url = Column(String(500), nullable=False)
    is_primary = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="avatars")


class Favorite(Base):
    __tablename__ = "app_favorites"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("app_users.id"), nullable=False)
    asana_name = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="favorites")


class PracticeSession(Base):
    __tablename__ = "app_practice_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("app_users.id"), nullable=False)
    sequence_id = Column(Integer, ForeignKey("app_sequences.id"), nullable=True)

    status = Column(String(20), default="active")  # active, completed, cancelled
    asanas_practiced = Column(JSON, default=list)
    total_duration_seconds = Column(Integer, default=0)
    asana_durations = Column(JSON, default=dict)  # {asana_name: seconds}
    rest_seconds = Column(Integer, default=15)

    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="practice_sessions")


class Sequence(Base):
    __tablename__ = "app_sequences"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("app_users.id"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    asanas = Column(JSON, nullable=False, default=list)  # [{name, duration_seconds, rest_seconds}]
    is_public = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="sequences")


class Video(Base):
    __tablename__ = "app_videos"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255), nullable=False)
    filepath = Column(String(500), nullable=False)  # e.g. catalog/tree_pose.mp4
    video_type = Column(String(20), nullable=False)  # "asana" or "sequence"
    is_premium = Column(Boolean, default=False)
    asana_id = Column(Integer, nullable=True)  # FK to asana if type=asana
    asana_name = Column(String(255), nullable=True)  # denormalized for quick lookup
    sequence_name = Column(String(255), nullable=True)  # for type=sequence
    duration_seconds = Column(Integer, nullable=True)
    thumbnail_path = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class UserSubscription(Base):
    __tablename__ = "app_user_subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("app_users.id"), unique=True, nullable=False)

    is_premium = Column(Boolean, default=False)
    subscription_type = Column(String(20), default=None)
    subscription_status = Column(String(20), default=None)

    subscription_start = Column(DateTime, default=None)
    subscription_end = Column(DateTime, default=None)
    trial_used = Column(Boolean, default=False)
    trial_start = Column(DateTime, default=None)
    trial_end = Column(DateTime, default=None)

    daily_generations_used = Column(Integer, default=0)
    last_generation_date = Column(Date, default=None)

    payment_id = Column(String(100), default=None)
    payment_provider = Column(String(50), default=None)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="subscription")


class PendingTelegramAuth(Base):
    __tablename__ = "app_pending_telegram_auth"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(10), unique=True, nullable=False, index=True)
    telegram_id = Column(Integer, nullable=False)
    telegram_name = Column(String(255), nullable=True)
    telegram_username = Column(String(100), nullable=True)
    user_id = Column(Integer, nullable=True)  # filled when user confirmed
    created_at = Column(DateTime, default=datetime.utcnow)
    confirmed = Column(Boolean, default=False)


class Payment(Base):
    """Ручной платёж (наличные/банковский перевод) с чеком об оплате."""
    __tablename__ = "app_payments"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("app_users.id"), nullable=True)
    telegram_id = Column(Integer, nullable=True)  # заполняется при оплате через бота
    user_name = Column(String(255), nullable=True)
    contact = Column(String(500), nullable=True)  # email или tg

    source = Column(String(20), default="app")  # app | bot
    payment_method = Column(String(100), nullable=True)  # выбранная карта/банк
    amount = Column(Float, nullable=True)

    receipt_url = Column(String(500), nullable=True)  # /uploads/receipts/...
    status = Column(String(20), default="pending")  # pending | confirmed | rejected
    premium_days = Column(Integer, default=30)

    reviewed_by = Column(Integer, nullable=True)  # admin user_id
    reviewed_at = Column(DateTime, nullable=True)
    notified = Column(Boolean, default=False)  # клиент уведомлён через бота
    admin_sent = Column(Boolean, default=False)  # чек доставлен админу в бот (фото+кнопки)
    client_seen = Column(Boolean, default=False)  # клиент видел уведомление в приложении

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BroadcastMessage(Base):
    """Рассылка админа (текст + настройки аудитории/каналов)."""
    __tablename__ = "app_broadcast_messages"

    id = Column(Integer, primary_key=True, index=True)
    message = Column(Text, nullable=False)
    audience_free = Column(Boolean, default=False)      # кому: бесплатные
    audience_premium = Column(Boolean, default=False)   # кому: платные
    channel_telegram = Column(Boolean, default=False)   # куда: телеграм
    channel_app = Column(Boolean, default=False)        # куда: приложение
    author_id = Column(Integer, nullable=True)          # admin user_id
    total_recipients = Column(Integer, default=0)

    telegram_pending = Column(Integer, default=0)
    telegram_sent = Column(Integer, default=0)
    telegram_failed = Column(Integer, default=0)
    app_pending = Column(Integer, default=0)
    app_read = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow)


class BroadcastDelivery(Base):
    """Одна строка доставки рассылки конкретному пользователю по каналу."""
    __tablename__ = "app_broadcast_deliveries"

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("app_broadcast_messages.id"), nullable=False, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    telegram_id = Column(Integer, nullable=True)  # только для канала telegram
    channel = Column(String(20), nullable=False)  # telegram | app

    # telegram: pending -> sent | failed
    # app:      pending -> read
    status = Column(String(20), default="pending")
    error = Column(String(500), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    sent_at = Column(DateTime, nullable=True)
