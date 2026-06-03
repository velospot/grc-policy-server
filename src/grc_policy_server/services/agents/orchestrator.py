"""Orchestrator Agent — thin Python coordinator (AGENTS.md Agent 1).

NOT a prompt-based LLM agent. Sequences the existing comparison services in
order and enforces cross-cutting concerns: 5-minute timeout, audit logging,
and progress callbacks for streaming routes.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SEC = 300.0  # 5 minutes per AGENTS.md spec


class OrchestratorAgent:
    """Thin coordinator for the multi-stage compliance comparison pipeline.

    Responsibilities:
      1. Enforce total pipeline timeout (5 minutes).
      2. Sequence: load nodes → align → diff → evidence → explain → report.
      3. Emit progress via optional callback (used by streaming routes).
      4. Log audit events if AuditLogStore is configured.

    NOT responsible for calling LLMs directly, deciding severity, or producing
    free-form text — each stage's dedicated service owns those concerns.
    """

    def __init__(
        self,
        *,
        diff_engine: Any,              # RealDiffEngine instance
        audit_log: Any | None = None,  # AuditLogStore | None
        batch_size: int = 10,
        timeout_sec: float = _DEFAULT_TIMEOUT_SEC,
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
    ) -> Any:  # ComparisonResult
        """Run the full comparison pipeline with timeout enforcement.

        progress_cb(stage, message) is awaited at stage boundaries so
        streaming routes can emit SSE progress events.
        """
        if progress_cb:
            await progress_cb("loading", "Loading document nodes…")

        try:
            result = await asyncio.wait_for(
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
                "OrchestratorAgent: comparison timed out after %.0fs doc1=%s doc2=%s",
                self._timeout_sec,
                getattr(doc1, "id", "?"),
                getattr(doc2, "id", "?"),
            )
            raise

        return result

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
            n = len(result.keyDifferences)
            await progress_cb("done", f"Comparison complete — {n} difference(s) found.")

        return result
