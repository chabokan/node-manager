from server_queue import run_pending_jobs
from core.db import get_db
from host_inventory import read_host_inventory, write_host_inventory
import logging


if __name__ == "__main__":
    try:
        if read_host_inventory(max_age_seconds=300) is None:
            write_host_inventory()
    except OSError:
        logging.exception("Could not write host inventory")
    db = next(get_db())
    try:
        run_pending_jobs(db)
    finally:
        db.close()
