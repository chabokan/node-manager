import json

import requests
from fastapi_restful.tasks import repeat_every

import crud
from api.helper import get_server_ip, get_system_info
from core.db import get_db
from main import app


@app.on_event("startup")
@repeat_every(seconds=5)
def server_sync() -> None:
    db = next(get_db())
    if crud.get_setting(db, "token"):
        server_info = get_system_info()
        ip = get_server_ip()
        data = {
            "token": crud.get_setting(db, "token"),
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
        requests.get("https://php-ee6q4l.chbk.run/")

    #     r = requests.post("https://hub.chabokan.net/fa/api/v1/servers/connect-server/", headers=headers,
    #                       data=json.dumps(data))
