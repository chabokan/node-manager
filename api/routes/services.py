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
    container_logs = service_logs(name)
    usages = container.usages.filter(type="minutely").order_by('-created')[:60]
    if request.GET.get('period') == "3h":
        usages = container.usages.filter(type="minutely").order_by('-created')[:180]
    elif request.GET.get('period') == "12h":
        usages = container.usages.filter(type="hourly").order_by('-created')[:12]
    elif request.GET.get('period') == "24h":
        usages = container.usages.filter(type="hourly").order_by('-created')[:24]
    elif request.GET.get('period') == "7d":
        usages = container.usages.filter(type="daily").order_by('-created')[:7]
    elif request.GET.get('period') == "14d":
        usages = container.usages.filter(type="daily").order_by('-created')[:14]
    elif request.GET.get('period') == "30d":
        usages = container.usages.filter(type="daily").order_by('-created')[:30]

    return {"success": True, "logs": container_logs}
