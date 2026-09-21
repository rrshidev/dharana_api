from typing import Optional

from fastapi import APIRouter, Query, Header

from app.services.asana_service import asana_service, resolve_lang

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("")
async def list_categories(
    lang: Optional[str] = Query(None),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
):
    return asana_service.get_all_categories(resolve_lang(lang, accept_language))


@router.get("/{category_id}/asanas")
async def category_asanas(
    category_id: str,
    lang: Optional[str] = Query(None),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
):
    asanas = asana_service.get_category_asanas(
        category_id, resolve_lang(lang, accept_language)
    )
    if not asanas:
        return {"error": "Category not found", "items": []}
    return {"category_id": category_id, "items": asanas}
