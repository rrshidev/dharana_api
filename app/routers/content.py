from fastapi import APIRouter

from app.services.asana_service import asana_service

router = APIRouter(tags=["content"])


@router.get("/basics")
async def list_basics():
    return asana_service.get_basics()


@router.get("/steps")
async def list_steps():
    return asana_service.get_steps()
