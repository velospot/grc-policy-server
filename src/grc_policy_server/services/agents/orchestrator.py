"""Backward-compatible import path for the deterministic comparison orchestrator."""

from grc_policy_server.services.orchestration.comparison_orchestrator import (
    ComplianceComparisonOrchestrator,
)


class OrchestratorAgent(ComplianceComparisonOrchestrator):
    """Compatibility alias.

    Kept so existing imports do not break. New code should import
    ComplianceComparisonOrchestrator from services.orchestration.
    """
