from sqlalchemy.orm import Session

from models import Setting


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


def get_setting(session: Session, key) -> list[Setting]:
    return session.query(Setting).filter(Setting.key == key).first()
