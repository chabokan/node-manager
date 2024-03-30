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
        "ram": server_info['ram']['total'],
        "cpu": server_info['cpu']['count'],
        "disk": server_info['all_disk_space'],
        "ip": ip,
        "ram_usage": server_info['ram']['used'],
        "cpu_usage": server_info['cpu']['usage'],
        "disk_usage": server_info['all_disk_usage'],
        "disk_data": server_info['disk']
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


@router.get("/test/")
async def test():
    db = next(get_db())
    return crud.get_setting(db, "token")
