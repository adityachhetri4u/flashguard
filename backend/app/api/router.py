from fastapi import APIRouter
from app.api.endpoints import api

api_router = APIRouter()
api_router.include_router(api.router, tags=["api"])
