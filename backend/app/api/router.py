from __future__ import annotations

from fastapi import APIRouter

from app.api import calculations, health, resources

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(calculations.router)
api_router.include_router(resources.router)
