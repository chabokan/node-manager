import requests
from fastapi import FastAPI
from fastapi_restful.tasks import repeat_every

from api.main import api_router
from core.config import settings
import models
from core.db import engine
import sentry_sdk

app = FastAPI(docs_url=None, redoc_url=None)
app.include_router(api_router, prefix=settings.API_V1_STR)
models.Base.metadata.create_all(engine)

# it should be here for running cron jobs
import core.cron
# Don't Remove this
