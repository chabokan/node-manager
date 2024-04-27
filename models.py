from sqlalchemy import Column, Integer, String, Float, DateTime, Text

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


class ServerRootJob(Base):
    __tablename__ = "server_root_jobs"
    id = Column(Integer, index=True, primary_key=True)
    name = Column(String)
    key = Column(String, unique=True)
    run_count = Column(Integer, nullable=True)
    data = Column(Text)
    status = Column(String, default="pending")
    run_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created = Column(DateTime)


class ServiceUsage(Base):
    __tablename__ = "service_usage"
    id = Column(Integer, index=True, primary_key=True)
    name = Column(String)
    ram = Column(Float)
    cpu = Column(Float)
    disk = Column(Float)
    read = Column(Float)
    write = Column(Float)
    network_rx = Column(Float)
    network_tx = Column(Float)
    created = Column(DateTime)
