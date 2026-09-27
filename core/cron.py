import json
import datetime
import logging
import os
import time

import requests
from fastapi_restful.tasks import repeat_every

import crud
from core.clock import tehran_now
from core.logging_setup import log_event, log_transition
from api.helper import get_server_ip, get_system_info, cal_all_containers_stats, containers_usages
from core.db import SessionLocal
from main import app
from models import ServerUsage
from server_queue import run_pending_jobs
from host_inventory import read_host_inventory


logger = logging.getLogger(__name__)
_hub_sync_state = {}


@app.on_event("startup")
@repeat_every(seconds=(60 * 60 * 3), raise_exceptions=True)
def check_main_dir_sizes() -> None:
    os.system("timeout 1800 duc index /storage/ -m 2")
    os.system("timeout 1800 duc index /home/ -m 2")
    os.system("timeout 1800 duc index /home2/ -m 2")


@app.on_event("startup")
@repeat_every(seconds=300, raise_exceptions=True)
def server_sync() -> None:
    with SessionLocal() as db:
        token_setting = crud.get_setting(db, "token")
        if not token_setting:
            return
        token = token_setting.value
        base_hub_setting = crud.get_setting(db, "base_hub_url")
        base_hub_url = base_hub_setting.value if base_hub_setting else None
        services_usages = containers_usages(db)
    if token:
        server_info = get_system_info()
        if server_info.get('disk_available') is False:
            return
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
            "disk_data": server_info['disk'],
            "services-usages": services_usages
        }
        host_inventory = read_host_inventory()
        if host_inventory:
            data["host_inventory"] = host_inventory
        headers = {
            "Content-Type": "application/json",
        }
        try:
            r = requests.post(f"https://{base_hub_url}/fa/api/v1/servers/connect-server/", headers=headers,
                              data=json.dumps(data), timeout=45)
            if r.status_code == 200:
                # Auto-heal compares this against the host's own reachability.
                with SessionLocal() as sync_db:
                    crud.update_or_create_setting(sync_db, "hub_last_seen", str(time.time()))
                log_transition(_hub_sync_state, "sync", True, logger,
                               "hub_sync_ok", "hub_sync_ok", status=r.status_code)
                # crud.create_setting(db, Setting(key="backup_server_url", value=r.json()['backup_server_url']))
                # crud.create_setting(db, Setting(key="backup_server_access_key", value=r.json()['backup_server_access_key']))
                # crud.create_setting(db, Setting(key="backup_server_secret_key", value=r.json()['backup_server_secret_key']))
            else:
                log_transition(_hub_sync_state, "sync", False, logger,
                               "hub_sync_ok", "hub_sync_rejected", status=r.status_code)
        except requests.RequestException as exc:
            log_transition(_hub_sync_state, "sync", False, logger,
                           "hub_sync_ok", "hub_sync_failed", error=type(exc).__name__)
        except Exception as exc:
            log_event(logger, "hub_sync_error", level=logging.ERROR, error=type(exc).__name__)


@app.on_event("startup")
@repeat_every(seconds=60, raise_exceptions=True)
def monitor_server_usage() -> None:
    with SessionLocal() as db:
        enabled = bool(crud.get_setting(db, "token"))
    if enabled:
        server_info = get_system_info()
        if server_info.get('disk_available') is False:
            return
        with SessionLocal() as db:
            crud.create_server_usage(db,
                                     ServerUsage(
                                         ram=server_info['ram']['used'],
                                         cpu=server_info['cpu']['usage'],
                                         disk=server_info['all_disk_usage']
                                     ))


@app.on_event("startup")
@repeat_every(seconds=30, raise_exceptions=True)
def get_jobs_from_hub() -> None:
    with SessionLocal() as db:
        token_present = bool(crud.get_setting(db, "token"))
    if token_present:
        headers = {
            "Content-Type": "application/json",
        }
        try:
            requests.get("http://127.0.0.1/api/v1/jobs/", headers=headers, timeout=45)
            log_transition(_hub_sync_state, "local_api", True, logger,
                           "local_api_ok", "local_api_ok")
        except requests.RequestException as exc:
            log_transition(_hub_sync_state, "local_api", False, logger,
                           "local_api_ok", "local_api_unreachable", error=type(exc).__name__)


@app.on_event("startup")
@repeat_every(seconds=30, raise_exceptions=True)
def run_server_jobs() -> None:
    with SessionLocal() as db:
        if crud.get_setting(db, "token"):
            try:
                run_pending_jobs(db, host_mode=False)
            except Exception as exc:
                log_event(logger, "job_worker_failed", level=logging.ERROR,
                          worker="web", error=type(exc).__name__)


@app.on_event("startup")
@repeat_every(seconds=60, raise_exceptions=True)
def monitor_services_usage() -> None:
    with SessionLocal() as db:
        enabled = bool(crud.get_setting(db, "token"))
    if enabled:
        with SessionLocal() as db:
            try:
                cal_all_containers_stats(db)
            except Exception as exc:
                log_event(logger, "service_usage_failed", level=logging.ERROR,
                          error=type(exc).__name__)


@app.on_event("startup")
@repeat_every(seconds=(60 * 10), raise_exceptions=True)
def reset_locked_root_jobs() -> None:
    with SessionLocal() as db:
        if crud.get_setting(db, "token"):
            crud.unlock_stale_server_root_jobs(
                db, tehran_now() - datetime.timedelta(seconds=(60 * 30)))


@app.on_event("startup")
def start_containers() -> None:
    with SessionLocal() as db:
        token_setting = crud.get_setting(db, "token")
        if not token_setting:
            return
        token = token_setting.value
        base_hub_setting = crud.get_setting(db, "base_hub_url")
        base_hub_url = base_hub_setting.value if base_hub_setting else None
        data = {"token": token}
    if token:
        headers = {"Content-Type": "application/json", }
        try:
            r = requests.post(f"https://{base_hub_url}/fa/api/v1/servers/get-server-services/", headers=headers,
                              data=json.dumps(data), timeout=45)
            if r.status_code == 200:
                for service in r.json()['data']:
                    if service['status'] == "on":
                        os.system(f"docker start {service['main_name']}")
                    elif service['status'] == "off":
                        os.system(f"docker stop {service['main_name']}")
        except:
            pass

#
# @app.on_event("startup")
# @repeat_every(seconds=(60 * 60 * 24))
# def clean_old_jobs() -> None:
#     db = next(get_db())
#     if crud.get_setting(db, "token"):
#         jobs = crud.get_all_jobs(db)
#         for job in jobs:
#             if job.created and job.created <= datetime.datetime.now() - datetime.timedelta(days=14):
#                 db.delete(job)
#                 db.commit()
#
#
# @app.on_event("startup")
# @repeat_every(seconds=(60 * 60 * 24))
# def clean_old_server_usage() -> None:
#     db = next(get_db())
#     if crud.get_setting(db, "token"):
#         usages = crud.get_full_server_usages(db)
#         for usage in usages:
#             if usage.created and usage.created <= datetime.datetime.now() - datetime.timedelta(days=14):
#                 db.delete(usage)
#                 db.commit()
#
#
# @app.on_event("startup")
# @repeat_every(seconds=(60 * 60 * 24))
# def clean_old_service_usage() -> None:
#     db = next(get_db())
#     if crud.get_setting(db, "token"):
#         usages = crud.get_full_services_usages(db)
#         for usage in usages:
#             if usage.created and usage.created <= datetime.datetime.now() - datetime.timedelta(days=14):
#                 db.delete(usage)
#                 db.commit()
