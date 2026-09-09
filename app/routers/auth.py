import secrets
from datetime import datetime, timedelta

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.models import User, PendingTelegramAuth
from app.services.auth_service import (
    hash_password,
    verify_password,
    create_access_token,
    require_user,
    get_current_user,
    create_email_verify_token,
    decode_email_verify_claims,
)
from app.services.notify_service import notify_new_user
from app.services.telegram_avatar import fetch_telegram_avatar
from app.services.email_service import (
    send_verification_email_async,
    send_verification_email_now,
)
from app.services.google import decode_google_id_token

router = APIRouter(prefix="/auth", tags=["auth"])

# Одноразовые/мусорные почтовые домены (часть большей общедоступной базы).
DISPOSABLE_DOMAINS = {
    "10minutemail.com", "10minutemail.net", "0-mail.com", "0mks.com", "00peep.com",
    "antireg.ru", "binkmail.com", "boxthemail.net", "burnermail.io", "burneremail.net",
    "discard.email", "discardmail.com", "discardmail.de", "dispostable.com",
    "email2me.net", "emailnator.com", "emailondeck.com", "emltmp.com", "fexbox.com",
    "fakeinbox.com", "getnada.com", "guerrillamail.biz", "guerrillamail.com",
    "guerrillamail.net", "guerrillamail.org", "guerrillamail.info", "gustr.com",
    "inboxes.com", "instantmail.de", "jetable.fr", "just4spam.com", "kaback.de",
    "mail2nowhere.com", "mailbiz.biz", "mailcatch.com", "maildrop.cc", "mailforspam.com",
    "mailinator.com", "mailinator.net", "mailinator.org", "mailmetrash.com",
    "mailnesia.com", "mailtemp.net", "mailslite.com", "mintemail.com", "mjtmail.com",
    "moakt.cc", "moakt.co", "moakt.com", "moakt.ws", "mytrashmail.com", "mytemp.email",
    "nada.email", "nicemail.com", "noblies.net", "nomail.xl.cx", "no-spam.ws",
    "nowmymail.com", "onetimeusemail.com", "plasticinbox.com", "quickinbox.com",
    "sharklasers.com", "slmail.me", "snkmail.com", "soadmail.com", "spam4.me",
    "spambob.com", "spambox.us", "spameater.com", "spamgourmet.com", "spamhole.com",
    "spam.la", "spamspired.com", "sneakemail.com", "soodonims.com", "sosweet.org",
    "temporaryinbox.com", "tempinbox.com", "tempmail.com", "tempmail.net",
    "tempmail.org", "tempmail.io", "temp-mail.org", "temp-mail.io", "throwawaymail.com",
    "trashmail.com", "trashmail.net", "trashmail.org", "trashmail.ws", "trashymail.com",
    "turtle-mail.com", "veryrealemail.com", "welcomea.com", "whyspam.me",
    "yopmail.com", "yopmail.fr", "yopmail.net", "yopmail.org", "zoemail.org",
}


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str
    website: str = ""  # honeypot: скрытое поле для ботов


class LoginRequest(BaseModel):
    email: str
    password: str


class TelegramLoginRequest(BaseModel):
    telegram_id: int
    name: str | None = None
    username: str | None = None


class TelegramCodeRequest(BaseModel):
    code: str


class GoogleLoginRequest(BaseModel):
    """id_token от Google (Google Identity Services / google_sign_in)."""
    id_token: str
    name: str | None = None
    avatar_url: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class UserResponse(BaseModel):
    id: int
    email: str | None
    name: str | None
    avatar_url: str | None


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    # Honeypot: поле "website" люди не заполняют. Ботам отвечаем 400, пользователя не создаём.
    if body.website:
        raise HTTPException(status_code=400, detail="SPAM")

    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="PASSWORD_TOO_SHORT")
    if len(body.password) > 128:
        raise HTTPException(status_code=400, detail="PASSWORD_TOO_LONG")

    name = (body.name or "").strip()
    if len(name) > 60:
        raise HTTPException(status_code=400, detail="NAME_TOO_LONG")

    email = body.email.strip().casefold()

    try:
        validate_email(email, check_deliverability=False)
    except EmailNotValidError:
        raise HTTPException(status_code=400, detail="EMAIL_INVALID")

    if email.rsplit("@", 1)[1] in DISPOSABLE_DOMAINS:
        raise HTTPException(status_code=400, detail="EMAIL_DISPOSABLE")

    # Доставляемость (DNS MX). Fail-open: при сетевом/ДНС сбое пропускаем,
    # чтобы не блокировать легальную регистрацию из-за проблем DNS.
    if settings.EMAIL_DELIVERABILITY_CHECK:
        try:
            validate_email(email, check_deliverability=True)
        except EmailNotValidError:
            raise HTTPException(status_code=400, detail="EMAIL_NOT_DELIVERABLE")
        except Exception:
            pass

    result = await db.execute(select(User).where(User.email == email))
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=email,
        hashed_password=hash_password(body.password),
        name=name,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    await notify_new_user(user.name or user.email or f"User #{user.id}", "email")

    # Ненавязчивая верификация: вход не блокируем, письмо уходит в фоне.
    # Верификация пригодится при восстановлении профиля, если потеряете доступ.
    verify_token = create_email_verify_token(user.id, user.email)
    send_verification_email_async(user.email, verify_token)

    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        user={"id": user.id, "email": user.email, "name": user.name},
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    email = body.email.strip().casefold()
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(body.password, user.hashed_password or ""):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        user={"id": user.id, "email": user.email, "name": user.name},
    )


@router.post("/google", response_model=TokenResponse)
async def google_login(body: GoogleLoginRequest, db: AsyncSession = Depends(get_db)):
    """Google Sign-In: принимает id_token от Google, валидирует подпись/audience,
    находит или создаёт пользователя по google_id (сверяя email), выдаёт наш JWT.
    """
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=501, detail="GOOGLE_NOT_CONFIGURED")

    audiences = [settings.GOOGLE_CLIENT_ID]
    if settings.GOOGLE_ANDROID_CLIENT_ID:
        audiences.append(settings.GOOGLE_ANDROID_CLIENT_ID)

    claims = decode_google_id_token(body.id_token, audiences)
    if claims is None:
        raise HTTPException(status_code=401, detail="INVALID_GOOGLE_TOKEN")

    sub = claims.get("sub")
    email = (claims.get("email") or "").strip().casefold() or None
    name = claims.get("name") or body.name
    picture = claims.get("picture") or body.avatar_url

    if not sub:
        raise HTTPException(status_code=401, detail="INVALID_GOOGLE_TOKEN")

    result = await db.execute(select(User).where(User.google_id == sub))
    user = result.scalar_one_or_none()

    if user is None and email:
        # Нет аккаунта по google_id, но есть по email — привязываем google_id (нужен токен Google, т.к. email подтверждён).
        email_result = await db.execute(select(User).where(User.email == email))
        user = email_result.scalar_one_or_none()
        if user:
            user.google_id = sub

    is_new = False
    if user is None:
        user = User(
            google_id=sub,
            email=email,
            name=name or (email.split("@", 1)[0] if email else f"user_{sub[-6:]}"),
            avatar_url=picture,
            email_verified=True if email else False,  # Google уже верифицировал почту
        )
        db.add(user)
        is_new = True
    else:
        # Обновляем недостающие данные
        if name and not user.name:
            user.name = name
        if picture and not user.avatar_url:
            user.avatar_url = picture
        if email and not user.email:
            user.email = email
        if email and not user.email_verified:
            user.email_verified = True

    await db.commit()
    await db.refresh(user)

    if is_new:
        await notify_new_user(user.name or user.email or f"User #{user.id}", "google")

    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        user={
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "google_id": user.google_id,
        },
    )


@router.get("/verify-email")
async def verify_email(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Ссылка из письма верификации. Публичный эндпоинт: JWT содержит и email-замок.

    Идемпотентен: повторный переход (уже верифицировано) тоже возвращает ok.
    """
    claims = decode_email_verify_claims(token)
    if claims is None:
        raise HTTPException(status_code=400, detail="INVALID_VERIFICATION_TOKEN")
    user_id, token_email = claims

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or user.email != token_email:
        raise HTTPException(status_code=400, detail="INVALID_VERIFICATION_TOKEN")

    if not user.email_verified:
        user.email_verified = True
        await db.commit()

    return {"ok": True}


@router.post("/verify-email/send")
async def send_verify_email(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Повторная отправка письма верификации (кнопка в профиле). Rate-limit 1/мин."""
    if not user.email:
        raise HTTPException(status_code=400, detail="NO_EMAIL")
    if user.email_verified:
        raise HTTPException(status_code=400, detail="ALREADY_VERIFIED")

    now = datetime.utcnow()
    if user.email_verify_sent_at and (now - user.email_verify_sent_at) < timedelta(minutes=1):
        raise HTTPException(status_code=429, detail="TOO_FREQUENT")

    user.email_verify_sent_at = now
    await db.commit()

    token = create_email_verify_token(user.id, user.email)
    sent = await send_verification_email_now(user.email, token)
    return {"ok": True, "sent": sent}


@router.post("/telegram/create-code")
async def create_telegram_code(body: TelegramLoginRequest, db: AsyncSession = Depends(get_db)):
    """Bot calls this to create a pending auth code. Also upserts the user in DB."""
    code = str(secrets.randbelow(900000) + 100000)

    # Upsert user by telegram_id
    result = await db.execute(select(User).where(User.telegram_id == body.telegram_id))
    user = result.scalar_one_or_none()
    is_new = False
    if not user:
        user = User(
            telegram_id=body.telegram_id,
            name=body.name,
            username=body.username,
        )
        db.add(user)
        is_new = True
    else:
        if body.name and not user.name:
            user.name = body.name
        if body.username and not user.username:
            user.username = body.username

    # Fetch Telegram avatar if user has no avatar
    if not user.avatar_url:
        avatar_url = await fetch_telegram_avatar(body.telegram_id)
        if avatar_url:
            user.avatar_url = avatar_url

    pending = PendingTelegramAuth(
        code=code,
        telegram_id=body.telegram_id,
        telegram_name=body.name,
        telegram_username=body.username,
    )
    db.add(pending)
    await db.commit()

    if is_new:
        await notify_new_user(user.name or f"TG #{body.telegram_id}", "telegram")

    return {"code": code}


@router.post("/telegram/verify", response_model=TokenResponse)
async def verify_telegram_code(
    body: TelegramCodeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    """App calls this with the code from the bot to complete login."""
    result = await db.execute(
        select(PendingTelegramAuth).where(
            PendingTelegramAuth.code == body.code,
            PendingTelegramAuth.confirmed == False,
        )
    )
    pending = result.scalar_one_or_none()
    if not pending:
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    if datetime.utcnow() - pending.created_at > timedelta(minutes=10):
        raise HTTPException(status_code=400, detail="Code expired")

    pending.confirmed = True

    if current_user:
        # Logged-in user linking Telegram — merge if bot user exists
        existing_result = await db.execute(
            select(User).where(User.telegram_id == pending.telegram_id, User.id != current_user.id)
        )
        existing = existing_result.scalar_one_or_none()
        if existing:
            # Merge: transfer favorites/practice data from bot user, then delete
            from app.models.models import Favorite, PracticeSession
            await db.execute(
                Favorite.__table__.update().where(Favorite.user_id == existing.id).values(user_id=current_user.id)
            )
            await db.execute(
                PracticeSession.__table__.update().where(PracticeSession.user_id == existing.id).values(user_id=current_user.id)
            )
            # Free up unique fields before current_user takes them (avoid UNIQUE conflicts)
            existing.telegram_id = None
            if pending.telegram_username and pending.telegram_username == existing.username:
                existing.username = None
            await db.flush()
            await db.delete(existing)

        current_user.telegram_id = pending.telegram_id
        if pending.telegram_name and not current_user.name:
            current_user.name = pending.telegram_name
        if pending.telegram_username and not current_user.username:
            current_user.username = pending.telegram_username
        # Fetch Telegram avatar if user has no avatar
        if not current_user.avatar_url:
            avatar_url = await fetch_telegram_avatar(pending.telegram_id)
            if avatar_url:
                current_user.avatar_url = avatar_url
        user = current_user
    else:
        # New login via Telegram — find or create
        user_result = await db.execute(select(User).where(User.telegram_id == pending.telegram_id))
        user = user_result.scalar_one_or_none()

        if user:
            if pending.telegram_name and not user.name:
                user.name = pending.telegram_name
            if pending.telegram_username and not user.username:
                user.username = pending.telegram_username
        else:
            user = User(
                telegram_id=pending.telegram_id,
                name=pending.telegram_name,
                username=pending.telegram_username,
            )
            db.add(user)
            await db.flush()
            await notify_new_user(user.name or f"TG #{pending.telegram_id}", "telegram")

        # Fetch Telegram avatar if user has no avatar
        if not user.avatar_url:
            avatar_url = await fetch_telegram_avatar(pending.telegram_id)
            if avatar_url:
                user.avatar_url = avatar_url

    pending.user_id = user.id
    await db.commit()
    await db.refresh(user)

    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        user={"id": user.id, "email": user.email, "name": user.name, "telegram_id": user.telegram_id},
    )


@router.post("/telegram", response_model=TokenResponse)
async def telegram_login(body: TelegramLoginRequest, db: AsyncSession = Depends(get_db)):
    """Direct telegram login (legacy)."""
    result = await db.execute(select(User).where(User.telegram_id == body.telegram_id))
    user = result.scalar_one_or_none()

    if user:
        if body.name and not user.name:
            user.name = body.name
        if body.username and not user.username:
            user.username = body.username
        await db.commit()
        await db.refresh(user)
    else:
        user = User(
            telegram_id=body.telegram_id,
            name=body.name,
            username=body.username,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        await notify_new_user(user.name or f"TG #{body.telegram_id}", "telegram")

    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        user={"id": user.id, "email": user.email, "name": user.name, "telegram_id": user.telegram_id},
    )


@router.get("/me")
async def get_me(user: User = Depends(require_user)):
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "username": user.username,
        "bio": user.bio,
        "avatar_url": user.avatar_url,
        "telegram_id": user.telegram_id,
        "email_verified": user.email_verified,
        "is_admin": user.is_admin,
        "total_practice_minutes": user.total_practice_minutes,
        "total_practice_days": user.total_practice_days,
        "current_streak": user.current_streak,
        "last_practice_at": user.last_practice_at.isoformat() if user.last_practice_at else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }
