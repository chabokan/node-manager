import json
import os

from fastapi import APIRouter, Depends
import requests

import crud
from api.helper import get_system_info, run_bash_command, get_server_ip
from core.db import get_db
from models import Setting

router = APIRouter()


@router.get("/usages/")
async def usage(period: str = "1h", db=Depends(get_db)):
    return crud.get_server_usages_for_period(db, period)
