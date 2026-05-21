import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from grc_policy_server.api.routes import (
    compare,
    compare_v2,
    compare_v3_stream,
    compare_v4_stream,
    documents,
    health,
    storage_providers,
    with_summary,
)
from grc_policy_server.core.config import settings
from grc_policy_server.core.logging import log_runtime_environment, setup_logging
from grc_policy_server.services.observability import tracing

setup_logging(
    level=settings.log_level,
    service_name=settings.app_name,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    log_runtime_environment(settings.as_env_items(), logger_name=logger.name)
    tracing.configure(
        enabled=settings.opik_enabled,
        url=settings.opik_url_override,
        project_name=settings.opik_project_name,
        workspace=settings.opik_workspace,
    )
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
        {"name": "storage", "description": "Storage provider configuration endpoints"},
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


app.include_router(health.router)
app.include_router(documents.router)
app.include_router(compare.router)
app.include_router(with_summary.router)
app.include_router(compare_v2.router)
app.include_router(compare_v3_stream.router)
app.include_router(compare_v4_stream.router)
app.include_router(storage_providers.router)


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
