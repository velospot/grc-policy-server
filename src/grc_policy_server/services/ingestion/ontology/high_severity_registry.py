"""Unified registry of high-severity ontology entity types across all GRC domains.

Each domain ontology (EMC, Safety, Environment) defines its own frozenset of
entity type names whose values are compliance-critical — a change to them always
warrants HIGH severity in the diff engine.

This module merges those per-domain sets into a single authoritative collection
so that table_diff_engine and severity_classifier import from one place instead
of duplicating the union logic.

To register a new high-severity entity type:
  1. Add its fact_type string to the relevant domain frozenset in the owning
     ontology module (emc_ontology, safety_ontology, or environment_ontology).
  2. No other file needs updating — this registry re-exports the merged set.
"""

from __future__ import annotations

from grc_policy_server.services.ingestion.ontology.emc_ontology import (
    EMC_HIGH_SEVERITY_ENTITIES,
)
from grc_policy_server.services.ingestion.ontology.safety_ontology import (
    SAFETY_HIGH_SEVERITY_ENTITIES,
)
from grc_policy_server.services.ingestion.ontology.environment_ontology import (
    ENV_HIGH_SEVERITY_ENTITIES,
)

ALL_HIGH_SEVERITY_ENTITIES: frozenset[str] = (
    EMC_HIGH_SEVERITY_ENTITIES
    | SAFETY_HIGH_SEVERITY_ENTITIES
    | ENV_HIGH_SEVERITY_ENTITIES
)

__all__ = [
    "ALL_HIGH_SEVERITY_ENTITIES",
    "EMC_HIGH_SEVERITY_ENTITIES",
    "SAFETY_HIGH_SEVERITY_ENTITIES",
    "ENV_HIGH_SEVERITY_ENTITIES",
]
