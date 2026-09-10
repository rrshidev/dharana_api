"""Транзакционная почта через SMTP.BZ (https://docs.smtp.bz/#/api).

Используется для ненавязчивой верификации email при регистрации: письмо
отправляется в фоне и НЕ блокирует вход. Без настроенного API_SMPT отправка
молча пропускается (fail-open) — приложение продолжает работать в dev/тестах.
"""

import asyncio
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

SEND_URL = "https://api.smtp.bz/v1/smtp/send"

_background_tasks: set = set()


def _spawn(coro):
    """Запуск фоновой задачи без GC-мёртвой ссылки."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def send_email(to: str, subject: str, html: str, text: str = "") -> bool:
    """Отправка письма. Возвращает True при успехе, False при любой ошибке.

    Fail-open: при отсутствии ключа или сетевой ошибке пишет в лог и не бросает
    исключение, чтобы регистрация/ресенд не падали из-за почты.
    """
    if not settings.API_SMPT:
        logger.warning("send_email skipped (API_SMPT not configured): to=%s", to)
        return False

    fields = {
        "from": settings.SMTPBZ_FROM,
        "name": settings.SMTPBZ_FROM_NAME,
        "subject": subject,
        "to": to,
        "html": html,
    }
    if text:
        fields["text"] = text

    # SMTP.BZ ожидает multipart/form-data; строковые поля передаём как (None, value).
    files = {key: (None, value) for key, value in fields.items()}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                SEND_URL,
                files=files,
                headers={"Authorization": settings.API_SMPT},
            )
            if resp.status_code >= 300:
                logger.error("send_email failed %s to=%s: %s", resp.status_code, to, resp.text[:300])
                return False
            logger.info("send_email ok to=%s tag=verify-email", to)
            return True
    except Exception as e:
        logger.error("send_email error to=%s: %s", to, e)
        return False


def build_verify_html(link: str) -> str:
    """Минимальный HTML письма верификации (одна кнопка)."""
    return (
        "<div style='font-family:Arial,Helvetica,sans-serif;max-width:480px;margin:0 auto;"
        "padding:24px;color:#1f2937'>"
        "<h2 style='font-size:20px;margin:0 0 12px'>Подтвердите почту</h2>"
        "<p style='font-size:14px;line-height:1.6;margin:0 0 20px'>"
        "Привет! Подтвердите, что этот адрес принадлежит вам — это поможет "
        "восстановить профиль, если потеряете доступ.</p>"
        "<a href='%s' style='display:inline-block;background:#22c55e;color:#ffffff;"
        "text-decoration:none;padding:12px 22px;border-radius:9999px;font-size:14px;"
        "font-weight:600'>Подтвердить почту</a>"
        "<p style='font-size:12px;color:#6b7280;margin:20px 0 0'>Если письмо открыли "
        "случайно — просто проигнорируйте его.</p>"
        "</div>"
    ) % link


def send_verification_email_async(email: str, token: str) -> None:
    """Фоновая отправка письма верификации (не блокирует ответ регистрации)."""
    link = f"{settings.EMAIL_VERIFY_BASE_URL}/verify-email?token={token}"
    _spawn(send_email(email, "Подтвердите почту — Dharana", build_verify_html(link)))


async def send_verification_email_now(email: str, token: str) -> bool:
    """Синхронная отправка (кнопка «Отправить ещё раз»)."""
    link = f"{settings.EMAIL_VERIFY_BASE_URL}/verify-email?token={token}"
    return await send_email(email, "Подтвердите почту — Dharana", build_verify_html(link))


def build_reset_html(link: str) -> str:
    """Минимальный HTML письма сброса пароля (одна кнопка)."""
    return (
        "<div style='font-family:Arial,Helvetica,sans-serif;max-width:480px;margin:0 auto;"
        "padding:24px;color:#1f2937'>"
        "<h2 style='font-size:20px;margin:0 0 12px'>Восстановление пароля</h2>"
        "<p style='font-size:14px;line-height:1.6;margin:0 0 20px'>"
        "Мы получили запрос на сброс пароля. Если это были вы — нажмите кнопку, "
        "чтобы задать новый пароль. Ссылка действует 30 минут.</p>"
        "<a href='%s' style='display:inline-block;background:#22c55e;color:#ffffff;"
        "text-decoration:none;padding:12px 22px;border-radius:9999px;font-size:14px;"
        "font-weight:600'>Сбросить пароль</a>"
        "<p style='font-size:12px;color:#6b7280;margin:20px 0 0'>Если письмо открыли "
        "случайно — просто проигнорируйте его.</p>"
        "</div>"
    ) % link


def send_password_reset_email_async(email: str, token: str) -> None:
    """Фоновая отправка письма сброса пароля (не блокирует ответ)."""
    link = f"{settings.EMAIL_VERIFY_BASE_URL}/reset-password?token={token}"
    _spawn(send_email(email, "Сброс пароля — Dharana", build_reset_html(link)))


async def send_password_reset_email_now(email: str, token: str) -> bool:
    """Синхронная отправка письма сброса пароля."""
    link = f"{settings.EMAIL_VERIFY_BASE_URL}/reset-password?token={token}"
    return await send_email(email, "Сброс пароля — Dharana", build_reset_html(link))