from typing import Optional

from fastapi import APIRouter, Query

from app.services.asana_service import asana_service

router = APIRouter(prefix="/asanas", tags=["asanas"])


@router.get("")
async def list_asanas(
    difficulty: Optional[int] = Query(None, ge=1, le=5),
    effect: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return asana_service.get_all_asanas(
        difficulty=difficulty,
        effect=effect,
        category=category,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get("/random")
async def random_asana():
    asana = asana_service.get_random_asana()
    if asana is None:
        return {"error": "No asanas found"}
    return asana


@router.get("/{asana_name}")
async def get_asana(asana_name: str):
    asana = asana_service.get_asana_detail(asana_name)
    if asana is None:
        return {"error": "Asana not found"}
    return asana
