from server_queue import run_pending_jobs
from core.db import SessionLocal
from host_inventory import read_host_inventory, write_host_inventory
import logging


if __name__ == "__main__":
    try:
        if read_host_inventory(max_age_seconds=300) is None:
            write_host_inventory()
    except OSError:
        logging.exception("Could not write host inventory")
    with SessionLocal() as db:
        run_pending_jobs(db)
