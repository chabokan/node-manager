from fastapi import APIRouter

router = APIRouter()


@router.get("/connect/")
async def connect():
    return {"msg": "ok"}
