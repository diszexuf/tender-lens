from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from app.core.config import get_settings
from app.services.health import HealthReport, check_health

router = APIRouter(tags=["health"])


@router.get("/health/live", summary="Процесс API жив")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/health",
    response_model=HealthReport,
    summary="Готовность всех сервисов",
    responses={503: {"model": HealthReport}},
)
async def health(request: Request, response: Response) -> HealthReport:
    report = await check_health(get_settings(), request.app.state.redis)
    if report.status == "down":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return report
