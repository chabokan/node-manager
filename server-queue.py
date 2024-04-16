import json
import os

import crud
from api.helper import set_job_run_in_hub, create_service, delete_service, service_action
from core.db import get_db

db = next(get_db())
jobs = crud.get_server_not_run_root_jobs(db)

for job in jobs:
    if job.name == "restart_server":
        crud.set_server_root_job_run(db, job.id)
        set_job_run_in_hub(db, job.key)
        os.system("reboot")
    elif job.name == "service_create":
        data = json.loads(job.data)
        create_service(db, job.key, data)
        crud.set_server_root_job_run(db, job.id)
    elif job.name == "service_delete":
        data = json.loads(job.data)
        delete_service(db, job.key, data)
        crud.set_server_root_job_run(db, job.id)
    elif job.name == "service_action":
        data = json.loads(job.data)
        service_action(db, job.key, data)
        crud.set_server_root_job_run(db, job.id)
    elif job.name == "host_command":
        data = json.loads(job.data)
        set_job_run_in_hub(db, job.key)
        crud.set_server_root_job_run(db, job.id)
        os.system(data['command'])
    elif job.name == "update_core":
        cmd_code = os.system("bash /var/manager/update_core.sh")
        if cmd_code == 0:
            set_job_run_in_hub(db, job.key)
            crud.set_server_root_job_run(db, job.id)
    elif job.name == "delete_core":
        set_job_run_in_hub(db, job.key)
        crud.set_server_root_job_run(db, job.id)
        data = json.loads(job.data)
        cmd_code = os.system("cd /var/manager/ && docker compose down && rm -rf /var/manager/")
    elif job.name == "debug_on":
        cmd_code = os.system("bash /var/manager/debug_on.sh")
        if cmd_code == 0:
            set_job_run_in_hub(db, job.key)
            crud.set_server_root_job_run(db, job.id)
        else:
            raise Exception(f"error cmd_code: {cmd_code} ")
    elif job.name == "debug_off":
        cmd_code = os.system(
            "curl -s https://raw.githubusercontent.com/chabokan/server-connector/main/firewall.sh | source")
        if cmd_code == 0:
            set_job_run_in_hub(db, job.key)
            crud.set_server_root_job_run(db, job.id)
        else:
            raise Exception(f"error cmd_code: {cmd_code}")
