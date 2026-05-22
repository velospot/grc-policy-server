"""Safety domain ontology — entity types, test classification, and NormalizedFact extraction.

Covers IEC 62368-1 (audio/video/IT equipment), ISO 26262 / IEC 61508 (functional safety),
LV 123 (HV automotive safety), IEC 60529 IP ratings, and general electrical safety
standards. All processing is offline (regex + dict lookups, no LLM).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any


class SafetyTestType(str, Enum):
    """Detected safety test type — determines domain-specific row key structure."""

    ELECTRICAL_SAFETY = "electrical_safety"    # IEC 62368-1, LV 123 insulation/voltage
    FUNCTIONAL_SAFETY = "functional_safety"    # ISO 26262, IEC 61508 SIL/ASIL levels
    MECHANICAL = "mechanical"                  # Torque, force, vibration (safety context)
    THERMAL = "thermal"                        # Temperature limits, thermal runaway
    IP_RATING = "ip_rating"                    # IEC 60529 ingress protection
    CHEMICAL = "chemical"                      # Battery electrolyte, coolant safety
    UNKNOWN = "unknown"


class SafetyEntityType(str, Enum):
    """Entity types for the safety domain ontology."""

    PROTECTION_CLASS = "ProtectionClass"          # I, II, III (IEC 61140)
    CREEPAGE_DISTANCE = "CreepageDistance"        # mm creepage across insulation
    CLEARANCE = "Clearance"                       # mm air clearance
    WORKING_VOLTAGE = "WorkingVoltage"            # Vrms / Vpeak working voltage
    IP_RATING = "IPRating"                        # IPXX ingress protection level
    TEMPERATURE_LIMIT = "TemperatureLimit"        # °C max/min operating temp
    FUNCTIONAL_SAFETY_LEVEL = "FunctionalSafetyLevel"  # SIL 1-4, ASIL A-D
    INSULATION_RESISTANCE = "InsulationResistance"  # MΩ / GΩ
    DIELECTRIC_STRENGTH = "DielectricStrength"    # kVrms withstand
    FAULT_TOLERANCE = "FaultTolerance"            # DC / SC fault handling requirement


# Domain-specific primary key columns per test type
SAFETY_DOMAIN_ROW_KEYS: dict[SafetyTestType, list[str]] = {
    SafetyTestType.ELECTRICAL_SAFETY: [
        "protection_class", "working_voltage", "clearance", "creepage_distance"
    ],
    SafetyTestType.FUNCTIONAL_SAFETY: [
        "functional_safety_level", "failure_rate", "diagnostic_coverage"
    ],
    SafetyTestType.THERMAL: [
        "temperature_limit", "temperature_class", "component_location"
    ],
    SafetyTestType.IP_RATING: [
        "ip_rating", "protection_degree", "test_condition"
    ],
    SafetyTestType.MECHANICAL: [
        "torque", "force_limit", "material_class"
    ],
    SafetyTestType.CHEMICAL: [
        "substance", "concentration_limit", "exposure_route"
    ],
}

# Entities that always produce HIGH severity when changed.
# Must match the fact_type/name strings produced by SafetyFactExtractor (lowercase).
SAFETY_HIGH_SEVERITY_ENTITIES: frozenset[str] = frozenset({
    "protection_class",
    "creepage_distance",
    "clearance",
    "functional_safety_level",
    "sil_level",
    "asil_level",
    "dielectric_strength",
    "insulation_resistance",
})


# ──────────────────────────────────────────────────────────────────────────────
# Compiled regex patterns
# ──────────────────────────────────────────────────────────────────────────────

# IP rating: IP followed by 2 digits or X placeholders (e.g. IP67, IP2X, IPX4)
_IP_RATING_RE = re.compile(r"\bIP\s*([0-6X][0-9X])\b", re.IGNORECASE)

# Protection class: Class I / II / III (Roman numerals) or Klasse I/II/III
_PROTECTION_CLASS_RE = re.compile(
    r"\b(?:protection\s+class|class|klasse|schutzklasse)\s+(I{1,3}|IV|V|1|2|3)\b",
    re.IGNORECASE,
)
# Standalone protection class column values: just "I", "II", "III"
_PROTECTION_CLASS_STANDALONE_RE = re.compile(r"^\s*(I{1,3})\s*$")

# SIL / ASIL levels
_SIL_RE = re.compile(r"\bSIL\s*([1-4])\b", re.IGNORECASE)
_ASIL_RE = re.compile(r"\bASIL\s*([A-D])\b", re.IGNORECASE)

# Creepage and clearance: numeric mm values
_CREEPAGE_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*mm\b.*(?:creep|kriech|creepa)",
    re.IGNORECASE,
)
_CLEARANCE_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*mm\b.*(?:clear|luft|abstand)",
    re.IGNORECASE,
)
# Generic mm dimension (used as fallback when column name indicates creepage/clearance)
_MM_VALUE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*mm\b", re.IGNORECASE)

# Temperature: numeric °C or °F
_TEMP_RE = re.compile(r"([+\-±]?\s*\d+(?:[.,]\d+)?)\s*°\s*([CF])\b", re.IGNORECASE)

# Voltage (working): Vrms, Vpeak, kVrms, V (electrical safety context)
_WORKING_VOLTAGE_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(kVrms|Vrms|kVpeak|Vpeak|kV|V)\b(?!\s*/\s*m)",
    re.IGNORECASE,
)

# Insulation resistance: MΩ or GΩ
_INSULATION_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(GΩ|MΩ|GOhm|MOhm|GΩ|MΩ)\b", re.IGNORECASE)

# Dielectric strength (Hi-pot): kVrms withstand voltage
_DIELECTRIC_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kVrms|kV\s*rms|kVac|V\s*rms)\b", re.IGNORECASE)


# ──────────────────────────────────────────────────────────────────────────────
# Test type classifier
# ──────────────────────────────────────────────────────────────────────────────

_ELECTRICAL_SAFETY_SIGNALS = {
    "protection class", "schutzklasse", "creepage", "kriechstrecke", "clearance",
    "luft- und kriechstrecke", "dielectric strength", "durchschlagfestigkeit",
    "insulation resistance", "isolationswiderstand", "withstand voltage",
    "iec 62368", "iec 60950", "lv 123", "hvil", "high voltage interlock",
    "working voltage", "nennspannung", "reinforced insulation",
}
_FUNCTIONAL_SAFETY_SIGNALS = {
    "sil", "asil", "functional safety", "funktionale sicherheit",
    "iso 26262", "iec 61508", "iec 62061", "diagnostic coverage",
    "failure rate", "ausfallrate", "safe state", "fmea", "fmeda",
    "hardware fault tolerance", "probability of failure",
    "pfh", "pfhd", "pfd",
}
_THERMAL_SAFETY_SIGNALS = {
    "maximum operating temperature", "max temperature", "thermal runaway",
    "temperature class", "temperaturklasse", "thermal limit",
    "wärmebeständigkeit", "heat resistance", "t-class", "t-class rating",
    "temperature derating", "temperature coefficient",
}
_IP_RATING_SIGNALS = {
    "ip rating", "ingress protection", "ip code", "ipxx", "ip6", "ip5",
    "iec 60529", "schutzart", "protection degree",
    "dust protection", "water protection", "splash proof",
}
_MECHANICAL_SAFETY_SIGNALS = {
    "torque limit", "anzugsmoment", "shear force", "pull force",
    "mechanical strength", "mechanical stress", "impact resistance",
    "drop test", "crush resistance",
}
_CHEMICAL_SAFETY_SIGNALS = {
    "electrolyte", "coolant", "refrigerant", "substance limit",
    "chemical exposure", "rohs", "reach regulation", "hazardous material",
    "gefährdungsbeurteilung",
}


class SafetyTestClassifier:
    """Classifies safety test type from table caption and column headers."""

    def classify_table(self, caption: str, headers: list[str]) -> SafetyTestType:
        combined = (caption + " " + " ".join(headers)).lower()
        return self._classify_text(combined)

    def classify_from_section_path(self, section_path: list[str]) -> SafetyTestType:
        combined = " ".join(section_path).lower()
        return self._classify_text(combined)

    def _classify_text(self, text: str) -> SafetyTestType:
        for signal in _FUNCTIONAL_SAFETY_SIGNALS:
            if signal in text:
                return SafetyTestType.FUNCTIONAL_SAFETY
        for signal in _IP_RATING_SIGNALS:
            if signal in text:
                return SafetyTestType.IP_RATING
        for signal in _ELECTRICAL_SAFETY_SIGNALS:
            if signal in text:
                return SafetyTestType.ELECTRICAL_SAFETY
        for signal in _THERMAL_SAFETY_SIGNALS:
            if signal in text:
                return SafetyTestType.THERMAL
        for signal in _MECHANICAL_SAFETY_SIGNALS:
            if signal in text:
                return SafetyTestType.MECHANICAL
        for signal in _CHEMICAL_SAFETY_SIGNALS:
            if signal in text:
                return SafetyTestType.CHEMICAL
        return SafetyTestType.UNKNOWN


# ──────────────────────────────────────────────────────────────────────────────
# NormalizedFact extractor
# ──────────────────────────────────────────────────────────────────────────────

def _new_fact_id() -> str:
    return str(uuid.uuid4())


class SafetyFactExtractor:
    """Extracts NormalizedFact objects from safety domain table cells."""

    def extract_from_cell(
        self,
        cell_text: str,
        column_name: str = "",
        owner_object_id: str = "",
    ) -> list[Any]:
        from grc_policy_server.services.documents.canonical_table_model import NormalizedFact

        facts: list[NormalizedFact] = []
        if not cell_text or not cell_text.strip():
            return facts

        col_lower = column_name.lower()

        # IP rating (IP67, IP2X, etc.)
        for m in _IP_RATING_RE.finditer(cell_text):
            facts.append(NormalizedFact(
                fact_id=_new_fact_id(),
                owner_object_id=owner_object_id,
                fact_type="ip_rating",
                name="ip_rating",
                value=f"IP{m.group(1).upper()}",
                unit="",
                raw_value=m.group(0),
                confidence=0.97,
            ))

        # Functional safety level: SIL or ASIL
        for m in _SIL_RE.finditer(cell_text):
            facts.append(NormalizedFact(
                fact_id=_new_fact_id(),
                owner_object_id=owner_object_id,
                fact_type="functional_safety_level",
                name="sil_level",
                value=f"SIL {m.group(1)}",
                unit="",
                raw_value=m.group(0),
                confidence=0.97,
            ))
        for m in _ASIL_RE.finditer(cell_text):
            facts.append(NormalizedFact(
                fact_id=_new_fact_id(),
                owner_object_id=owner_object_id,
                fact_type="functional_safety_level",
                name="asil_level",
                value=f"ASIL {m.group(1).upper()}",
                unit="",
                raw_value=m.group(0),
                confidence=0.97,
            ))

        # Protection class
        m = _PROTECTION_CLASS_STANDALONE_RE.match(cell_text)
        if m and any(k in col_lower for k in ("class", "klasse", "schutz", "protection")):
            facts.append(NormalizedFact(
                fact_id=_new_fact_id(),
                owner_object_id=owner_object_id,
                fact_type="protection_class",
                name="protection_class",
                value=m.group(1),
                unit="",
                raw_value=cell_text.strip(),
                confidence=0.90,
            ))
        else:
            for m in _PROTECTION_CLASS_RE.finditer(cell_text):
                facts.append(NormalizedFact(
                    fact_id=_new_fact_id(),
                    owner_object_id=owner_object_id,
                    fact_type="protection_class",
                    name="protection_class",
                    value=m.group(1),
                    unit="",
                    raw_value=m.group(0),
                    confidence=0.92,
                ))

        # Creepage / clearance (contextual mm)
        if any(k in col_lower for k in ("creep", "kriech")):
            for m in _MM_VALUE_RE.finditer(cell_text):
                facts.append(NormalizedFact(
                    fact_id=_new_fact_id(),
                    owner_object_id=owner_object_id,
                    fact_type="creepage_distance",
                    name="creepage_distance",
                    value=m.group(1).replace(",", "."),
                    unit="mm",
                    raw_value=m.group(0),
                    confidence=0.93,
                ))
        elif any(k in col_lower for k in ("clear", "luft", "abstand")):
            for m in _MM_VALUE_RE.finditer(cell_text):
                facts.append(NormalizedFact(
                    fact_id=_new_fact_id(),
                    owner_object_id=owner_object_id,
                    fact_type="clearance",
                    name="clearance",
                    value=m.group(1).replace(",", "."),
                    unit="mm",
                    raw_value=m.group(0),
                    confidence=0.93,
                ))
        else:
            # Pattern-anchored creepage/clearance without column context
            for m in _CREEPAGE_RE.finditer(cell_text):
                facts.append(NormalizedFact(
                    fact_id=_new_fact_id(),
                    owner_object_id=owner_object_id,
                    fact_type="creepage_distance",
                    name="creepage_distance",
                    value=m.group(1).replace(",", "."),
                    unit="mm",
                    raw_value=m.group(0),
                    confidence=0.85,
                ))
            for m in _CLEARANCE_RE.finditer(cell_text):
                facts.append(NormalizedFact(
                    fact_id=_new_fact_id(),
                    owner_object_id=owner_object_id,
                    fact_type="clearance",
                    name="clearance",
                    value=m.group(1).replace(",", "."),
                    unit="mm",
                    raw_value=m.group(0),
                    confidence=0.85,
                ))

        # Temperature
        for m in _TEMP_RE.finditer(cell_text):
            facts.append(NormalizedFact(
                fact_id=_new_fact_id(),
                owner_object_id=owner_object_id,
                fact_type="temperature_limit",
                name="temperature_limit",
                value=m.group(1).replace(" ", "").replace(",", "."),
                unit=f"°{m.group(2).upper()}",
                raw_value=m.group(0),
                confidence=0.92,
            ))

        # Working voltage / dielectric strength
        for m in _DIELECTRIC_RE.finditer(cell_text):
            if any(k in col_lower for k in ("dielectric", "withstand", "hipot", "durchschlag")):
                facts.append(NormalizedFact(
                    fact_id=_new_fact_id(),
                    owner_object_id=owner_object_id,
                    fact_type="dielectric_strength",
                    name="dielectric_strength",
                    value=m.group(1).replace(",", "."),
                    unit=m.group(2),
                    raw_value=m.group(0),
                    confidence=0.93,
                ))
        if not any(f.fact_type == "dielectric_strength" for f in facts):
            for m in _WORKING_VOLTAGE_RE.finditer(cell_text):
                facts.append(NormalizedFact(
                    fact_id=_new_fact_id(),
                    owner_object_id=owner_object_id,
                    fact_type="working_voltage",
                    name="working_voltage",
                    value=m.group(1).replace(",", "."),
                    unit=m.group(2),
                    raw_value=m.group(0),
                    confidence=0.88,
                ))

        # Insulation resistance
        for m in _INSULATION_RE.finditer(cell_text):
            facts.append(NormalizedFact(
                fact_id=_new_fact_id(),
                owner_object_id=owner_object_id,
                fact_type="insulation_resistance",
                name="insulation_resistance",
                value=m.group(1).replace(",", "."),
                unit=m.group(2),
                raw_value=m.group(0),
                confidence=0.93,
            ))

        return facts

    def extract_bare_numeric_with_unit(
        self,
        cell_text: str,
        column_unit: str,
        fact_type: str,
    ) -> list[Any]:
        """Fallback: bare numeric value when column header carries the unit."""
        from grc_policy_server.services.documents.canonical_table_model import NormalizedFact

        plain_num_re = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*$")
        m = plain_num_re.match(cell_text)
        if not m:
            return []
        return [NormalizedFact(
            fact_id=_new_fact_id(),
            owner_object_id="",
            fact_type=fact_type,
            name=fact_type,
            value=m.group(1).replace(",", "."),
            unit=column_unit,
            raw_value=cell_text.strip(),
            confidence=0.75,
        )]
