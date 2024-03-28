from fastapi import APIRouter, Depends
import requests

import crud
from api.helper import get_system_info
from core.db import get_db
from models import Setting

router = APIRouter()


@router.post("/connect/")
async def connect(token, db=Depends(get_db)):
    if crud.get_setting(db, key="token"):
        server_info = get_system_info()
        data = {
            "token": token,
            "ram": server_info['ram'],
            "cpu": server_info['cpu'],
            "disk": server_info['disk'],
            "ip": "192.168.1.1",
            "ram_usage": server_info['ram_usage'],
            "cpu_usage": server_info['cpu_usage'],
            "disk_usage": server_info['disk_usage'],
        }
        headers = {
            "Content-Type": "application/json",
        }
        r = requests.post("https://hub.chabokan.net/fa/api/v1/servers/connect-server/", headers=headers, data=data)
        if r.status_code == 200:
            crud.create_setting(db, Setting(key="token", value=token))
        return {"success": True, "message": "node connected to chabokan successfully."}
    else:
        return {"success": False, "message": "node connected before!"}
