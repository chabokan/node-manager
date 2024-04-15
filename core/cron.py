import json

import requests
from fastapi import BackgroundTasks
from fastapi_restful.tasks import repeat_every

import crud
from api.helper import get_server_ip, get_system_info, cal_all_containers_stats
from core.db import get_db
from core.tasks import process_hub_jobs
from main import app
from models import ServerUsage


@app.on_event("startup")
@repeat_every(seconds=300, raise_exceptions=True)
def server_sync() -> None:
    db = next(get_db())
    if crud.get_setting(db, "token"):
        server_info = get_system_info()
        ip = get_server_ip()
        data = {
            "token": crud.get_setting(db, "token").value,
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
                          data=json.dumps(data), timeout=15)


@app.on_event("startup")
@repeat_every(seconds=60, raise_exceptions=True)
def monitor_server_usage() -> None:
    db = next(get_db())
    if crud.get_setting(db, "token"):
        server_info = get_system_info()
        crud.create_server_usage(db,
                                 ServerUsage(
                                     ram=server_info['ram']['used'],
                                     cpu=server_info['cpu']['usage'],
                                     disk=server_info['all_disk_usage']
                                 ))


@app.on_event("startup")
@repeat_every(seconds=30, raise_exceptions=True)
def get_jobs_from_hub() -> None:
    db = next(get_db())
    if crud.get_setting(db, "token"):
        headers = {
            "Content-Type": "application/json",
        }
        r = requests.get("http://0.0.0.0/api/v1/jobs/", headers=headers, timeout=15)


@app.on_event("startup")
@repeat_every(seconds=60, raise_exceptions=True)
def monitor_services_usage() -> None:
    db = next(get_db())
    if crud.get_setting(db, "token"):
        cal_all_containers_stats(db)
