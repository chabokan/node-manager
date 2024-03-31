import os

import crud
from api.helper import set_job_run_in_hub
from core.db import get_db

db = next(get_db())
jobs = crud.get_server_not_run_root_jobs(db)

for job in jobs:
    if job.name == "restart":
        crud.set_server_root_job_run(db, job.id)
        set_job_run_in_hub(db, job.key)
        os.system("reboot")
