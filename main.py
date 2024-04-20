import requests
from fastapi import FastAPI
from fastapi_restful.tasks import repeat_every

from api.main import api_router
from core.config import settings
import models
from core.db import engine
import sentry_sdk

sentry_sdk.init(
    dsn="https://e0d86ba4f740f7b3bd16d5aa226f2545@sentry.chabokan.net/15",
    traces_sample_rate=1.0,
)

app = FastAPI()
app.include_router(api_router, prefix=settings.API_V1_STR)
# models.Base.metadata.create_all(engine)

# it should be here for running cron jobs
import core.cron
# Don't Remove this
