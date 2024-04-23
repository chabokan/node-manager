import datetime
import json
import os

import dateutil

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
                                       'host_command', 'delete_core', 'debug_on', 'debug_off', 'create_backup',
                                       'restore_backup', 'limit_container', 'update_core']:

                run_at = ""
                try:
                    run_at = dateutil.parser.parse(pending_job['run_at'])
                except:
                    pass
                if not run_at:
                    run_at = datetime.datetime.now()
                if not crud.get_server_root_job(db, pending_job['key']):
                    crud.create_server_root_job(db, ServerRootJob(name=pending_job['name'], key=pending_job['key'],
                                                                  data=json.dumps(pending_job['data']),
                                                                  run_at=run_at))
                else:
                    server_root_job = crud.get_server_root_job(db, pending_job['key'])
                    if server_root_job.completed_at:
                        set_job_run_in_hub(db, pending_job['key'])

            elif pending_job['name'] == "normal_command":
                set_job_run_in_hub(db, pending_job['key'])
                os.system(pending_job['data']['command'])
