"""Shared compiled regex patterns for change detection across the comparison pipeline.

All patterns here are *detection-mode* (return a match / no-match boolean).
Domain fact *extraction* patterns (with named capture groups) live in the ontology
modules (emc_ontology, safety_ontology, environment_ontology) because they need to
parse values, not just confirm their presence.

Import from here in any module that needs to ask "does this text contain a
field-strength value?" — never re-compile the same pattern in multiple files.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Generic numeric / structural patterns
# ---------------------------------------------------------------------------

NUMBER_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:%|percent|days?|weeks?|months?|years?|"
    r"hours?|minutes?|seconds?|kg|g|mg|ms|s|m|cm|mm|w|kw|v|a)?\b",
    re.IGNORECASE,
)

# Cross-reference labels — "Figure 3", "Table 5.2", "Section 3.1.2", "Annex A.1"
# Changes to these are structural reordering artefacts, not semantic content changes.
REF_NUM_RE = re.compile(
    r"\b(?:figure|fig\.?|table|tbl\.?|section|sec\.?|clause|annex|"
    r"appendix|chapter|part|article)\s*(?:[A-Z]\.)?[\d]+(?:[.\-][\d]+)*",
    re.IGNORECASE,
)

# Characters that are purely formatting — changes to these alone carry no semantic weight.
FORMATTING_STRIP_RE = re.compile(r"[-–—\n\r;]")

# ---------------------------------------------------------------------------
# EMC / domain detection patterns
# ---------------------------------------------------------------------------

FIELD_STRENGTH_RE = re.compile(
    r'\b\d+(?:[.,]\d+)?\s*(?:v/m|mv/m|kv/m|db[µu]v/m)\b',
    re.IGNORECASE,
)

FREQ_RANGE_RE = re.compile(
    r'\b\d+(?:[.,]\d+)?\s*(?:hz|khz|mhz|ghz)\b.*?\b\d+(?:[.,]\d+)?\s*(?:hz|khz|mhz|ghz)\b',
    re.IGNORECASE | re.DOTALL,
)

EMISSION_LIMIT_RE = re.compile(
    r'\b\d+(?:[.,]\d+)?\s*(?:db[µμu]v(?:/m)?|db[µμu]a)\b',
    re.IGNORECASE,
)

ACCEPTANCE_CLASS_RE = re.compile(
    r'\b(?:class|performance\s+criterion|performance\s+level)\s*[a-e]\b',
    re.IGNORECASE,
)

TEST_METHOD_RE = re.compile(
    r'\b(?:iec|cispr|iso|din\s+en|vde|sae)\s*\d+[-\s.]\d+(?:[-\s.]\d+)?(?:\s+ed\.?\s*\d+)?',
    re.IGNORECASE,
)

DWELL_TIME_RE = re.compile(
    r'\b(?:dwell|exposure|soak|dwell\s+time|exposure\s+time)\b.*?\b\d+\s*(?:s|ms|min|h|second|minute|hour)',
    re.IGNORECASE,
)

TEST_SETUP_RE = re.compile(
    r'\b(?:temperature|humidity|eut\s+orientation|antenna\s+distance|ground\s+plane|test\s+distance'
    r'|pre-conditioning|precondition|polarisation|polarization)\b',
    re.IGNORECASE,
)
