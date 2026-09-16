import datetime
import json
import logging
import os

import crud
from core.clock import tehran_now, tehran_naive
from api.helper import (set_job_run_in_hub, create_service, delete_service, service_action,
                        create_backup_task, normal_restore, limit_container_task,
                        mysql_restore, deploy_task)
from core.db import SessionLocal
from host_admin import execute_host_admin_job
from host_inventory import write_host_inventory

logger = logging.getLogger(__name__)
HOST_ONLY_JOBS = frozenset(("host_command", "normal_command", "update_core",
                            "debug_on", "debug_off", "restart_server", "delete_core",
                            "server_nameservers_set", "server_firewall_set",
                            "server_application_action"))


def failure_reason_for(job_name):
    if job_name == "update_core":
        return "core_update_failed"
    if job_name in ("host_command", "normal_command"):
        return "command_failed"
    if job_name in ("create_backup", "restore_backup"):
        return "backup_failed"
    return "operation_failed"


def execute_job(db, job):
    data = json.loads(job.data) if job.data else {}
    if job.name == "service_create":
        create_service(db, job.key, data)
    elif job.name == "service_delete":
        delete_service(db, job.key, data)
    elif job.name == "service_action":
        if not service_action(db, job.key, data):
            raise ValueError("Unsupported service action")
    elif job.name in ("host_command", "normal_command"):
        if os.system(data["command"]) != 0:
            raise RuntimeError("Host command failed")
    elif job.name == "update_core":
        for key in ("technical_name", "backup_server_url", "backup_server_access_key",
                    "backup_server_secret_key", "backup_server_bucket"):
            crud.update_or_create_setting(db, key, data[key])
        if os.system("bash /var/ch-manager/update_core.sh") != 0:
            raise RuntimeError("Core update failed")
    elif job.name == "debug_on":
        if os.system("bash /var/ch-manager/debug_on.sh") != 0:
            raise RuntimeError("Debug enable failed")
    elif job.name == "debug_off":
        if os.system("bash /var/server-connector/utilities/firewall.sh") != 0:
            raise RuntimeError("Debug disable failed")
    elif job.name == "create_backup":
        create_backup_task(db, data["name"], data["platform"])
    elif job.name == "restore_backup":
        platform = data["platform"]["name"]
        if "sql.gz" in data["url"] and platform in ("mysql", "mariadb"):
            mysql_restore(data)
        else:
            normal_restore(data)
    elif job.name == "limit_container":
        limit_container_task(data)
    elif job.name == "deploy_service":
        deploy_task(data)
    elif job.name == "restart_server":
        if os.system("reboot") != 0:
            raise RuntimeError("Reboot failed")
    elif job.name == "delete_core":
        if os.system("cd /var/ch-manager/ && docker compose down") != 0:
            raise RuntimeError("Core deletion failed")
    elif job.name in ("server_nameservers_set", "server_firewall_set",
                      "server_application_action"):
        execute_host_admin_job(job.name, data)
        try:
            write_host_inventory()
        except OSError:
            # The next host-worker tick refreshes inventory; never repeat a
            # completed privileged action due to an unrelated probe failure.
            logger.exception("Could not refresh inventory after job %s", job.key)
    else:
        raise ValueError(f"Unsupported job: {job.name}")


def run_pending_jobs(db, host_mode=True):
    for job in crud.get_server_not_completed_and_pending_root_jobs(db):
        if not host_mode and job.name in HOST_ONLY_JOBS:
            continue
        if job.run_at and tehran_naive(job.run_at) > tehran_now():
            continue
        if job.name == "create_backup" and len(crud.get_server_backup_locked(db)) >= 2:
            continue
        if not crud.claim_server_root_job(db, job):
            continue
        if job.name == "restart_server":
            # Reboot terminates the host worker, so acknowledge it first.
            try:
                crud.set_server_root_job_run(db, job.id)
                try:
                    set_job_run_in_hub(db, job.key)
                except Exception:
                    logger.exception("Could not report shutdown job %s", job.key)
                execute_job(db, job)
            except Exception:
                logger.exception("Shutdown job %s failed after acknowledgement", job.key)
            continue
        try:
            execute_job(db, job)
        except Exception:
            logger.exception("Job %s failed", job.key)
            db.rollback()
            job = crud.get_server_root_job(db, job.key)
            if (job.run_count or 0) < 6:
                job.run_count = (job.run_count or 0) + 1
                job.run_at = tehran_now() + datetime.timedelta(minutes=job.run_count)
                job.locked = False
                job.locked_at = None
                db.commit()
            else:
                crud.fail_server_root_job(db, job)
                try:
                    set_job_run_in_hub(db, job.key, "failed",
                                       failure_reason=failure_reason_for(job.name))
                except Exception:
                    logger.exception("Could not report failed job %s", job.key)
            continue

        # Persist first so an unavailable Hub cannot cause the action to repeat.
        crud.set_server_root_job_run(db, job.id)
        try:
            set_job_run_in_hub(db, job.key)
        except Exception:
            logger.exception("Could not report completed job %s", job.key)


if __name__ == "__main__":
    with SessionLocal() as db:
        run_pending_jobs(db)
