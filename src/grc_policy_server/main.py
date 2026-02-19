from fastapi import FastAPI

from grc_policy_server.api.routes import compare, documents, health, with_summary
from grc_policy_server.core.config import settings
from grc_policy_server.core.logging import setup_logging

setup_logging(
    level=settings.log_level,
    service_name=settings.app_name,
)
app = FastAPI(
    title=settings.app_name,
    description="API for GRC policy ingestion and comparison workflows.",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    openapi_tags=[
        {"name": "health", "description": "Service availability checks"},
        {"name": "documents", "description": "Document management endpoints"},
        {"name": "compare", "description": "Document comparison endpoints"},
    ],
)

app.include_router(health.router)
app.include_router(documents.router)
app.include_router(compare.router)
app.include_router(with_summary.router)


def run() -> None:
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
