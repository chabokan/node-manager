from server_queue import run_pending_jobs
from core.db import get_db


if __name__ == "__main__":
    db = next(get_db())
    try:
        run_pending_jobs(db)
    finally:
        db.close()
