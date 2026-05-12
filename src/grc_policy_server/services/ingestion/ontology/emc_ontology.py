"""Automotive EMC domain ontology — entity types, unit normalization, test classification,
and NormalizedFact extraction from table cells.

All processing is offline (regex + dict lookups, no network calls, no LLM).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any


class EMCTestType(str, Enum):
    """Detected EMC test type — determines domain-specific row key structure."""

    RADIATED_IMMUNITY = "radiated_immunity"
    CONDUCTED_EMISSIONS = "conducted_emissions"
    ESD = "esd"
    TRANSIENT_IMMUNITY = "transient_immunity"
    UNKNOWN = "unknown"


class OntologyEntityType(str, Enum):
    """Entity types from the automotive EMC ontology."""

    FIELD_STRENGTH = "FieldStrength"
    FREQUENCY_RANGE = "FrequencyRange"
    EMISSION_LIMIT = "EmissionLimit"
    NORMATIVE_TERM = "NormativeTerm"
    ACCEPTANCE_CRITERION = "AcceptanceCriterion"
    PHENOMENON = "Phenomenon"
    NUMERIC_LIMIT = "NumericLimit"
    IMMUNITY_LEVEL = "ImmunityLevel"
    TEST_METHOD = "TestMethod"


# KB spec: domain-specific row key column patterns per test type
EMC_DOMAIN_ROW_KEYS: dict[EMCTestType, list[str]] = {
    EMCTestType.RADIATED_IMMUNITY: [
        "phenomenon", "frequency_range", "modulation", "component_or_port", "acceptance_criterion"
    ],
    EMCTestType.CONDUCTED_EMISSIONS: [
        "phenomenon", "port", "frequency_range", "detector", "limit_class"
    ],
    EMCTestType.ESD: [
        "phenomenon", "discharge_type", "polarity", "voltage_level", "location", "acceptance_criterion"
    ],
    EMCTestType.TRANSIENT_IMMUNITY: [
        "phenomenon", "pulse_type", "supply_voltage", "coupling_path", "severity_level"
    ],
}

# Phenomenon taxonomy: alias → canonical name
PHENOMENON_ALIASES: dict[str, str] = {
    "rs": "radiated_susceptibility",
    "ri": "radiated_immunity",
    "re": "radiated_emissions",
    "cs": "conducted_susceptibility",
    "ci": "conducted_immunity",
    "ce": "conducted_emissions",
    "bci": "bulk_current_injection",
    "esd": "electrostatic_discharge",
    "eft": "electrical_fast_transient",
    "surge": "surge",
    "dip": "voltage_dip",
    "pfmf": "power_frequency_magnetic_field",
    "radiated susceptibility": "radiated_susceptibility",
    "radiated immunity": "radiated_immunity",
    "radiated emissions": "radiated_emissions",
    "conducted emissions": "conducted_emissions",
    "bulk current injection": "bulk_current_injection",
    "electrostatic discharge": "electrostatic_discharge",
    "electrical fast transient": "electrical_fast_transient",
    "leitungsgebundene stoerung": "conducted_emissions",
    "leitungsgeführte störung": "conducted_emissions",
    "strahlungsimmunität": "radiated_immunity",
    "strahlungsaussendung": "radiated_emissions",
    "magnetisches feld": "power_frequency_magnetic_field",
}

# Normative terms per language: text → strength level
NORMATIVE_TERM_STRENGTH: dict[str, str] = {
    # English
    "shall": "mandatory",
    "must": "mandatory",
    "is required to": "mandatory",
    "are required to": "mandatory",
    "shall not": "prohibited",
    "must not": "prohibited",
    "should": "recommended",
    "is recommended": "recommended",
    "should not": "not_recommended",
    "may": "permitted",
    "is permitted": "permitted",
    "is allowed": "permitted",
    # German
    "muss": "mandatory",
    "müssen": "mandatory",
    "ist erforderlich": "mandatory",
    "sind erforderlich": "mandatory",
    "darf nicht": "prohibited",
    "dürfen nicht": "prohibited",
    "soll": "recommended",
    "sollte": "recommended",
    "empfohlen": "recommended",
    "darf": "permitted",
    "dürfen": "permitted",
    "kann": "permitted",
    # French
    "doit": "mandatory",
    "doivent": "mandatory",
    "est obligatoire": "mandatory",
    "sont obligatoires": "mandatory",
    "ne doit pas": "prohibited",
    "ne doivent pas": "prohibited",
    "devrait": "recommended",
    "il est recommandé": "recommended",
    "peut": "permitted",
    "peuvent": "permitted",
    "est autorisé": "permitted",
}

# ──────────────────────────────────────────────────────────────────────────────
# Compiled regex patterns for fact extraction
# ──────────────────────────────────────────────────────────────────────────────

_FREQ_RANGE_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(Hz|kHz|MHz|GHz)\s*[-–—to bis]\s*(\d+(?:[.,]\d+)?)\s*(Hz|kHz|MHz|GHz)",
    re.IGNORECASE,
)
_FREQ_SINGLE_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(Hz|kHz|MHz|GHz)",
    re.IGNORECASE,
)
_FIELD_STRENGTH_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(V/m|mV/m|dBV/m|dBuV/m)",
    re.IGNORECASE,
)
_EMISSION_LIMIT_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(?:"
    r"(dBuV|dBµV|dBuA|dBµA|dBuV/m|dBµV/m)"           # compact form
    r"|db\s*[\(]?\s*[μu]v\s*[\)]?"                      # spaced: db (μv)
    r"|db\s*[\(]?\s*[μu]a\s*[\)]?"                      # spaced: db (μa)
    r")",
    re.IGNORECASE,
)
_CURRENT_LEVEL_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(mA|A)\b",
    re.IGNORECASE,
)
# kV levels: always extract (± prefix allowed, e.g. ±15kV in ESD tables)
_KV_STRONG_RE = re.compile(
    r"[±+\-]?\s*(\d+(?:[.,]\d+)?)\s*(kV)\b(?!\s*/\s*m)",
    re.IGNORECASE,
)
_VOLTAGE_LEVEL_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(V)\b(?!\s*/\s*m)",
    re.IGNORECASE,
)
# Tolerance-format voltage: "13, 5 ± 0, 5" or "13,5±0,5" (comma as decimal separator)
_TOLERANCE_VOLTAGE_RE = re.compile(
    r"(\d+[,\s]\s*\d+)\s*[±+\-]\s*[\d,\s]+\s*(kV|V)\b(?!\s*/\s*m)",
    re.IGNORECASE,
)
_NORMATIVE_TERM_RE = re.compile(
    r"\b(shall\s+not|must\s+not|darf\s+nicht|dürfen\s+nicht|ne\s+doit\s+pas|ne\s+doivent\s+pas"
    r"|shall|must|is\s+required\s+to|are\s+required\s+to"
    r"|should\s+not|should|is\s+recommended|empfohlen|soll(?:te)?"
    r"|müssen|muss|may\s+not|may|darf|dürfen|kann|peut|peuvent|doit|doivent)\b",
    re.IGNORECASE,
)
_CLASS_RE = re.compile(r"\b[Cc]lass\s+([A-E])\b|\b[Kk]lasse\s+([A-E])\b")

_FREQ_MULTIPLIERS: dict[str, float] = {
    "hz": 1.0,
    "khz": 1e3,
    "mhz": 1e6,
    "ghz": 1e9,
}


# ──────────────────────────────────────────────────────────────────────────────
# Unit normalizer
# ──────────────────────────────────────────────────────────────────────────────

class UnitNormalizer:
    """Normalizes physical units to canonical base units for deterministic comparison."""

    def normalize_frequency(self, value_str: str, unit: str) -> tuple[float, str]:
        """Convert a frequency value+unit to Hz. Returns (value_in_hz, 'Hz')."""
        try:
            v = float(value_str.replace(",", "."))
        except ValueError:
            return (0.0, "Hz")
        multiplier = _FREQ_MULTIPLIERS.get(unit.lower(), 1.0)
        return (v * multiplier, "Hz")

    def normalize_unit(self, raw: str) -> tuple[str, str]:
        """Return (normalized_unit_string, canonical_unit_family).

        E.g. 'dBµV' → ('dBuV', 'emission_limit')
        """
        raw = raw.strip()
        mapping: dict[str, tuple[str, str]] = {
            "dBµV": ("dBuV", "emission_limit"),
            "dBuV": ("dBuV", "emission_limit"),
            "dBµA": ("dBuA", "emission_limit"),
            "dBuA": ("dBuA", "emission_limit"),
            "dBµV/m": ("dBuV/m", "emission_limit"),
            "dBuV/m": ("dBuV/m", "emission_limit"),
            "V/m": ("V/m", "field_strength"),
            "mV/m": ("mV/m", "field_strength"),
            "dBV/m": ("dBV/m", "field_strength"),
            "kHz": ("kHz", "frequency"),
            "MHz": ("MHz", "frequency"),
            "GHz": ("GHz", "frequency"),
            "Hz": ("Hz", "frequency"),
            "kV": ("kV", "voltage"),
            "V": ("V", "voltage"),
            "mA": ("mA", "current"),
            "A": ("A", "current"),
        }
        for key, val in mapping.items():
            if raw.lower() == key.lower():
                return val
        return (raw, "unknown")


# ──────────────────────────────────────────────────────────────────────────────
# Test type classifier
# ──────────────────────────────────────────────────────────────────────────────

_RADIATED_IMMUNITY_SIGNALS = {
    "radiated immunity", "ri", "strahlungsimmunität", "rs",
    "radiated susceptibility", "efield", "e-field", "electromagnetic immunity",
}
_CONDUCTED_EMISSIONS_SIGNALS = {
    "conducted emissions", "ce", "conducted disturbance", "leitungsgeführte",
    "leitungsgebundene", "conducted emission",
    "störaussendung", "stoeraussendung", "leitungsgebundene emissionen",
    "leitungsgebundene störung", "emission limit", "grenzwert",
}
_ESD_SIGNALS = {
    "esd", "electrostatic discharge", "elektrostatische entladung",
    "discharge", "contact discharge", "air discharge",
}
_TRANSIENT_SIGNALS = {
    "transient", "eft", "electrical fast transient", "burst",
    "surge", "pulse", "pfmf", "ring wave",
}


class EMCTestClassifier:
    """Classifies EMC test type from table caption and column headers."""

    def classify_table(self, caption: str, headers: list[str]) -> EMCTestType:
        """Return the EMCTestType for a table given its caption and column headers."""
        combined = (caption + " " + " ".join(headers)).lower()
        return self._classify_text(combined)

    def classify_from_section_path(self, section_path: list[str]) -> EMCTestType:
        """Return the EMCTestType inferred from a document section path."""
        combined = " ".join(section_path).lower()
        return self._classify_text(combined)

    def _classify_text(self, text: str) -> EMCTestType:
        for signal in _ESD_SIGNALS:
            if signal in text:
                return EMCTestType.ESD
        for signal in _RADIATED_IMMUNITY_SIGNALS:
            if signal in text:
                return EMCTestType.RADIATED_IMMUNITY
        for signal in _CONDUCTED_EMISSIONS_SIGNALS:
            if signal in text:
                return EMCTestType.CONDUCTED_EMISSIONS
        for signal in _TRANSIENT_SIGNALS:
            if signal in text:
                return EMCTestType.TRANSIENT_IMMUNITY
        return EMCTestType.UNKNOWN


# ──────────────────────────────────────────────────────────────────────────────
# NormalizedFact extractor
# ──────────────────────────────────────────────────────────────────────────────

class NormalizedFactExtractor:
    """Extracts NormalizedFact objects from raw cell text using offline regex patterns."""

    def __init__(self) -> None:
        self._normalizer = UnitNormalizer()

    def extract_from_cell(
        self,
        cell_text: str,
        column_name: str = "",
        owner_object_id: str = "",
        entity_type: OntologyEntityType | None = None,
    ) -> list[Any]:  # list[NormalizedFact] — avoid circular import, caller imports
        from grc_policy_server.services.documents.canonical_table_model import NormalizedFact

        facts: list[NormalizedFact] = []
        if not cell_text or not cell_text.strip():
            return facts

        col_lower = column_name.lower()

        # Frequency range detection (before single frequency to consume both endpoints)
        for m in _FREQ_RANGE_RE.finditer(cell_text):
            v1_hz, _ = self._normalizer.normalize_frequency(m.group(1), m.group(2))
            v2_hz, _ = self._normalizer.normalize_frequency(m.group(3), m.group(4))
            facts.append(NormalizedFact(
                fact_id=_new_fact_id(),
                owner_object_id=owner_object_id,
                fact_type="frequency_range",
                name="frequency_range",
                value=f"{v1_hz}-{v2_hz}",
                unit="Hz",
                raw_value=m.group(0),
                confidence=0.95,
            ))

        # Single frequency (only if no range already captured for this text)
        if not facts or "frequency" not in col_lower:
            for m in _FREQ_SINGLE_RE.finditer(cell_text):
                # Skip if already covered by range match
                if any(f.raw_value and m.group(0) in f.raw_value for f in facts):
                    continue
                v_hz, _ = self._normalizer.normalize_frequency(m.group(1), m.group(2))
                facts.append(NormalizedFact(
                    fact_id=_new_fact_id(),
                    owner_object_id=owner_object_id,
                    fact_type="frequency_range",
                    name="frequency",
                    value=str(v_hz),
                    unit="Hz",
                    raw_value=m.group(0),
                    confidence=0.85,
                ))

        # Field strength
        for m in _FIELD_STRENGTH_RE.finditer(cell_text):
            facts.append(NormalizedFact(
                fact_id=_new_fact_id(),
                owner_object_id=owner_object_id,
                fact_type="field_strength",
                name="field_strength",
                value=m.group(1).replace(",", "."),
                unit=m.group(2),
                raw_value=m.group(0),
                confidence=0.95,
            ))

        # Emission limit (dBuV, dBuA)
        for m in _EMISSION_LIMIT_RE.finditer(cell_text):
            norm_unit, _ = self._normalizer.normalize_unit(m.group(2))
            facts.append(NormalizedFact(
                fact_id=_new_fact_id(),
                owner_object_id=owner_object_id,
                fact_type="emission_limit",
                name="emission_limit",
                value=m.group(1).replace(",", "."),
                unit=norm_unit,
                raw_value=m.group(0),
                confidence=0.95,
            ))

        # Tolerance-format voltage: "13, 5 ± 0, 5 V" — always extract
        for m in _TOLERANCE_VOLTAGE_RE.finditer(cell_text):
            raw_v = m.group(1).replace(" ", "").replace(",", ".")
            try:
                float(raw_v)
            except ValueError:
                continue
            facts.append(NormalizedFact(
                fact_id=_new_fact_id(),
                owner_object_id=owner_object_id,
                fact_type="field_strength",
                name="voltage_level",
                value=raw_v,
                unit=m.group(2),
                raw_value=m.group(0),
                confidence=0.88,
            ))

        # kV levels — always extract regardless of column name (ESD/transient, ± prefix allowed)
        for m in _KV_STRONG_RE.finditer(cell_text):
            facts.append(NormalizedFact(
                fact_id=_new_fact_id(),
                owner_object_id=owner_object_id,
                fact_type="field_strength",
                name="voltage_level",
                value=m.group(1).replace(",", "."),
                unit=m.group(2),
                raw_value=m.group(0),
                confidence=0.92,
            ))

        # V levels (lower confidence, gate on column context to avoid false positives)
        if any(kw in col_lower for kw in ("voltage", "spannung", "level", "pegel", "esd", "u s", "u in", "prüfspannung")):
            for m in _VOLTAGE_LEVEL_RE.finditer(cell_text):
                # Skip if already captured as kV
                if any(f.raw_value and m.group(0) in f.raw_value for f in facts):
                    continue
                facts.append(NormalizedFact(
                    fact_id=_new_fact_id(),
                    owner_object_id=owner_object_id,
                    fact_type="field_strength",
                    name="voltage_level",
                    value=m.group(1).replace(",", "."),
                    unit=m.group(2),
                    raw_value=m.group(0),
                    confidence=0.85,
                ))

        # Normative term
        for m in _NORMATIVE_TERM_RE.finditer(cell_text):
            term_lower = m.group(0).lower().strip()
            strength = NORMATIVE_TERM_STRENGTH.get(term_lower, "unknown")
            facts.append(NormalizedFact(
                fact_id=_new_fact_id(),
                owner_object_id=owner_object_id,
                fact_type="normative_term",
                name="normative_term",
                value=term_lower,
                unit=strength,
                raw_value=m.group(0),
                confidence=0.90,
            ))
            break  # one normative term per cell is enough

        # Acceptance class (Class A/B/C/D/E)
        for m in _CLASS_RE.finditer(cell_text):
            class_letter = (m.group(1) or m.group(2) or "").upper()
            facts.append(NormalizedFact(
                fact_id=_new_fact_id(),
                owner_object_id=owner_object_id,
                fact_type="acceptance_criterion",
                name="acceptance_class",
                value=f"class_{class_letter.lower()}",
                unit="",
                raw_value=m.group(0),
                confidence=0.88,
            ))

        return facts


def _new_fact_id() -> str:
    return f"NF-{uuid.uuid4().hex[:12]}"
