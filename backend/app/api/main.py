"""FastAPI entrypoint."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.conversations import router as conversations_router
from app.api.routes.v1 import router as v1_router
from app.config import get_settings
from app.models.schemas import HealthResponse


settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Multi-agent intelligent travel planning backend.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=settings.app_version,
        agents=[
            "TotalAgent",
            "StrategyAgent",
            "QueryAgent",
            "AnalysisAgent",
            "ReportAgent",
            "Memory",
        ],
    )


app.include_router(v1_router, prefix="/api/v1")
app.include_router(conversations_router, prefix="/api/v1")
