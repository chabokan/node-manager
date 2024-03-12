from fastapi import FastAPI
from router import handling

app = FastAPI()

app.include_router(handling.router)
