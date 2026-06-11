from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from grc_policy_server.services.orchestration.job_state import IngestionState, JobState


class DeterministicFailure(RuntimeError):
    """A deterministic stage failed and the pipeline must stop."""


class AgentFailure(RuntimeError):
    """An optional agent stage failed and must route to review, not stop."""


class AuditSink(Protocol):
    def log_event(self, **kwargs: Any) -> None: ...


StageCallable = Callable[[Any], Any | Awaitable[Any]]


@dataclass(frozen=True)
class IngestionStage:
    name: str
    next_state: IngestionState
    is_agent: bool = False
    progress: int = 0


INGESTION_STAGES: tuple[IngestionStage, ...] = (
    IngestionStage("quality_gate", IngestionState.QUALITY_CHECKED, False, 7),
    IngestionStage("parse", IngestionState.PARSED, False, 14),
    IngestionStage("extract_docling_graph", IngestionState.EXTRACTED, False, 21),
    IngestionStage("canonicalize", IngestionState.CANONICALIZED, False, 29),
    IngestionStage("normalize_tables", IngestionState.TABLES_NORMALIZED, False, 36),
    IngestionStage("resolve_standards", IngestionState.STANDARDS_RESOLVED, True, 43),
    IngestionStage("map_ontology", IngestionState.ONTOLOGY_MAPPED, True, 50),
    IngestionStage("resolve_stable_identities", IngestionState.IDENTITIES_RESOLVED, True, 57),
    IngestionStage("build_graph", IngestionState.GRAPH_BUILT, False, 64),
    IngestionStage("build_relationships", IngestionState.RELATIONSHIPS_BUILT, True, 71),
    IngestionStage("validate_evidence_chains", IngestionState.EVIDENCE_VALIDATED, False, 79),
    IngestionStage("embed_nodes", IngestionState.EMBEDDED, False, 86),
    IngestionStage("mark_ready", IngestionState.READY_FOR_COMPARISON, False, 100),
)


class IngestionToolRegistry:
    """Small async-aware tool registry used by the deterministic orchestrator."""

    def __init__(self) -> None:
        self._tools: dict[str, StageCallable] = {}

    def register(self, name: str, func: StageCallable) -> None:
        self._tools[name] = func

    async def run(self, name: str, context: Any) -> Any:
        try:
            tool = self._tools[name]
        except KeyError as exc:
            raise DeterministicFailure(f"No ingestion tool registered for stage '{name}'") from exc
        result = tool(context)
        if inspect.isawaitable(result):
            return await result
        return result


class ComplianceIngestionOrchestrator:
    """Python state machine for ingestion.

    This class is intentionally not an LLM wrapper. Deterministic stages halt on
    failure. Agent-marked stages are allowed to route to review and advance only
    when they raise AgentFailure.
    """

    def __init__(
        self,
        *,
        tool_registry: IngestionToolRegistry,
        audit_log: AuditSink | None = None,
    ) -> None:
        self.tools = tool_registry
        self.audit_log = audit_log

    async def run(self, *, job: JobState, context: Any) -> tuple[JobState, Any]:
        start_idx = self._resume_index(job)
        for stage in INGESTION_STAGES[start_idx:]:
            try:
                context = await self.tools.run(stage.name, context)
            except AgentFailure as exc:
                job.risk.review_required = True
                job.risk.review_reason = str(exc)
                self._transition(job, stage)
                self._audit(job, stage.name, "AGENT_FAILURE_REVIEW_ROUTED", {"error": str(exc)})
                continue
            except Exception as exc:
                job.state = IngestionState.FAILED
                job.resume_from = stage.name
                job.failure_artifact_uri = f"memory://{job.job_id}/{stage.name}/failure"
                self._audit(job, stage.name, "DETERMINISTIC_FAILURE", {"error": str(exc)})
                raise DeterministicFailure(str(exc)) from exc

            self._transition(job, stage)
            self._audit(job, stage.name, "SUCCESS", {"state": job.state.value})

        return job, context

    @staticmethod
    def _resume_index(job: JobState) -> int:
        if not job.can_resume():
            return 0
        for idx, stage in enumerate(INGESTION_STAGES):
            if stage.name == job.resume_from:
                return idx
        return 0

    @staticmethod
    def _transition(job: JobState, stage: IngestionStage) -> None:
        job.state = stage.next_state
        job.progress = max(job.progress, stage.progress)

    def _audit(
        self,
        job: JobState,
        stage_name: str,
        status: str,
        payload: dict[str, Any],
    ) -> None:
        if self.audit_log is None:
            return
        self.audit_log.log_event(
            event_type=f"ingestion_{stage_name}",
            entity_id=job.job_id,
            entity_type="ingestion_job",
            doc_id=job.doc_id,
            actor="system",
            payload={"status": status, **payload},
        )

