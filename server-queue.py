import logging

from auto_heal import run_auto_heal
from server_queue import run_pending_jobs
from core.db import SessionLocal
from core.logging_setup import configure_logging, log_event
from host_inventory import read_host_inventory, write_host_inventory


logger = logging.getLogger(__name__)


if __name__ == "__main__":
    configure_logging(service="host")
    log_event(logger, "host_worker_started")
    try:
        inventory = read_host_inventory(max_age_seconds=60)
        if inventory is None or not inventory.get("disks") or not any(
                disk.get("usage_available", bool(disk.get("mountpoints")))
                for disk in inventory["disks"].values()):
            write_host_inventory()
            log_event(logger, "host_inventory_refreshed", source="host_worker")
    except OSError as exc:
        log_event(logger, "host_inventory_refresh_failed", level=logging.ERROR,
                  error=type(exc).__name__)
    with SessionLocal() as db:
        try:
            run_auto_heal(db)
        except Exception as exc:
            log_event(logger, "auto_heal_crashed", level=logging.ERROR,
                      error=type(exc).__name__)
        run_pending_jobs(db)
    log_event(logger, "host_worker_finished")
