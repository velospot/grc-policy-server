"""
Domain events — lightweight in-process pub/sub for decoupled module communication.
Modules emit events; other modules subscribe without direct imports between services.
Replace EventBus.publish with Redis pub/sub when scaling to multi-process workers.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, ClassVar
from uuid import uuid4

log = logging.getLogger(__name__)


@dataclass
class DomainEvent:
    event_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: datetime = field(default_factory=datetime.utcnow)
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def event_type(self) -> str:
        return self.__class__.__name__


# ---------------------------------------------------------------------------
# Ingestion events
# ---------------------------------------------------------------------------


@dataclass
class IngestionJobCompleted(DomainEvent):
    pass
    # payload: {job_id, version_id, document_id, canonical_node_count}


@dataclass
class IngestionJobFailed(DomainEvent):
    pass
    # payload: {job_id, version_id, document_id, error_message}


# ---------------------------------------------------------------------------
# Comparison events
# ---------------------------------------------------------------------------


@dataclass
class ComparisonJobCreated(DomainEvent):
    pass
    # payload: {job_id, doc1_id, doc2_id, mode}


@dataclass
class ComparisonJobCompleted(DomainEvent):
    pass
    # payload: {job_id, change_record_count}


@dataclass
class ComparisonJobFailed(DomainEvent):
    pass
    # payload: {job_id, error_message}


# ---------------------------------------------------------------------------
# Simple in-process event bus
# ---------------------------------------------------------------------------


class EventBus:
    _handlers: ClassVar[dict[str, list[Callable]]] = {}

    @classmethod
    def subscribe(cls, event_type: type[DomainEvent], handler: Callable) -> None:
        key = event_type.__name__
        cls._handlers.setdefault(key, []).append(handler)

    @classmethod
    def publish(cls, event: DomainEvent) -> None:
        for handler in cls._handlers.get(event.event_type, []):
            try:
                handler(event)
            except Exception:
                log.exception(
                    "event_handler_error event_type=%s event_id=%s",
                    event.event_type,
                    event.event_id,
                )

    @classmethod
    def clear(cls) -> None:
        """Reset all handlers — useful in tests."""
        cls._handlers.clear()
