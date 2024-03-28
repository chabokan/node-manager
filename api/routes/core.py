import json
import os

from fastapi import APIRouter, Depends
import requests

import crud
from api.helper import get_system_info, run_bash_command, get_server_ip
from core.db import get_db
from models import Setting

router = APIRouter()


@router.post("/connect/")
async def connect(token: str, db=Depends(get_db)):
    if crud.get_setting(db, key="token"):
        return {"success": False, "message": "node connected before!"}

    server_info = get_system_info()
    ip = get_server_ip()
    data = {
        "token": token,
        "ram": "8",
        "cpu": "2",
        "disk": "100",
        "ip": ip,
        "ram_usage": "3",
        "cpu_usage": "1",
        "disk_usage": "5",
        "disk_data": [
            {"s": "b"},
            {"s": "b"},
        ]
    }
    headers = {
        "Content-Type": "application/json",
    }
    r = requests.post("https://hub.chabokan.net/fa/api/v1/servers/connect-server/", headers=headers,
                      data=json.dumps(data))
    if r.status_code == 200:
        crud.create_setting(db, Setting(key="token", value=token))
        return {"success": True, "message": "node connected to chabokan successfully."}
    else:
        return {"success": False, "message": "some problem.", "r": r.json()}
