"""Квитанции оплаты не должны быть публичными.

Регрессия: файлы лежали в статическом /uploads/receipts, и ссылка без подписи
отдавала чек любому, кто её знал. Теперь только /api/v1/media/receipts/{f}
с exp+sig.
"""

import httpx
import pytest

from app.main import app
from app.services.media_urls import sign_receipt_url


@pytest.fixture
def receipt_dir(tmp_path, monkeypatch):
    from app.routers import media

    d = tmp_path / "receipts"
    d.mkdir()
    (d / "receipt.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    monkeypatch.setattr(media, "RECEIPT_DIR", str(d))
    return d


async def test_receipt_requires_signature(receipt_dir):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        unsigned = await c.get("/api/v1/media/receipts/receipt.jpg")
        assert unsigned.status_code == 403

        bad_sig = await c.get(
            "/api/v1/media/receipts/receipt.jpg?exp=9999999999&sig=deadbeef"
        )
        assert bad_sig.status_code == 403

        old_public = await c.get("/uploads/receipts/receipt.jpg")
        assert old_public.status_code == 404


async def test_receipt_signed_url_works(receipt_dir):
    signed = sign_receipt_url("/uploads/receipts/receipt.jpg")
    assert signed and signed.startswith("/api/v1/media/receipts/")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        res = await c.get(signed)
        assert res.status_code == 200
        assert res.content == b"\xff\xd8\xff\xe0fake-jpeg"


async def test_receipt_rejects_other_extensions(receipt_dir):
    signed = sign_receipt_url("../../etc/passwd")
    assert signed is not None
    # basename отбрасывает traversal, а расширение не в белом списке.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        res = await c.get(signed)
        assert res.status_code == 404
