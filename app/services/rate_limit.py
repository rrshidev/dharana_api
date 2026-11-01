"""Простой in-memory rate limit для чувствительных auth-эндпоинтов.

Один процесс uvicorn — этого достаточно, чтобы остановить перебор с одного IP.
Ключ — (IP, path, method). Для приватных (docker) адресов лимит выше: боты
ходят напрямую и легитимно делают больше запросов.
"""

import ipaddress
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Optional

# (method, path) -> (limit for external IP, window seconds)
RULES: dict[tuple[str, str], tuple[int, int]] = {
    ("POST", "/api/v1/auth/login"): (15, 300),
    ("POST", "/api/v1/auth/register"): (5, 600),
    ("POST", "/api/v1/auth/password-reset/send"): (5, 600),
    ("POST", "/api/v1/auth/telegram/verify"): (15, 600),
    ("POST", "/api/v1/auth/telegram/create-code"): (120, 600),
    ("POST", "/api/v1/auth/telegram"): (300, 300),
    ("POST", "/api/v1/auth/google"): (60, 300),
    ("POST", "/api/v1/auth/vk"): (60, 300),
    ("POST", "/api/v1/auth/yandex"): (60, 300),
}

_hits: dict[tuple[str, str], Deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def client_ip(headers: dict, fallback: Optional[str]) -> str:
    # X-Forwarded-For может содержать присланное клиентом значение; Caddy
    # дописывает реальный IP в конец, поэтому доверяем только последнему.
    xff = headers.get("x-forwarded-for") or headers.get("X-Forwarded-For")
    if xff:
        candidate = str(xff).split(",")[-1].strip()
        try:
            ipaddress.ip_address(candidate)
            return candidate
        except ValueError:
            pass
    return fallback or "unknown"


def _is_internal(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
        return addr.is_private or addr.is_loopback
    except ValueError:
        return False


def check(method: str, path: str, ip: str) -> Optional[int]:
    """Вернуть Retry-After (сек), если запрос надо отклонить, иначе None."""
    rule = RULES.get((method.upper(), path))
    if not rule:
        return None
    limit, window = rule
    if _is_internal(ip):
        limit *= 10

    now = time.monotonic()
    key = (ip, f"{method.upper()} {path}")
    with _lock:
        bucket = _hits[key]
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        if len(bucket) >= limit:
            return max(1, int(window - (now - bucket[0])) + 1)
        bucket.append(now)
        if len(_hits) > 5000:
            for k in [k for k, v in _hits.items() if not v]:
                _hits.pop(k, None)
    return None
