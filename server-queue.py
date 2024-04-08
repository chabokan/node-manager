import os

import crud
from api.helper import set_job_run_in_hub, create_service, delete_service
from core.db import get_db

db = next(get_db())
jobs = crud.get_server_not_run_root_jobs(db)

for job in jobs:
    if job.name == "restart_server":
        crud.set_server_root_job_run(db, job.id)
        set_job_run_in_hub(db, job.key)
        os.system("reboot")
    elif job.name == "create_service":
        create_service(db, job.key, job.data)
        crud.set_server_root_job_run(db, job.id)
    elif job.name == "delete_service":
        delete_service(db, job.key, job.data)
        crud.set_server_root_job_run(db, job.id)
