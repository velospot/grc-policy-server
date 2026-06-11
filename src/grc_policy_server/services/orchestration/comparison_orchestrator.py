from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

DEFAULT_COMPARISON_TIMEOUT_SEC = 300.0


class ComplianceComparisonOrchestrator:
    """Deterministic coordinator for comparison execution.

    The current comparison engine still owns alignment, diffing, severity, and
    report assembly internally. This wrapper gives callers a non-agent
    orchestration boundary with timeout and progress semantics while preserving
    the existing ComparisonResult schema.
    """

    def __init__(
        self,
        *,
        diff_engine: Any,
        audit_log: Any | None = None,
        batch_size: int = 10,
        timeout_sec: float = DEFAULT_COMPARISON_TIMEOUT_SEC,
    ) -> None:
        self._engine = diff_engine
        self._audit_log = audit_log
        self._batch_size = batch_size
        self._timeout_sec = timeout_sec

    async def run_comparison(
        self,
        doc1: Any,
        doc2: Any,
        *,
        audit_mode: bool = True,
        force_re_extract: bool = False,
        testing_department: str = "",
        progress_cb: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> Any:
        if progress_cb:
            await progress_cb("loading", "Loading document nodes…")

        try:
            return await asyncio.wait_for(
                self._run(
                    doc1=doc1,
                    doc2=doc2,
                    audit_mode=audit_mode,
                    force_re_extract=force_re_extract,
                    testing_department=testing_department,
                    progress_cb=progress_cb,
                ),
                timeout=self._timeout_sec,
            )
        except asyncio.TimeoutError:
            logger.error(
                "comparison orchestrator timed out after %.0fs doc1=%s doc2=%s",
                self._timeout_sec,
                getattr(doc1, "id", "?"),
                getattr(doc2, "id", "?"),
            )
            raise

    async def _run(
        self,
        doc1: Any,
        doc2: Any,
        *,
        audit_mode: bool,
        force_re_extract: bool,
        testing_department: str,
        progress_cb: Callable[[str, str], Awaitable[None]] | None,
    ) -> Any:
        if progress_cb:
            await progress_cb("comparing", "Running comparison pipeline…")

        result = await self._engine.compare(
            doc1,
            doc2,
            force_re_extract=force_re_extract,
            audit_mode=audit_mode,
            save_to_db=False,
            testing_department=testing_department,
        )

        if progress_cb:
            count = len(result.keyDifferences)
            await progress_cb("done", f"Comparison complete — {count} difference(s) found.")

        return result

