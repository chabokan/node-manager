import json
import logging
from typing import Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, Body, Depends, HTTPException
import requests

import crud
from api.helper import get_system_info, get_server_ip, process_jobs
from core.db import get_db
from models import Setting

router = APIRouter()
logger = logging.getLogger(__name__)


def normalize_hub_host(value: str) -> str:
    value = value.strip()
    parsed = urlsplit(value if "://" in value else f"https://{value}")
    try:
        port = parsed.port
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid hub URL")
    if (parsed.scheme.lower() != "https" or not parsed.hostname or
            parsed.username or parsed.password or parsed.path not in ("", "/") or
            parsed.query or parsed.fragment or port == 0):
        raise HTTPException(status_code=422, detail="HUB_URL must be an HTTPS hostname or origin")
    return f"{parsed.hostname}:{port}" if port else parsed.hostname


@router.post("/connect/")
async def connect(token: Optional[str] = None, hub_url: Optional[str] = None,
                  payload: Optional[dict] = Body(default=None), db=Depends(get_db)):
    payload = payload or {}
    token = token if token is not None else payload.get("token")
    hub_url = hub_url if hub_url is not None else payload.get("hub_url", "hub.chabokan.net")
    if not isinstance(token, str) or not token:
        raise HTTPException(status_code=422, detail="TOKEN is required")
    if not isinstance(hub_url, str):
        raise HTTPException(status_code=422, detail="Invalid hub URL")
    base_hub_url = normalize_hub_host(hub_url)
    stored_token = crud.get_setting(db, key="token")
    connection_keys = ("technical_name", "backup_server_url", "backup_server_bucket",
                       "backup_server_access_key", "backup_server_secret_key", "base_hub_url")
    if stored_token and all(crud.get_setting(db, key) for key in connection_keys):
        stored_hub = crud.get_setting(db, "base_hub_url")
        if stored_token.value == token and stored_hub.value == base_hub_url:
            return {"success": True, "message": "node already connected to chabokan."}
        return {"success": False, "message": "node is already connected with another token or hub"}
    if stored_token and stored_token.value != token:
        return {"success": False, "message": "incomplete connection uses another token"}

    server_info = get_system_info()
    if server_info.get('disk_available') is False:
        raise HTTPException(status_code=503, detail="Host disk inventory is not ready")
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
        r = requests.post(f"https://{base_hub_url}/fa/api/v1/servers/connect-server/", headers=headers,
                          data=json.dumps(data), timeout=60)
        response = r.json()
        required = ("technical_name", "backup_server_url", "backup_server_bucket",
                    "backup_server_access_key", "backup_server_secret_key")
        if r.status_code != 200 or response.get("success") is not True or any(
                key not in response or response[key] is None for key in required):
            return {"success": False, "status": r.status_code, "response": response}

        values = {"token": token, "base_hub_url": base_hub_url}
        values.update({key: response[key] for key in required})
        for key, value in values.items():
            setting = crud.get_setting(db, key)
            if setting:
                setting.value = value
            else:
                db.add(Setting(key=key, value=value))
        db.commit()
        return {"success": True, "message": "node connected to chabokan successfully."}
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Could not connect to hub: %s", exc)
        return {"success": False, "status": 543, "response": {}}
    except Exception:
        db.rollback()
        logger.exception("Could not save hub connection")
        return {"success": False, "status": 543, "response": {}}


@router.get("/jobs/")
async def jobs(db=Depends(get_db)):
    token_setting = crud.get_setting(db, key="token")
    if not token_setting:
        return {"success": False, "message": "node is not connected"}
    data = {"token": token_setting.value}
    hub_setting = crud.get_setting(db, "base_hub_url")
    if not hub_setting:
        hub_setting = Setting(key="base_hub_url", value="hub.chabokan.net")
        db.add(hub_setting)
        db.commit()
    elif not hub_setting.value:
        hub_setting.value = "hub.chabokan.net"
        db.commit()
    base_hub_url = hub_setting.value

    headers = {"Content-Type": "application/json"}

    try:
        r = requests.post(f"https://{base_hub_url}/fa/api/v1/servers/get-server-jobs/", headers=headers,
                          data=json.dumps(data), timeout=45)
        r.raise_for_status()
        response = r.json()
        if response.get("success") is not True:
            raise ValueError("Hub rejected job request")
        process_jobs(db, response['data'])
        return {"success": True}
    except (requests.RequestException, ValueError, KeyError) as exc:
        logger.warning("Could not fetch jobs from hub: %s", exc)
        return {"success": False}
