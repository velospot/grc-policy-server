"""
Observability / tracing — Opik integration gated entirely on settings.opik_enabled.

Usage:
    from grc_policy_server.services.observability import tracing

    # Call once at startup (main.py lifespan or worker startup):
    tracing.configure(
        enabled=settings.opik_enabled,
        url=settings.opik_url_override,
        project_name=settings.opik_project_name,
        workspace=settings.opik_workspace,
    )

    # Wrap any function transparently — no-op when disabled:
    @tracing.track(name="my_fn", type="llm", tags=["llm"], metadata={"model": "x"})
    def my_fn(text: str) -> str: ...

    # Or as a factory (same as the decorator but applied at call time):
    tracked_fn = tracing.track(name="embed", ...)(raw_fn)
    result = tracked_fn(input)
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable

log = logging.getLogger(__name__)

# Module-level singleton — None until configure() is called with enabled=True.
_opik: Any = None


def configure(
    *,
    enabled: bool,
    url: str,
    project_name: str,
    workspace: str,
) -> None:
    """
    Initialise the Opik tracing backend.
    Must be called once before using track(). Safe to call multiple times.
    When enabled=False this is always a no-op.
    """
    global _opik
    if not enabled:
        return
    try:
        import opik  # lazy — not imported unless tracing is enabled

        os.environ.setdefault("OPIK_URL_OVERRIDE", url)
        os.environ.setdefault("OPIK_PROJECT_NAME", project_name)
        os.environ.setdefault("OPIK_WORKSPACE", workspace)
        _opik = opik
        log.info(
            "opik_tracing_enabled url=%s project=%s workspace=%s",
            url,
            project_name,
            workspace,
        )
    except Exception:
        log.exception("opik_init_failed — tracing will be disabled")
        _opik = None


def track(
    *,
    name: str,
    type: str = "llm",  # noqa: A002  (shadows builtin — matches opik API)
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    project_name: str | None = None,
) -> Callable[[Callable], Callable]:
    """
    Return a decorator that wraps a function with Opik tracing.
    When tracing is disabled (either by config or init failure), returns
    the original function unchanged — zero overhead.

    Compatible with both sync and async callables; opik.track preserves
    the coroutine nature of wrapped async functions.
    """
    # Lazy settings import avoids circular import at module load time.
    from grc_policy_server.core.config import settings

    if not settings.opik_enabled or _opik is None:
        def _passthrough(fn: Callable) -> Callable:
            return fn
        return _passthrough

    return _opik.track(
        name=name,
        type=type,
        tags=tags or [],
        metadata=metadata or {},
        project_name=project_name,
    )


def is_enabled() -> bool:
    """True only when tracing has been successfully initialised."""
    return _opik is not None
