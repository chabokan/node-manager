import json

import crud
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
            if pending_job['name'] in ["restart_server", "service_create", "service_delete", 'service_action']:
                crud.create_server_root_job(db, ServerRootJob(name=pending_job['name'], key=pending_job['key'],
                                                              data=json.dumps(pending_job['data'])))
            # elif pending_job['name'] == "service_action":
            #     pass
