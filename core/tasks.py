import json
import os

import crud
from api.helper import set_job_run_in_hub
from core.db import get_db
from models import ServerRootJob


def process_hub_jobs(jobs):
    pending_jobs = []
    db = next(get_db())
    if jobs:
        for job in jobs:
            if job['status'] == "pending":
                pending_jobs.append(job)

        for pending_job in pending_jobs:
            if pending_job['name'] in ["restart_server", "service_create", "service_delete", 'service_action',
                                       'host_command', 'delete_core', 'debug_on', 'debug_off']:
                crud.create_server_root_job(db, ServerRootJob(name=pending_job['name'], key=pending_job['key'],
                                                              data=json.dumps(pending_job['data'])))
            elif pending_job['name'] == "normal_command":
                set_job_run_in_hub(db, pending_job['key'])
                os.system(pending_job['data']['command'])
