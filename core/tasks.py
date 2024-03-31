from fastapi import BackgroundTasks

import crud
from core.db import get_db
from models import ServerRootJob


def process_hub_jobs(jobs):
    pending_jobs = []
    db = next(get_db())
    for job in jobs:
        if job['status'] == "pending":
            pending_jobs.append(job)

    for pending_job in pending_jobs:
        if pending_job['name'] == "restart_server":
            crud.create_server_root_job(db, ServerRootJob(name=pending_job['name'], key=pending_job['key'],data=pending_job['data']))
