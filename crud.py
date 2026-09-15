import datetime

from sqlalchemy.orm import Session
from sqlalchemy import update, func, cast, Integer
from core.clock import tehran_now
from models import Setting, ServerUsage, ServerRootJob, ServiceUsage
from typing import List


def create_setting(session: Session, request: Setting) -> Setting:
    db_obj = Setting(
        key=request.key,
        value=request.value,
    )
    session.add(db_obj)
    session.commit()
    session.refresh(db_obj)
    return db_obj


def update_or_create_setting(session: Session, key, value) -> Setting:
    setting = session.query(Setting).filter(Setting.key == key).first()
    if setting:
        setting.value = value
    else:
        setting = Setting(key=key, value=value)
        session.add(setting)
    session.commit()
    session.refresh(setting)
    return setting



def get_all_settings(session: Session) -> List[Setting]:
    return session.query(Setting).all()


def get_all_jobs(session: Session) -> List[ServerRootJob]:
    return session.query(ServerRootJob).all()


def get_setting(session: Session, key) -> Setting:
    return session.query(Setting).filter(Setting.key == key).first()


def create_server_usage(session: Session, request: ServerUsage) -> ServerUsage:
    db_obj = ServerUsage(
        ram=request.ram,
        cpu=request.cpu,
        disk=request.disk,
        created=tehran_now()
    )
    session.add(db_obj)
    session.commit()
    session.refresh(db_obj)
    return db_obj


def get_full_server_usages(session: Session) -> List[ServerUsage]:
    return session.query(ServerUsage).order_by(ServerUsage.created.desc()).all()


def get_full_services_usages(session: Session) -> List[ServiceUsage]:
    return session.query(ServiceUsage).order_by(ServiceUsage.created.desc()).all()


def get_all_server_usages(session: Session) -> List[ServerUsage]:
    return session.query(ServerUsage).order_by(ServerUsage.created.desc()).limit(
        60).all()


SERVER_USAGE_PERIODS = {
    "1h": (3600, 30), "3h": (10800, 90), "12h": (43200, 360),
    "24h": (86400, 720), "7d": (604800, 5040),
    "14d": (1209600, 10080), "30d": (2592000, 21600),
}


def get_server_usages_for_period(session: Session, period: str) -> List[ServerUsage]:
    duration, bucket_seconds = SERVER_USAGE_PERIODS.get(period, SERVER_USAGE_PERIODS["1h"])
    cutoff = tehran_now() - datetime.timedelta(seconds=duration)
    epoch = cast(func.strftime("%s", ServerUsage.created), Integer)
    latest_ids = (session.query(func.max(ServerUsage.id))
                  .filter(ServerUsage.created >= cutoff)
                  .group_by(epoch - epoch % bucket_seconds))
    return (session.query(ServerUsage)
            .filter(ServerUsage.id.in_(latest_ids))
            .order_by(ServerUsage.created.asc()).all())


def get_server_locked_root_jobs(session: Session) -> List[ServerRootJob]:
    return session.query(ServerRootJob).filter(ServerRootJob.completed_at.is_(None),
                                               ServerRootJob.status == "pending", ServerRootJob.locked.is_(True)).all()


def get_server_not_completed_and_pending_root_jobs(session: Session) -> List[ServerRootJob]:
    return session.query(ServerRootJob).filter(ServerRootJob.completed_at.is_(None),
                                               ServerRootJob.status == "pending", ServerRootJob.locked.is_(False)).all()


def get_server_backup_locked(session: Session) -> List[ServerRootJob]:
    return session.query(ServerRootJob).filter(ServerRootJob.name == "create_backup",
                                               ServerRootJob.completed_at.is_(None),
                                               ServerRootJob.status == "pending", ServerRootJob.locked.is_(True)).all()


def get_server_root_job(session: Session, key: str) -> ServerRootJob:
    return session.query(ServerRootJob).filter(ServerRootJob.key == key).first()


def claim_server_root_job(session: Session, job: ServerRootJob) -> bool:
    result = session.execute(update(ServerRootJob).where(
        ServerRootJob.id == job.id,
        ServerRootJob.status == "pending",
        ServerRootJob.completed_at.is_(None),
        ServerRootJob.locked.is_(False),
    ).values(locked=True, locked_at=tehran_now()))
    session.commit()
    session.refresh(job)
    return result.rowcount == 1


def fail_server_root_job(session: Session, job: ServerRootJob) -> None:
    job.status = "failed"
    job.completed_at = tehran_now()
    job.locked = False
    session.commit()


def create_server_root_job(session: Session, request: ServerRootJob) -> ServerRootJob:
    db_obj = ServerRootJob(
        name=request.name,
        key=request.key,
        data=request.data,
        run_at=request.run_at,
        run_count=0,
        locked=False,
        status="pending",
        created=tehran_now()
    )
    session.add(db_obj)
    session.commit()
    session.refresh(db_obj)
    return db_obj


def set_server_root_job_run(session: Session, id) -> ServerRootJob:
    server_root_job = session.query(ServerRootJob).filter(ServerRootJob.id == id).first()
    server_root_job.completed_at = tehran_now()
    if server_root_job.run_count:
        server_root_job.run_count += 1
    else:
        server_root_job.run_count = 1
    server_root_job.status = "success"
    server_root_job.locked = False
    session.commit()
    session.refresh(server_root_job)
    return server_root_job


def create_bulk_service_usage(session: Session, service_usages: ServiceUsage) -> ServiceUsage:
    session.add_all(service_usages)
    session.commit()


def get_single_service_usages(session: Session, name: str) -> List[ServiceUsage]:
    return session.query(ServiceUsage).filter(ServiceUsage.name == name).order_by(ServiceUsage.created.desc()).limit(
        30).all()


def get_single_service_usages_last(session: Session, name: str) -> ServiceUsage:
    return session.query(ServiceUsage).filter(ServiceUsage.name == name).order_by(ServiceUsage.created.desc()).first()
