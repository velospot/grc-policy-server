"""Service health registry with circuit-breaker semantics.

Maintains a cached, per-service health status that is consulted by the
dependency injection layer (`api/deps.py`) to select the appropriate
comparison engine in `auto` mode without blocking request handling.

Design
------
- Module-level singleton (`_REGISTRY`) created on first access.
- Each service status is cached for `cache_ttl_sec` (default 30s).
- After `threshold` consecutive probe failures the circuit opens and the
  service is marked DOWN for `timeout_s` seconds without re-probing.
- Probes use `httpx` with a short connect timeout so a dead service never
  holds a request for more than ~2 seconds.
- Thread-safe: a `threading.Lock` guards the status dict.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Literal

import httpx

from grc_policy_server.core.config import settings

logger = logging.getLogger(__name__)

ServiceStatus = Literal["up", "down", "disabled", "unknown"]

_PROBE_TIMEOUT = 2.0  # seconds — fast fail on dead service


@dataclass
class _ServiceState:
    status: ServiceStatus = "unknown"
    last_checked: float = 0.0  # monotonic seconds
    consecutive_failures: int = 0


class ServiceHealthRegistry:
    """Cached, circuit-breaking service health registry.

    Usage:
        registry = get_service_health_registry()
        if registry.is_healthy("qdrant"):
            ...
        report = registry.status_report()  # dict for /health/services
    """

    def __init__(
        self,
        *,
        cache_ttl_sec: float = 30.0,
        threshold: int = 3,
        timeout_s: float = 60.0,
    ) -> None:
        self._ttl = cache_ttl_sec
        self._threshold = threshold
        self._circuit_timeout = timeout_s
        self._lock = threading.Lock()
        self._states: dict[str, _ServiceState] = {
            "qdrant": _ServiceState(),
            "neo4j": _ServiceState(),
            "celery": _ServiceState(),
            "llm": _ServiceState(),
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_healthy(self, service: str) -> bool:
        """Return True when the service is known-up. Refreshes cache if stale."""
        self._refresh_if_stale(service)
        with self._lock:
            return self._states.get(service, _ServiceState()).status == "up"

    def status_report(self) -> dict[str, dict]:
        """Return a snapshot of all service statuses for the /health/services endpoint."""
        import datetime

        for svc in list(self._states):
            self._refresh_if_stale(svc)

        with self._lock:
            report: dict[str, dict] = {}
            for svc, state in self._states.items():
                entry: dict = {"status": state.status}
                if state.last_checked > 0 and state.status != "disabled":
                    ts = time.time() - (time.monotonic() - state.last_checked)
                    entry["last_checked"] = datetime.datetime.fromtimestamp(
                        ts, tz=datetime.UTC
                    ).isoformat()
                report[svc] = entry
            return report

    # ------------------------------------------------------------------
    # Internal refresh + probes
    # ------------------------------------------------------------------

    def _refresh_if_stale(self, service: str) -> None:
        with self._lock:
            state = self._states.get(service)
            if state is None:
                return
            now = time.monotonic()
            # Disabled services never re-probe.
            if state.status == "disabled":
                return
            # Circuit is open: wait until timeout_s elapses before re-probing.
            if (
                state.status == "down"
                and state.consecutive_failures >= self._threshold
                and (now - state.last_checked) < self._circuit_timeout
            ):
                return
            # Cache is fresh.
            if state.status != "unknown" and (now - state.last_checked) < self._ttl:
                return

        # Release lock before doing network I/O.
        self._probe(service)

    def _probe(self, service: str) -> None:
        try:
            healthy = {
                "qdrant": self._probe_qdrant,
                "neo4j": self._probe_neo4j,
                "celery": self._probe_celery,
                "llm": self._probe_llm,
            }[service]()
        except Exception:
            logger.debug(
                "health probe for %s raised unexpectedly", service, exc_info=True
            )
            healthy = False

        with self._lock:
            state = self._states.get(service)
            if state is None:
                return
            state.last_checked = time.monotonic()
            if healthy is None:  # disabled
                state.status = "disabled"
                state.consecutive_failures = 0
            elif healthy:
                state.status = "up"
                state.consecutive_failures = 0
            else:
                state.consecutive_failures += 1
                state.status = "down"

    def _probe_qdrant(self) -> bool:
        url = settings.qdrant_url.rstrip("/") + "/healthz"
        try:
            r = httpx.get(url, timeout=_PROBE_TIMEOUT)
            return r.status_code < 400
        except Exception:
            return False

    def _probe_neo4j(self) -> bool | None:
        if not settings.neo4j_enabled:
            return None  # disabled
        uri = settings.neo4j_uri
        # Bolt doesn't speak HTTP — check TCP reachability via httpx HTTP probe.
        host_port = uri.replace("bolt://", "").replace("neo4j://", "").split("/")[0]
        host, _, port = host_port.partition(":")
        http_url = f"http://{host}:{port or 7474}/"
        try:
            r = httpx.get(http_url, timeout=_PROBE_TIMEOUT)
            return r.status_code < 500
        except Exception:
            return False

    def _probe_celery(self) -> bool:
        if settings.comparison_backend == "offline":
            return False  # disabled in offline mode
        broker_url = settings.celery_broker_url
        if not broker_url:
            return False
        # For Redis: try a simple HTTP probe against the broker host.
        # Redis speaks its own protocol, so we just check TCP connect by
        # hitting the Redis URL host:port via a raw socket rather than httpx.
        import socket

        try:
            url = broker_url.replace("redis://", "").replace("rediss://", "")
            url = url.split("/")[0]
            host, _, port = url.partition(":")
            with socket.create_connection(
                (host, int(port or 6379)), timeout=_PROBE_TIMEOUT
            ):
                return True
        except Exception:
            return False

    def _probe_llm(self) -> bool:
        """Probe the local Ollama instance via its version endpoint."""
        base_url = settings.ollama_url
        if not base_url:
            return False
        for path in ("/api/version", "/api/tags"):
            try:
                r = httpx.get(base_url.rstrip("/") + path, timeout=_PROBE_TIMEOUT)
                if r.status_code < 400:
                    return True
            except Exception:
                pass
        return False


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_REGISTRY: ServiceHealthRegistry | None = None
_REGISTRY_LOCK = threading.Lock()


def get_service_health_registry() -> ServiceHealthRegistry:
    """Return the process-level singleton, creating it on first call."""
    global _REGISTRY  # noqa: PLW0603
    if _REGISTRY is not None:
        return _REGISTRY
    with _REGISTRY_LOCK:
        if _REGISTRY is None:
            _REGISTRY = ServiceHealthRegistry(
                cache_ttl_sec=30.0,
                threshold=settings.circuit_breaker_threshold,
                timeout_s=settings.circuit_breaker_timeout_s,
            )
    return _REGISTRY
