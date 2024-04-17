import datetime

from sqlalchemy.orm import Session
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


def get_all_settings(session: Session) -> List[Setting]:
    return session.query(Setting).all()


def get_setting(session: Session, key) -> Setting:
    return session.query(Setting).filter(Setting.key == key).first()


def create_server_usage(session: Session, request: ServerUsage) -> ServerUsage:
    db_obj = ServerUsage(
        ram=request.ram,
        cpu=request.cpu,
        disk=request.disk,
        created=datetime.datetime.now()
    )
    session.add(db_obj)
    session.commit()
    session.refresh(db_obj)
    return db_obj


def get_all_server_usages(session: Session) -> List[ServerUsage]:
    return session.query(ServerUsage).all()


def get_server_not_run_root_jobs(session: Session) -> List[ServerRootJob]:
    return session.query(ServerRootJob).filter(ServerRootJob.run_at.is_(None)).all()


def get_server_root_job(session: Session, key: str) -> List[ServerRootJob]:
    return session.query(ServerRootJob).filter(ServerRootJob.key == key).all()


def create_server_root_job(session: Session, request: ServerRootJob) -> ServerRootJob:
    db_obj = ServerRootJob(
        name=request.name,
        key=request.key,
        data=request.data,
        created=datetime.datetime.now()
    )
    session.add(db_obj)
    session.commit()
    session.refresh(db_obj)
    return db_obj


def set_server_root_job_run(session: Session, id) -> ServerRootJob:
    server_root_job = session.query(ServerRootJob).filter(ServerRootJob.id == id).first()
    server_root_job.run_at = datetime.datetime.now()
    session.commit()
    session.refresh(server_root_job)
    return server_root_job


def create_bulk_service_usage(session: Session, service_usages: ServiceUsage) -> ServiceUsage:
    session.add_all(service_usages)
    session.commit()


def get_single_service_usages(session: Session, name: str) -> List[ServiceUsage]:
    return session.query(ServiceUsage).filter(ServiceUsage.name == name).all()
