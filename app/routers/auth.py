import secrets
from datetime import datetime, timedelta

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.models import User, UserEmail, PendingTelegramAuth, PendingMaxAuth
from app.services.bot_auth import require_bot_key, require_max_key, require_timer_key
from app.services.auth_service import (
    hash_password,
    verify_password,
    create_access_token,
    require_user,
    get_current_user,
    get_optional_user,
    create_email_verify_token,
    decode_email_verify_claims,
    create_password_reset_token,
    decode_password_reset_claims,
)
from app.services.notify_service import notify_new_user
from app.services.telegram_avatar import fetch_telegram_avatar
from app.services.user_merge import merge_users
from app.services.email_service import (
    send_verification_email_async,
    send_verification_email_now,
    send_password_reset_email_async,
)
from app.services.google import decode_google_id_token
from app.services.vk import login_with_access_token as vk_login_with_access_token
from app.services.yandex import login_with_code as yandex_login_with_code
from app.services.asana_service import normalize_lang
from app.services.max_webapp import (
    MaxWebAppError,
    parse_user as parse_max_user,
    validate_webapp_data,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# Версия документов, с которой соглашается пользователь. Держим в синхроне с
# LEGAL.version на сайте (dharana_web_app/lib/legal/config.ts) и в приложении
# (dharana_app/lib/core/legal/legal_links.dart).
CURRENT_CONSENT_VERSION = "1.2"


def _consent_fields(version: str | None = None) -> dict:
    """Отметка о согласии на обработку персональных данных для нового юзера."""
    return {
        "consent_at": datetime.utcnow(),
        "consent_version": (version or CURRENT_CONSENT_VERSION)[:20],
    }


async def _find_user_by_email(db: AsyncSession, email: str):
    """Юзер по почте-«двери»: основной или верифицированной дополнительной.

    Вход и сброс пароля работают по любой из них. Неверифицированная
    дополнительная почта дверью не считается (иначе можно было бы прицепить
    чужой адрес и угнать сброс — верификация подтверждает владение ящиком).
    """
    owned = select(UserEmail.user_id).where(
        UserEmail.email == email, UserEmail.email_verified == True
    )
    result = await db.execute(
        select(User).where(or_(User.email == email, User.id.in_(owned)))
    )
    return result.scalar_one_or_none()


async def _email_taken(db: AsyncSession, email: str) -> bool:
    """Занята ли почта где-либо (основная или вторичная, даже неверифицированная)."""
    owned = select(UserEmail.user_id).where(UserEmail.email == email)
    result = await db.execute(select(User.id).where(or_(User.email == email, User.id.in_(owned))))
    return result.scalar_one_or_none() is not None

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
    consent: bool = False
    # Версия документов, которую видел пользователь (клиент присылает свою).
    consent_version: str | None = None


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


class MaxSessionRequest(BaseModel):
    """Создание сессии входа через мини-приложение MAX."""

    # Согласие на обработку ПДн (для входа без активной сессии).
    consent: bool = False
    consent_version: str | None = None


class MaxConfirmRequest(BaseModel):
    """Мини-приложение подтверждает сессию подписанным initData."""

    session_id: str = Field(min_length=10, max_length=64)
    init_data: str = Field(min_length=10, max_length=16384)


class MaxBotRegisterRequest(BaseModel):
    """Синхронизация пользователя MAX-ботом (боты не получают JWT)."""

    max_id: int
    name: str | None = None
    username: str | None = None
    language: str | None = None


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
    # Какой OAuth-клиент выдал код. У Яндекса каждая платформа в кабинете имеет
    # свои credentials (у Android-приложения — свои), а код можно обменять только
    # secret'ом именно того клиента. Пусто = дефолтный (веб) клиент.
    client_id: str | None = None


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

    # Без явного согласия персональные данные не обрабатываем (152-ФЗ).
    if not body.consent:
        raise HTTPException(status_code=400, detail="CONSENT_REQUIRED")

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

    if await _email_taken(db, email):
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=email,
        hashed_password=hash_password(body.password),
        name=name,
        **_consent_fields(body.consent_version),
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
    user = await _find_user_by_email(db, email)
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
        user = await _find_user_by_email(db, email)
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
            # Вход через соцсеть сопровождается юридической плашкой на экране
            # входа: продолжая, пользователь принимает документы.
            **_consent_fields(),
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


def _yandex_client_credentials(requested: str | None) -> tuple[str, str]:
    """Креденшелы Яндекса для обмена кода: веб-клиент или Android-приложение.

    В кабинете Яндекса у каждой платформы свой Client ID и secret (в т.ч. у
    добавленного нами Android-приложения), а код обменивается только secret'ом
    того клиента, который его выдал. Дополнительные клиенты перечислены в
    `YANDEX_EXTRA_CLIENTS` как `id:secret,id2:secret2`; клиент присылает в
    запросе только публичный client_id, секрет остаётся на сервере.
    """
    if not requested or requested == settings.YANDEX_CLIENT_ID:
        return settings.YANDEX_CLIENT_ID, settings.YANDEX_CLIENT_SECRET
    for pair in settings.YANDEX_EXTRA_CLIENTS.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        extra_id, extra_secret = pair.split(":", 1)
        if extra_id.strip() == requested:
            return extra_id.strip(), extra_secret.strip()
    raise HTTPException(status_code=400, detail="OAUTH_CLIENT_NOT_ALLOWED")


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
    client_id, client_secret = _yandex_client_credentials(body.client_id)
    if not client_id or not client_secret:
        raise HTTPException(status_code=501, detail="YANDEX_NOT_CONFIGURED")

    profile = yandex_login_with_code(
        code=body.code,
        redirect_uri=_oauth_redirect_uri("yandex", body.redirect_uri),
        client_id=client_id,
        client_secret=client_secret,
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

    user = await _find_user_by_email(db, email)
    if user is None:
        # Несуществующий email — отвечаем одинаково (анти-энумерация).
        return {"ok": True}
    # Аккаунт без пароля (OAuth-вход) тоже получает ссылку — она позволяет
    # УСТАНОВИТЬ пароль, по сути открыв «дверь» email+пароль.

    now = datetime.utcnow()
    if user.password_reset_sent_at and (now - user.password_reset_sent_at) < timedelta(minutes=1):
        raise HTTPException(status_code=429, detail="TOO_FREQUENT")

    user.password_reset_sent_at = now
    await db.commit()

    # Токен и письмо уходят на ТУ почту, которую юзер ввёл (основную или
    # дополнительную) — получение письма подтверждает владение именно ею.
    token = create_password_reset_token(user.id, email, issued_at=now)
    send_password_reset_email_async(email, token)
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
    user_id, token_email, token_issued = claims

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=400, detail="INVALID_RESET_TOKEN")

    # Токен действителен только для последнего запроса сброса. If sent_at is
    # None (internal/test-created token), skip the binding so unit tests can
    # mint tokens directly; real tokens always come from /password-reset/send.
    if user.password_reset_sent_at is not None:
        sent_ts = int((user.password_reset_sent_at - datetime(1970, 1, 1)).total_seconds())
        if sent_ts != token_issued:
            raise HTTPException(status_code=400, detail="INVALID_RESET_TOKEN")

    # Токен замкнут на почту, на которую ушло письмо (основную или вторичную).
    known_emails = {e for e in [user.email] if e}
    owned = (await db.execute(
        select(UserEmail.email).where(UserEmail.user_id == user.id)
    )).scalars().all()
    known_emails.update(owned)
    if token_email not in known_emails:
        raise HTTPException(status_code=400, detail="INVALID_RESET_TOKEN")

    user.hashed_password = hash_password(body.password)
    # Одноразовость: после успешного сброса токен больше не принимается.
    # Сентинел (эпоха) также позволяет сразу запросить новую ссылку.
    user.password_reset_sent_at = datetime(1970, 1, 1)
    # Доступ к почте подтверждён фактом получения письма: помечаем верифицированной
    # основную почту и (если письмо уходило на вторичную) саму вторичную.
    user.email_verified = True
    if token_email != user.email:
        await db.execute(
            UserEmail.__table__.update().where(
                UserEmail.user_id == user.id, UserEmail.email == token_email
            ).values(email_verified=True)
        )
    await db.commit()

    return {"ok": True}


@router.post("/telegram/create-code")
async def create_telegram_code(
    body: TelegramLoginRequest,
    _: bool = Depends(require_bot_key),
    db: AsyncSession = Depends(get_db),
):
    """Bot calls this to create a pending auth code. Also upserts the user in DB.

    Только для бота (X-Bot-Key): код — это фактически одноразовый пароль,
    публично его отдавать нельзя.
    """
    # Чистим просроченные коды (10 минут), чтобы таблица не росла бесконечно.
    await db.execute(
        delete(PendingTelegramAuth).where(
            PendingTelegramAuth.created_at < datetime.utcnow() - timedelta(minutes=10)
        )
    )

    # Также снимаем старые неиспользованные коды того же telegram_id: один
    # активный код за раз — иначе можно накапливать их через быстрые вызовы бота.
    await db.execute(
        delete(PendingTelegramAuth).where(
            PendingTelegramAuth.telegram_id == body.telegram_id,
            PendingTelegramAuth.confirmed == False,
        )
    )

    # code UNIQUE: при редком совпадении с ещё живым кодом INSERT упал бы
    # с 500, поэтому подбираем свободный (заодно не выдаём два одинаковых).
    for _ in range(10):
        code = str(secrets.randbelow(900000) + 100000)
        taken = await db.execute(
            select(PendingTelegramAuth.id)
            .where(PendingTelegramAuth.code == code)
            .limit(1)
        )
        if taken.scalar_one_or_none() is None:
            break
    else:
        raise HTTPException(status_code=503, detail="Could not allocate login code")

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
            # /start бота предупреждает о сборе Telegram ID, имени и username.
            **_consent_fields(),
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
    current_user: User | None = Depends(get_optional_user),
):
    """App calls this with the code from the bot to complete login."""
    now = datetime.utcnow()
    # Просроченные коды не ищем вовсе: единый ответ не даёт отличить
    # «неверный» от «истёкший», а лишний код не остаётся валидным из-за гонки.
    result = await db.execute(
        select(PendingTelegramAuth).where(
            PendingTelegramAuth.code == body.code,
            PendingTelegramAuth.confirmed == False,
            PendingTelegramAuth.created_at >= now - timedelta(minutes=10),
        )
    )
    pending = result.scalar_one_or_none()
    if not pending:
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    if now - pending.created_at > timedelta(minutes=10):
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    pending.confirmed = True

    if current_user:
        # Logged-in user linking Telegram — merge if bot user exists
        existing_result = await db.execute(
            select(User).where(User.telegram_id == pending.telegram_id, User.id != current_user.id)
        )
        existing = existing_result.scalar_one_or_none()
        if existing:
            # Merge: переносим ВСЕ данные существующего пользователя (подписку,
            # избранное, практики, комплексы, платежи, статистику и т.д.) в
            # current_user, освобождая уникальные поля перед удалением.
            await merge_users(db, source=existing, target=current_user)
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
                **_consent_fields(),
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


@router.post("/telegram")
async def telegram_login(
    body: TelegramLoginRequest,
    _: bool = Depends(require_timer_key),
    db: AsyncSession = Depends(get_db),
):
    """Синхронизация пользователя таймер-ботом (legacy).

    Только для таймер-бота (X-Timer-Key). JWT здесь НЕ выдаётся: вход в
    приложение идёт через create-code + verify.
    """
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
            **_consent_fields(),
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        await notify_new_user(user.name or f"TG #{body.telegram_id}", "telegram")

    return {
        "ok": True,
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "telegram_id": user.telegram_id,
            "language": user.language,
            "timer_language": user.timer_language,
        },
    }


class TimerLanguageRequest(BaseModel):
    language: str = "ru"


@router.get("/telegram/{telegram_id}/language")
async def get_timer_language(
    telegram_id: int,
    _: bool = Depends(require_timer_key),
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
    _: bool = Depends(require_timer_key),
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


# --- MAX (МАКС) -------------------------------------------------------------
# OAuth для сторонних сайтов у MAX нет: личность подтверждает мини-приложение
# бота, присылая подписанный initData. Схема и ограничения — в
# app/services/max_webapp.py. Код в чат не отправляем: п. 1.5 Требований MAX
# запрещает авторизационные сообщения через API без договора с Компанией.


def _max_session_ttl() -> timedelta:
    return timedelta(minutes=max(1, settings.MAX_SESSION_TTL_MINUTES))


def _max_bot_url() -> str:
    return f"https://max.ru/{settings.MAX_BOT_NAME}"


def _max_deep_link(session_id: str) -> str:
    # payload: латиница/цифры/подчёркивание, до 512 символов.
    return f"{_max_bot_url()}?startapp=login_{session_id}"


def _max_user_name(max_user: dict, max_id: int) -> str:
    parts = [max_user.get("first_name"), max_user.get("last_name")]
    name = " ".join(str(p).strip() for p in parts if p)
    if not name:
        name = str(max_user.get("username") or "").strip()
    return (name or f"MAX {max_id}")[:255]


@router.post("/max/session")
async def create_max_session(
    body: MaxSessionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
):
    """Создать сессию входа через MAX и вернуть deep link мини-приложения.

    Вызывается и с сайта, и из приложения. Если пользователь уже авторизован
    (есть JWT), сессия работает как привязка MAX к текущему профилю.
    """
    if not settings.MAX_BOT_TOKEN or not settings.MAX_BOT_NAME:
        raise HTTPException(status_code=503, detail="MAX_NOT_CONFIGURED")

    if current_user is None and not body.consent:
        raise HTTPException(status_code=400, detail="CONSENT_REQUIRED")

    now = datetime.utcnow()
    # Чистим сессии, которые давно истекли, чтобы таблица не росла.
    await db.execute(
        delete(PendingMaxAuth).where(
            PendingMaxAuth.expires_at < now - timedelta(hours=1)
        )
    )

    session_id = secrets.token_hex(16)
    session = PendingMaxAuth(
        session_id=session_id,
        user_id=current_user.id if current_user else None,
        consent_version=(
            (body.consent_version or CURRENT_CONSENT_VERSION)[:20]
            if current_user is None
            else None
        ),
        expires_at=now + _max_session_ttl(),
    )
    db.add(session)
    await db.commit()

    return {
        "session_id": session_id,
        "mode": "link" if current_user else "login",
        "deep_link": _max_deep_link(session_id),
        "bot_url": _max_bot_url(),
        "miniapp_url": settings.MAX_MINIAPP_URL,
        "expires_in": int(_max_session_ttl().total_seconds()),
        "poll_interval_ms": 2000,
        "consent_version": CURRENT_CONSENT_VERSION,
    }


@router.post("/max/confirm")
async def confirm_max_session(
    body: MaxConfirmRequest,
    db: AsyncSession = Depends(get_db),
):
    """Подтвердить сессию из мини-приложения подписанным initData.

    Вызывается только страницей мини-приложения внутри MAX. JWT здесь не
    выдаётся: его забирает исходный клиент (сайт/приложение) поллингом.
    """
    if not settings.MAX_BOT_TOKEN:
        raise HTTPException(status_code=503, detail="MAX_NOT_CONFIGURED")

    try:
        data = validate_webapp_data(body.init_data, settings.MAX_BOT_TOKEN)
    except MaxWebAppError:
        raise HTTPException(status_code=400, detail="INVALID_INIT_DATA")

    max_user = parse_max_user(data)
    try:
        max_id = int((max_user or {}).get("id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="MAX_USER_MISSING")

    # start_param подписан вместе с остальными полями: если он есть, он должен
    # указывать на ту же сессию (защита от подмены в клиенте).
    start_param = data.get("start_param")
    if start_param and start_param != f"login_{body.session_id}":
        raise HTTPException(status_code=400, detail="SESSION_MISMATCH")

    now = datetime.utcnow()
    result = await db.execute(
        select(PendingMaxAuth).where(PendingMaxAuth.session_id == body.session_id)
    )
    session = result.scalar_one_or_none()
    if session is None or session.expires_at < now:
        raise HTTPException(status_code=400, detail="MAX_SESSION_EXPIRED")
    if session.status != "pending":
        raise HTTPException(status_code=400, detail="MAX_SESSION_USED")

    session.max_id = max_id
    session.max_name = _max_user_name(max_user or {}, max_id)
    username = str((max_user or {}).get("username") or "").strip()
    session.max_username = username[:100] or None
    avatar = str((max_user or {}).get("photo_url") or "").strip()
    session.max_avatar_url = avatar[:500] or None
    session.max_language = str((max_user or {}).get("language_code") or "").strip()[:10] or None
    session.status = "confirmed"
    session.confirmed_at = now
    await db.commit()

    return {"ok": True, "status": "confirmed"}


async def _finish_max_login(db: AsyncSession, session: PendingMaxAuth) -> User:
    """Найти/создать пользователя по подтверждённой сессии MAX."""
    assert session.max_id is not None
    max_id = session.max_id
    name = session.max_name or f"MAX {max_id}"

    async def _free_username() -> str | None:
        """MAX-ник свободен? Иначе не занимаем чужой (UNIQUE в app_users)."""
        if not session.max_username:
            return None
        taken = (
            await db.execute(
                select(User.id).where(User.username == session.max_username).limit(1)
            )
        ).scalar_one_or_none()
        return None if taken is not None else session.max_username

    if session.user_id:
        # Привязка MAX к уже открытому профилю.
        user = await db.get(User, session.user_id)
        if user is None:
            raise HTTPException(status_code=400, detail="MAX_SESSION_USER_MISSING")

        existing = (
            await db.execute(
                select(User).where(User.max_id == max_id, User.id != user.id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            await merge_users(db, source=existing, target=user)
            await db.delete(existing)
            await db.flush()

        user.max_id = max_id
        if not user.name:
            user.name = name
        if not user.username:
            user.username = await _free_username()
        if not user.avatar_url and session.max_avatar_url:
            user.avatar_url = session.max_avatar_url
        await db.commit()
        await db.refresh(user)
        return user

    user = (
        await db.execute(select(User).where(User.max_id == max_id))
    ).scalar_one_or_none()

    if user is not None:
        if not user.name:
            user.name = name
        if not user.username:
            user.username = await _free_username()
        if not user.avatar_url and session.max_avatar_url:
            user.avatar_url = session.max_avatar_url
        await db.commit()
        await db.refresh(user)
        return user

    user = User(
        max_id=max_id,
        name=name,
        username=await _free_username(),
        avatar_url=session.max_avatar_url,
        language=normalize_lang(session.max_language or "ru"),
        **_consent_fields(session.consent_version),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    await notify_new_user(user.name or f"MAX #{max_id}", "max")
    return user


@router.get("/max/session/{session_id}")
async def poll_max_session(session_id: str, db: AsyncSession = Depends(get_db)):
    """Поллинг сессии. При подтверждении один раз отдаёт JWT."""
    result = await db.execute(
        select(PendingMaxAuth).where(PendingMaxAuth.session_id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        return {"status": "expired"}

    now = datetime.utcnow()
    if session.expires_at < now:
        return {"status": "expired"}

    if session.status == "pending":
        return {
            "status": "pending",
            "expires_in": max(0, int((session.expires_at - now).total_seconds())),
        }
    if session.status == "consumed":
        return {"status": "consumed"}

    # confirmed: забираем сессию атомарно, чтобы два параллельных поллинга
    # не создали двух пользователей и не выдали два токена.
    claimed = await db.execute(
        update(PendingMaxAuth)
        .where(
            PendingMaxAuth.id == session.id,
            PendingMaxAuth.status == "confirmed",
        )
        .values(status="consumed")
    )
    if not claimed.rowcount:
        await db.rollback()
        return {"status": "consumed"}

    try:
        await db.commit()
        user = await _finish_max_login(db, session)
    except Exception:
        # Не сожгли сессию зря: при сбое создания профиля клиент сможет
        # повторить поллинг.
        await db.rollback()
        await db.execute(
            update(PendingMaxAuth)
            .where(PendingMaxAuth.id == session.id)
            .values(status="confirmed")
        )
        await db.commit()
        raise

    token = create_access_token(user.id)
    return {
        "status": "confirmed",
        "access_token": token,
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "max_id": user.max_id,
        },
    }


@router.post("/max/bot/register")
async def register_max_bot_user(
    body: MaxBotRegisterRequest,
    _: bool = Depends(require_max_key),
    db: AsyncSession = Depends(get_db),
):
    """Синхронизация пользователя MAX-ботом. JWT не выдаётся."""
    lang = normalize_lang(body.language or "ru")
    user = (
        await db.execute(select(User).where(User.max_id == body.max_id))
    ).scalar_one_or_none()

    if user is not None:
        if body.name and not user.name:
            user.name = body.name
        if body.username and not user.username:
            taken = (
                await db.execute(
                    select(User.id).where(User.username == body.username).limit(1)
                )
            ).scalar_one_or_none()
            if taken is None:
                user.username = body.username
        await db.commit()
        await db.refresh(user)
    else:
        username = None
        if body.username:
            taken = (
                await db.execute(
                    select(User.id).where(User.username == body.username).limit(1)
                )
            ).scalar_one_or_none()
            username = body.username if taken is None else None
        user = User(
            max_id=body.max_id,
            name=body.name,
            username=username,
            language=lang,
            **_consent_fields(),
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        await notify_new_user(user.name or f"MAX #{body.max_id}", "max")

    return {
        "ok": True,
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "max_id": user.max_id,
            "language": normalize_lang(user.language),
        },
    }


@router.get("/max/{max_id}/language")
async def get_max_language(
    max_id: int,
    _: bool = Depends(require_max_key),
    db: AsyncSession = Depends(get_db),
):
    user = (
        await db.execute(select(User).where(User.max_id == max_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return {"language": normalize_lang(user.language)}


@router.put("/max/{max_id}/language")
async def set_max_language(
    max_id: int,
    body: TimerLanguageRequest,
    _: bool = Depends(require_max_key),
    db: AsyncSession = Depends(get_db),
):
    user = (
        await db.execute(select(User).where(User.max_id == max_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user.language = normalize_lang(body.language)
    user.updated_at = datetime.utcnow()
    await db.commit()
    return {"language": normalize_lang(user.language)}


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
        "max_id": user.max_id,
        "email_verified": user.email_verified,
        "is_admin": user.is_admin,
        "total_practice_minutes": user.total_practice_minutes,
        "total_practice_days": user.total_practice_days,
        "current_streak": user.current_streak,
        "last_practice_at": user.last_practice_at.isoformat() if user.last_practice_at else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }
