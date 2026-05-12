"""Stable re-export of CIR (Compliance Intermediate Representation) model types.

Import from here to avoid depending on the internal structure of canonical_table_model.
"""

from grc_policy_server.services.documents.canonical_table_model import (
    Citation,
    NormalizedFact,
)

__all__ = ["NormalizedFact", "Citation"]
