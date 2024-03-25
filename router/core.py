from fastapi import APIRouter

router = APIRouter(prefix="/core", tags=["core"])


@router.get("/connect/")
async def connect():
    return {"msg": "ok"}
