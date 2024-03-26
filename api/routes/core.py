from fastapi import APIRouter
import requests

from api.helper import get_system_info

router = APIRouter()


@router.post("/connect/")
async def connect(token):
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
    requests.post("https://hub.chabokan.net/fa/api/v1/servers/connect-server/", headers=headers, data=data)
    return data
