import logging
import sys
from typing import Optional


def setup_logging(
    *,
    level: str = "INFO",
    service_name: Optional[str] = None,
) -> None:
    """
    Configure application-wide logging.

    - Logs to stdout (container-friendly)
    - Single formatter
    - Idempotent (safe to call once at startup)
    """

    log_level = getattr(logging, level.upper(), logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Avoid duplicate handlers if called multiple times
    if root_logger.handlers:
        return

    handler = logging.StreamHandler(sys.stdout)

    formatter = logging.Formatter(
        fmt=("%(asctime)s %(levelname)s %(name)s %(message)s")
    )

    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    # Optional: label the service at startup
    if service_name:
        logging.getLogger(__name__).info(
            "logging initialized",
            extra={"service": service_name},
        )
