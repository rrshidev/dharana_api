from fastapi import APIRouter

from app.services.asana_service import asana_service

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("")
async def list_categories():
    return asana_service.get_all_categories()


@router.get("/{category_id}/asanas")
async def category_asanas(category_id: str):
    asanas = asana_service.get_category_asanas(category_id)
    if not asanas:
        return {"error": "Category not found", "items": []}
    return {"category_id": category_id, "items": asanas}
