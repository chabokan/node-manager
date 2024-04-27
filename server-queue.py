import datetime
import json
import os
import crud
from api.helper import set_job_run_in_hub, create_service, delete_service, service_action, create_backup_task, \
    normal_restore, limit_container_task
from core.db import get_db

db = next(get_db())
jobs = crud.get_server_not_completed_and_pending_root_jobs(db)

for job in jobs:
    try:
        if job.run_at <= datetime.datetime.now():
            job.locked = True
            job.locked_at = datetime.datetime.now()
            db.commit()
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
                job_complete = service_action(db, job.key, data)
                if job_complete:
                    set_job_run_in_hub(db, job.key)
                    crud.set_server_root_job_run(db, job.id)

            elif job.name == "host_command":
                data = json.loads(job.data)
                set_job_run_in_hub(db, job.key)
                crud.set_server_root_job_run(db, job.id)
                os.system(data['command'])
            elif job.name == "update_core":
                cmd_code = os.system("bash /var/ch-manager/update_core.sh")
                if cmd_code == 0:
                    set_job_run_in_hub(db, job.key)
                    crud.set_server_root_job_run(db, job.id)
            elif job.name == "delete_core":
                set_job_run_in_hub(db, job.key)
                crud.set_server_root_job_run(db, job.id)
                data = json.loads(job.data)
                cmd_code = os.system("cd /var/ch-manager/ && docker compose down")
            elif job.name == "debug_on":
                cmd_code = os.system("bash /var/ch-manager/debug_on.sh")
                if cmd_code == 0:
                    set_job_run_in_hub(db, job.key)
                    crud.set_server_root_job_run(db, job.id)
                else:
                    raise Exception(f"error cmd_code: {cmd_code} ")
            elif job.name == "debug_off":
                cmd_code = os.system(
                    "curl -s https://raw.githubusercontent.com/chabokan/server-connector/main/firewall.sh | bash -s")
                if cmd_code == 0:
                    set_job_run_in_hub(db, job.key)
                    crud.set_server_root_job_run(db, job.id)
                else:
                    raise Exception(f"error cmd_code: {cmd_code}")
            elif job.name == "create_backup":
                data = json.loads(job.data)
                create_backup_task(db, data['name'], data['platform'])
                set_job_run_in_hub(db, job.key)
                crud.set_server_root_job_run(db, job.id)
            elif job.name == "restore_backup":
                data = json.loads(job.data)
                normal_restore(db, data)
                set_job_run_in_hub(db, job.key)
                crud.set_server_root_job_run(db, job.id)
            elif job.name == "limit_container":
                data = json.loads(job.data)
                limit_container_task(data)
                crud.set_server_root_job_run(db, job.id)
    except:
        if job.run_count < 6:
            if job.run_count:
                job.run_count += 1
            else:
                job.run_count = 1
            job.run_at = datetime.datetime.now() + datetime.timedelta(seconds=(60 * job.run_count))
            job.locked = False
            db.commit()
        else:
            set_job_run_in_hub(db, job.key, "failed")
            job.status = "failed"
            job.locked = False
            db.commit()
