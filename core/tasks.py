import crud
from api.helper import create_service, delete_service
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
            if pending_job['name'] == "restart_server":
                crud.create_server_root_job(db, ServerRootJob(name=pending_job['name'], key=pending_job['key'],
                                                              data=pending_job['data']))
            elif pending_job['name'] == "create_service":
                create_service(pending_job['data'])
            elif pending_job['name'] == "delete_service":
                delete_service(pending_job['data'])
