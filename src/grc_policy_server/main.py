from fastapi import FastAPI

from grc_policy_server.api.routes import compare, health
from grc_policy_server.core.config import settings
from grc_policy_server.core.logging import setup_logging

setup_logging(
    level=settings.log_level,
    service_name=settings.app_name,
)
app = FastAPI(title=settings.app_name)

app.include_router(health.router)
# app.include_router(documents.router)
app.include_router(compare.router)


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
