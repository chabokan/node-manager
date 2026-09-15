from server_queue import run_pending_jobs
from core.db import SessionLocal
from host_inventory import read_host_inventory, write_host_inventory
import logging


if __name__ == "__main__":
    try:
        inventory = read_host_inventory(max_age_seconds=60)
        if inventory is None or not inventory.get("disks") or not any(
                disk.get("usage_available", bool(disk.get("mountpoints")))
                for disk in inventory["disks"].values()):
            write_host_inventory()
    except OSError:
        logging.exception("Could not write host inventory")
    with SessionLocal() as db:
        run_pending_jobs(db)
