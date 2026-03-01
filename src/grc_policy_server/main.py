import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from grc_policy_server.api.routes import compare, documents, health, with_summary
from grc_policy_server.core.config import settings
from grc_policy_server.core.logging import log_runtime_environment, setup_logging

setup_logging(
    level=settings.log_level,
    service_name=settings.app_name,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    log_runtime_environment(settings.as_env_items(), logger_name=logger.name)
    yield


# Main FastAPI application for upload, listing, and comparison workflows.
app = FastAPI(
    title=settings.app_name,
    description="API for GRC policy ingestion and comparison workflows.",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    openapi_tags=[
        {"name": "health", "description": "Service availability checks"},
        {"name": "documents", "description": "Document management endpoints"},
        {"name": "compare", "description": "Document comparison endpoints"},
    ],
)

def _parse_cors_items(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_cors_items(settings.cors_allow_origins),
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=_parse_cors_items(settings.cors_allow_methods),
    allow_headers=_parse_cors_items(settings.cors_allow_headers),
)

request_lock = asyncio.Lock()


@app.middleware("http")
async def with_request_lock(request: Request, call_next):
    await request_lock.acquire()
    try:
        response = await call_next(request)
        return response
    finally:
        request_lock.release()


app.include_router(health.router)
app.include_router(documents.router)
app.include_router(compare.router)
app.include_router(with_summary.router)


def run() -> None:
    """Run the API using runtime settings from environment configuration."""
    import uvicorn

    uvicorn.run(
        "grc_policy_server.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=settings.debug,
    )


if __name__ == "__main__":
    run()
