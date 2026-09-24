from typing import Optional

from fastapi import APIRouter

from app.services.asana_service import asana_service

router = APIRouter(tags=["content"])


@router.get("/basics")
async def list_basics(lang: Optional[str] = None):
    return asana_service.get_basics(lang)


@router.get("/steps")
async def list_steps(lang: Optional[str] = None):
    return asana_service.get_steps(lang)
