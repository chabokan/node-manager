from fastapi import APIRouter, status, Depends, HTTPException
# from typing import List

router = APIRouter(
    tags=['handling'],
    prefix="/handling"
)


@router.get("/")
async def root():
    return {"message": "Hello World"}


@router.get("/hello/{name}")
async def say_hello(name: str):
    return {"message": f"Hello {name}"}


