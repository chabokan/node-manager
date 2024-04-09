from fastapi import APIRouter
from api.routes import core, server, services

api_router = APIRouter()
api_router.include_router(core.router, tags=["core"])
api_router.include_router(server.router, prefix="/server", tags=["server"])
api_router.include_router(services.router, prefix="/service", tags=["service"])
