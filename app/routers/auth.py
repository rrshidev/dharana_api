import secrets
from datetime import datetime, timedelta

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
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
    create_password_reset_token,
    decode_password_reset_claims,
)
from app.services.notify_service import notify_new_user
from app.services.telegram_avatar import fetch_telegram_avatar
from app.services.email_service import (
    send_verification_email_async,
    send_verification_email_now,
    send_password_reset_email_async,
)
from app.services.google import decode_google_id_token
from app.services.vk import login_with_access_token as vk_login_with_access_token
from app.services.yandex import login_with_code as yandex_login_with_code
from app.services.asana_service import normalize_lang

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
    language: str | None = None


class TelegramCodeRequest(BaseModel):
    code: str


class GoogleLoginRequest(BaseModel):
    """id_token от Google (Google Identity Services / google_sign_in)."""
    id_token: str
    name: str | None = None
    avatar_url: str | None = None


class OAuthCodeRequest(BaseModel):
    """Authorization code от Яндекса (редирект-флоу на вебе или в приложении)."""
    code: str
    # Приложение Android ловит свой колбэк через App Link и присылает его сюда,
    # потому что обмен кода на токен требует ТОЧНОГО redirect_uri из authorize-запроса.
    # Значение проверяется по allowlist (см. _oauth_redirect_uri).
    redirect_uri: str | None = None
    # PKCE (S256) — шлёт клиент, которому он был нужен (Яндекс не требует).
    code_verifier: str | None = None


class VKAccessTokenRequest(BaseModel):
    """access_token из официального виджета VK ID OneTap.

    Виджет сам меняет код на токен в браузере (VK ID требует device_id, который
    даёт только их SDK), поэтому клиент шлёт нам токен, а мы проверяем его
    через /oauth2/user_info. Пустой токен → 422.
    """
    access_token: str = Field(min_length=1)


class PasswordResetRequest(BaseModel):
    email: str


class PasswordResetConfirmRequest(BaseModel):
    token: str
    password: str


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


async def _finish_social_login(
    db: AsyncSession,
    *,
    provider: str,
    provider_field: str,
    provider_id: str | None,
    email: str | None,
    name: str | None,
    avatar_url: str | None,
    error_detail: str,
) -> TokenResponse:
    """Общая часть входа через внешний провайдер (Google / VK ID / Яндекс).

    Идентификатор провайдера — главный ключ поиска. Если юзер уже есть по
    совпавшей почте — просто привязываем provider_id (провайдер почту проверил).
    """
    if not provider_id:
        raise HTTPException(status_code=401, detail=error_detail)

    result = await db.execute(select(User).where(getattr(User, provider_field) == provider_id))
    user = result.scalar_one_or_none()

    if user is None and email:
        email_result = await db.execute(select(User).where(User.email == email))
        user = email_result.scalar_one_or_none()
        if user:
            setattr(user, provider_field, provider_id)

    is_new = False
    if user is None:
        fallback_name = email.split("@", 1)[0] if email else f"user_{provider_id[-6:]}"
        user = User(
            email=email,
            name=name or fallback_name,
            avatar_url=avatar_url,
            email_verified=True if email else False,  # почту уже проверил провайдер
        )
        setattr(user, provider_field, provider_id)
        db.add(user)
        is_new = True
    else:
        # Обновляем недостающие данные
        if name and not user.name:
            user.name = name
        if avatar_url and not user.avatar_url:
            user.avatar_url = avatar_url
        if email and not user.email:
            user.email = email
        if email and not user.email_verified:
            user.email_verified = True

    await db.commit()
    await db.refresh(user)

    if is_new:
        await notify_new_user(user.name or user.email or f"User #{user.id}", provider)

    token = create_access_token(user.id)
    payload = {
        "id": user.id,
        "email": user.email,
        "name": user.name,
    }
    payload[provider_field] = getattr(user, provider_field)
    return TokenResponse(access_token=token, user=payload)


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

    return await _finish_social_login(
        db,
        provider="google",
        provider_field="google_id",
        provider_id=claims.get("sub"),
        email=(claims.get("email") or "").strip().casefold() or None,
        name=claims.get("name") or body.name,
        avatar_url=claims.get("picture") or body.avatar_url,
        error_detail="INVALID_GOOGLE_TOKEN",
    )


def _oauth_redirect_uri(provider: str, requested: str | None) -> str:
    """Какой redirect_uri слать в обмене кода на токен.

    Дефолт — колбэк сайта (`{base}/api/auth/{provider}/callback`): им пользуется
    веб. Приложение Android ловит свой колбэк через App Link, поэтому присылает
    свой URI — но принять можно только заранее разрешённые значения из
    `*_ALLOWED_REDIRECT_URIS`, иначе 400. Так клиент не сможет подсунуть
    чужой redirect_uri (перехват кода) и сломать обмен.
    """
    default = f"{settings.OAUTH_REDIRECT_BASE_URL.rstrip('/')}/api/auth/{provider}/callback"
    if not requested:
        return default
    allowed = {default}
    extra = settings.VK_ALLOWED_REDIRECT_URIS if provider == "vk" else settings.YANDEX_ALLOWED_REDIRECT_URIS
    allowed.update(uri.strip() for uri in extra.split(",") if uri.strip())
    if requested not in allowed:
        raise HTTPException(status_code=400, detail="OAUTH_REDIRECT_NOT_ALLOWED")
    return requested


@router.post("/vk", response_model=TokenResponse)
async def vk_login(body: VKAccessTokenRequest, db: AsyncSession = Depends(get_db)):
    """Вход по VK ID.

    Клиент — официальный виджет OneTap: код на access_token меняет сам браузер
    (`VKID.Auth.exchangeCode`), а мы проверяем присланный токен через
    /oauth2/user_info и выдаём свой JWT. client_secret в этой схеме не нужен.
    """
    if not settings.VK_CLIENT_ID:
        raise HTTPException(status_code=501, detail="VK_NOT_CONFIGURED")

    profile = vk_login_with_access_token(
        access_token=body.access_token,
        client_id=settings.VK_CLIENT_ID,
    )
    if profile is None:
        raise HTTPException(status_code=401, detail="INVALID_VK_TOKEN")

    return await _finish_social_login(
        db,
        provider="vk",
        provider_field="vk_id",
        provider_id=profile["id"],
        email=profile["email"],
        name=profile["name"],
        avatar_url=profile["avatar_url"],
        error_detail="INVALID_VK_TOKEN",
    )


@router.post("/yandex", response_model=TokenResponse)
async def yandex_login(body: OAuthCodeRequest, db: AsyncSession = Depends(get_db)):
    """Вход по Яндекс ID: код → access_token → профиль на login.yandex.ru/info."""
    if not settings.YANDEX_CLIENT_ID or not settings.YANDEX_CLIENT_SECRET:
        raise HTTPException(status_code=501, detail="YANDEX_NOT_CONFIGURED")

    profile = yandex_login_with_code(
        code=body.code,
        redirect_uri=_oauth_redirect_uri("yandex", body.redirect_uri),
        client_id=settings.YANDEX_CLIENT_ID,
        client_secret=settings.YANDEX_CLIENT_SECRET,
    )
    if profile is None:
        raise HTTPException(status_code=401, detail="INVALID_YANDEX_TOKEN")

    return await _finish_social_login(
        db,
        provider="yandex",
        provider_field="yandex_id",
        provider_id=profile["id"],
        email=profile["email"],
        name=profile["name"],
        avatar_url=profile["avatar_url"],
        error_detail="INVALID_YANDEX_TOKEN",
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


@router.post("/password-reset/send")
async def send_password_reset(body: PasswordResetRequest, db: AsyncSession = Depends(get_db)):
    """Запрос письма сброса пароля. Публичный.

    Ответ НЕ различает существующий/несуществующий email (анти-энумерация):
    всегда {ok:true}. Rate-limit 1/мин по user.password_reset_sent_at.
    """
    email = body.email.strip().casefold()
    try:
        validate_email(email, check_deliverability=False)
    except EmailNotValidError:
        raise HTTPException(status_code=400, detail="EMAIL_INVALID")

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None or not user.hashed_password:
        # Аккаунта с таким паролем нет (email-аккаунт или юзер без пароля) —
        # для Google/Telegram-юзеров письмо не шлём, но отвечаем одинаково.
        return {"ok": True}

    now = datetime.utcnow()
    if user.password_reset_sent_at and (now - user.password_reset_sent_at) < timedelta(minutes=1):
        raise HTTPException(status_code=429, detail="TOO_FREQUENT")

    user.password_reset_sent_at = now
    await db.commit()

    token = create_password_reset_token(user.id, user.email)
    send_password_reset_email_async(user.email, token)
    return {"ok": True}


@router.post("/password-reset/confirm")
async def confirm_password_reset(body: PasswordResetConfirmRequest, db: AsyncSession = Depends(get_db)):
    """Установка нового пароля по токену из письма. Публичный, одноразовый по email-замку."""
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="PASSWORD_TOO_SHORT")
    if len(body.password) > 128:
        raise HTTPException(status_code=400, detail="PASSWORD_TOO_LONG")

    claims = decode_password_reset_claims(body.token)
    if claims is None:
        raise HTTPException(status_code=400, detail="INVALID_RESET_TOKEN")
    user_id, token_email = claims

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or user.email != token_email:
        raise HTTPException(status_code=400, detail="INVALID_RESET_TOKEN")

    user.hashed_password = hash_password(body.password)
    # Доступ к почте подтверждён фактом получения письма.
    user.email_verified = True
    await db.commit()

    return {"ok": True}


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
            language=(body.language or "ru"),
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
    lang = normalize_lang(body.language or "ru")
    result = await db.execute(select(User).where(User.telegram_id == body.telegram_id))
    user = result.scalar_one_or_none()

    if user:
        if body.name and not user.name:
            user.name = body.name
        if body.username and not user.username:
            user.username = body.username
        # Язык таймер-бота задаём ТОЛЬКО при первом контакте (не перезаписываем ручной выбор)
        if not user.timer_language:
            user.timer_language = lang
        await db.commit()
        await db.refresh(user)
    else:
        user = User(
            telegram_id=body.telegram_id,
            name=body.name,
            username=body.username,
            language=lang,
            timer_language=lang,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        await notify_new_user(user.name or f"TG #{body.telegram_id}", "telegram")

    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        user={"id": user.id, "email": user.email, "name": user.name, "telegram_id": user.telegram_id,
              "language": user.language, "timer_language": user.timer_language},
    )


class TimerLanguageRequest(BaseModel):
    language: str = "ru"


@router.get("/telegram/{telegram_id}/language")
async def get_timer_language(
    telegram_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Язык таймер-бота пользователя: {"language": "ru"|"en"}."""
    result = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"language": normalize_lang(user.timer_language)}


@router.put("/telegram/{telegram_id}/language")
async def set_timer_language(
    telegram_id: int,
    body: TimerLanguageRequest,
    db: AsyncSession = Depends(get_db),
):
    """Сменить язык таймер-бота для пользователя."""
    result = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.timer_language = normalize_lang(body.language)
    user.updated_at = datetime.utcnow()
    await db.commit()
    return {"language": normalize_lang(user.timer_language)}


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
