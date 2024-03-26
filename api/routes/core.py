from fastapi import APIRouter

from api.helper import get_system_info

router = APIRouter()


@router.get("/connect/")
async def connect():
    return {"msg": get_system_info()}
