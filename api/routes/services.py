from fastapi import APIRouter, Depends
import crud
from api.helper import service_logs
from core.db import get_db

router = APIRouter()


@router.get("/{name}/logs/")
async def logs(name, db=Depends(get_db)):
    container_logs = service_logs(name)
    return {"success": True, "logs": container_logs}


@router.get("/{name}/usages/")
async def logs(name, db=Depends(get_db)):
    container_usages = crud.get_single_service_usages(db, name)
    return {"success": True, "usages": container_usages}
