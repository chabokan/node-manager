import requests
from fastapi import FastAPI
from fastapi_utils.tasks import repeat_every

from router import core

app = FastAPI()
app.include_router(core.router)


@app.on_event("startup")
@repeat_every(seconds=5)
def test_req() -> None:
    requests.get("https://php-ee6q4l.chbk.run/")
