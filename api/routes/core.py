import json
import os

from fastapi import APIRouter, Depends, BackgroundTasks
import requests

import crud
from api.helper import get_system_info, run_bash_command, get_server_ip, process_jobs
from core.db import get_db
from core.tasks import process_hub_jobs
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
    try:
        r = requests.post("https://hub.chabokan.net/fa/api/v1/servers/connect-server/", headers=headers,
                          data=json.dumps(data), timeout=60)
        if r.status_code == 200:
            crud.create_setting(db, Setting(key="token", value=token))
            crud.create_setting(db, Setting(key="technical_name", value=r.json()['technical_name']))
            crud.create_setting(db, Setting(key="backup_server_url", value=r.json()['backup_server_url']))
            crud.create_setting(db, Setting(key="backup_server_bucket", value=r.json()['backup_server_bucket']))
            crud.create_setting(db, Setting(key="backup_server_access_key", value=r.json()['backup_server_access_key']))
            crud.create_setting(db, Setting(key="backup_server_secret_key", value=r.json()['backup_server_secret_key']))

            return {"success": True, "message": "node connected to chabokan successfully."}
        else:
            return {"success": False, "status": r.status_code, "response": r.json()}
    except:
        return {"success": False, "status": 543, "response": {}}


@router.get("/jobs/")
async def jobs(background_tasks: BackgroundTasks, db=Depends(get_db)):
    if crud.get_setting(db, key="token"):
        data = {"token": crud.get_setting(db, "token").value}
        headers = {"Content-Type": "application/json", }
        try:
            r = requests.post("https://hub.chabokan.net/fa/api/v1/servers/get-server-jobs/", headers=headers,
                              data=json.dumps(data), timeout=45)
            if r.status_code == 200:
                process_jobs(db, r.json()['data'])
        except:
            pass
        return {"success": True}
