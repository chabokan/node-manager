import datetime

from sqlalchemy.orm import Session

from models import Setting, ServerUsage


def create_setting(session: Session, request: Setting) -> Setting:
    db_obj = Setting(
        key=request.key,
        value=request.value,
    )
    session.add(db_obj)
    session.commit()
    session.refresh(db_obj)
    return db_obj


def get_all_settings(session: Session) -> list[Setting]:
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


def get_all_server_usages(session: Session) -> list[ServerUsage]:
    return session.query(ServerUsage).all()
