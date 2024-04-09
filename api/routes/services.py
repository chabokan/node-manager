from fastapi import APIRouter, Depends
import crud
from core.db import get_db

router = APIRouter()


@router.get("/logs/")
async def logs(db=Depends(get_db)):

    return
