from fastapi import APIRouter
from api.routes import core

api_router = APIRouter()
api_router.include_router(core.router, tags=["core"])
