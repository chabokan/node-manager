import json
import datetime
import os

import requests
from fastapi import BackgroundTasks
from fastapi_restful.tasks import repeat_every

import crud
from api.helper import get_server_ip, get_system_info, cal_all_containers_stats, containers_usages
from core.db import get_db
from core.tasks import process_hub_jobs
from main import app
from models import ServerUsage, Setting


@app.on_event("startup")
@repeat_every(seconds=(60 * 60 * 3), raise_exceptions=True)
def check_main_dir_sizes() -> None:
    db = next(get_db())
    os.system("timeout 1800 duc index /storage/ -m 2")
    os.system("timeout 1800 duc index /home/ -m 2")
    os.system("timeout 1800 duc index /home2/ -m 2")


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
            "disk_data": server_info['disk'],
            "services-usages": containers_usages(db)
        }
        headers = {
            "Content-Type": "application/json",
        }
        try:
            r = requests.post("https://hub.chabokan.net/fa/api/v1/servers/connect-server/", headers=headers,
                              data=json.dumps(data), timeout=45)
            if r.status_code == 200:
                pass
                # crud.create_setting(db, Setting(key="backup_server_url", value=r.json()['backup_server_url']))
                # crud.create_setting(db, Setting(key="backup_server_access_key", value=r.json()['backup_server_access_key']))
                # crud.create_setting(db, Setting(key="backup_server_secret_key", value=r.json()['backup_server_secret_key']))
        except:
            pass


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
        try:
            r = requests.get("http://0.0.0.0/api/v1/jobs/", headers=headers, timeout=45)
        except:
            pass


@app.on_event("startup")
@repeat_every(seconds=60, raise_exceptions=True)
def monitor_services_usage() -> None:
    db = next(get_db())
    if crud.get_setting(db, "token"):
        cal_all_containers_stats(db)


@app.on_event("startup")
@repeat_every(seconds=(60 * 10), raise_exceptions=True)
def reset_locked_root_jobs() -> None:
    db = next(get_db())
    if crud.get_setting(db, "token"):
        jobs = crud.get_server_locked_root_jobs(db)
        for job in jobs:
            if job.locked_at and job.locked_at <= datetime.datetime.now() - datetime.timedelta(seconds=(60 * 30)):
                job.locked = False
                job.locked_at = None
                db.commit()


@app.on_event("startup")
def start_containers() -> None:
    db = next(get_db())
    if crud.get_setting(db, "token"):
        data = {"token": crud.get_setting(db, "token").value}
        headers = {"Content-Type": "application/json", }
        try:
            r = requests.post("https://hub.chabokan.net/fa/api/v1/servers/get-server-services/", headers=headers,
                              data=json.dumps(data), timeout=45)
            if r.status_code == 200:
                for service in r.json()['data']:
                    if service['status'] == "on":
                        os.system(f"docker start {service['main_name']}")
                    elif service['status'] == "off":
                        os.system(f"docker stop {service['main_name']}")
        except:
            pass
