from sqlalchemy import Column, Integer, String, Float, DateTime

from core.db import Base


class Setting(Base):
    __tablename__ = "settings"
    id = Column(Integer, index=True, primary_key=True)
    key = Column(String, unique=True)
    value = Column(String)


class ServerUsage(Base):
    __tablename__ = "server_usage"
    id = Column(Integer, index=True, primary_key=True)
    ram = Column(Float)
    cpu = Column(Float)
    disk = Column(Float)
    created = Column(DateTime)
