from sqlalchemy import Column, Integer, String

from core.db import Base


class Setting(Base):
    __tablename__ = "settings"
    id = Column(Integer, index=True, primary_key=True)
    key = Column(String)
    value = Column(String)
