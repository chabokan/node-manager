import logging

import requests
from fastapi import FastAPI
from fastapi_restful.tasks import repeat_every

from api.main import api_router
from core.config import settings
from core.logging_setup import configure_logging, log_event
import models
from core.db import engine
import sentry_sdk

configure_logging(service="web")

app = FastAPI(docs_url=None, redoc_url=None)
app.include_router(api_router, prefix=settings.API_V1_STR)
models.Base.metadata.create_all(engine)

log_event(logging.getLogger(__name__), "app_started", routes=len(app.routes))

# it should be here for running cron jobs
import core.cron
# Don't Remove this
