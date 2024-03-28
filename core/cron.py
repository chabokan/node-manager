import requests
from fastapi_restful.tasks import repeat_every

from main import app


@app.on_event("startup")
@repeat_every(seconds=5)
def server_sync() -> None:
    requests.get("https://php-ee6q4l.chbk.run/")