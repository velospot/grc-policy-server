"""Environment domain ontology — entity types, test classification, and NormalizedFact extraction.

Covers IEC 60068 environmental testing (temperature, humidity, salt fog, vibration),
RoHS/REACH substance limits, corrosion resistance, and thermal shock. Offline-only
processing (regex + dict lookups, no LLM).
"""

from __future__ import annotations

import re
import uuid
from enum import Enum
from typing import Any


class EnvTestType(str, Enum):
    """Detected environmental test type."""

    THERMAL_CYCLING = "thermal_cycling"      # IEC 60068-2-14 thermal shock / cycling
    HUMIDITY = "humidity"                    # IEC 60068-2-78 damp heat, 60068-2-38
    SALT_FOG = "salt_fog"                    # IEC 60068-2-11 / ISO 9227 salt spray
    VIBRATION = "vibration"                  # IEC 60068-2-6/64 vibration
    SHOCK = "shock"                          # IEC 60068-2-27 mechanical shock
    ROHS_REACH = "rohs_reach"               # RoHS/REACH substance thresholds
    CORROSION = "corrosion"                  # Corrosion resistance / coating
    UNKNOWN = "unknown"


class EnvEntityType(str, Enum):
    """Entity types for the environment domain ontology."""

    TEMPERATURE_RANGE = "TemperatureRange"     # °C operating / storage range
    HUMIDITY_LEVEL = "HumidityLevel"           # % RH
    TEST_DURATION = "TestDuration"             # hours / cycles
    SUBSTANCE_LIMIT = "SubstanceLimit"         # mg/kg (ppm) substance concentration
    VIBRATION_LEVEL = "VibrationLevel"         # g or m/s² acceleration
    SHOCK_LEVEL = "ShockLevel"                 # g peak shock
    SALT_CONCENTRATION = "SaltConcentration"   # % NaCl in salt fog solution
    THERMAL_RATE = "ThermalRate"               # °C/min or °C/h ramp rate
    IP_RATING = "IPRating"                     # Ingress protection (env context)
    CORROSION_CLASS = "CorrosionClass"         # ISO 9223 corrosivity category


# Domain-specific primary key columns per test type
ENV_DOMAIN_ROW_KEYS: dict[EnvTestType, list[str]] = {
    EnvTestType.THERMAL_CYCLING: ["temperature_range", "thermal_rate", "test_duration"],
    EnvTestType.HUMIDITY: ["humidity_level", "temperature_range", "test_duration"],
    EnvTestType.SALT_FOG: ["salt_concentration", "test_duration", "temperature_range"],
    EnvTestType.VIBRATION: ["vibration_level", "frequency_range", "test_duration"],
    EnvTestType.SHOCK: ["shock_level", "pulse_duration", "test_direction"],
    EnvTestType.ROHS_REACH: ["substance", "substance_limit", "application"],
    EnvTestType.CORROSION: ["corrosion_class", "exposure_condition", "test_duration"],
}

# Entities that always produce HIGH severity when changed.
# Must match the fact_type/name strings produced by EnvFactExtractor (lowercase).
ENV_HIGH_SEVERITY_ENTITIES: frozenset[str] = frozenset({
    "substance_limit",
    "substance_limit_pct",
    "temperature_range",
    "corrosion_class",
})


# ──────────────────────────────────────────────────────────────────────────────
# Compiled regex patterns
# ──────────────────────────────────────────────────────────────────────────────

# Temperature: single value or range  e.g. "-40 °C", "−40 °C to +85 °C"
_TEMP_SINGLE_RE = re.compile(r"([+\-−±]?\s*\d+(?:[.,]\d+)?)\s*°\s*([CF])\b", re.IGNORECASE)
_TEMP_RANGE_RE = re.compile(
    r"([+\-−]?\s*\d+(?:[.,]\d+)?)\s*°\s*[CF]\s*(?:to|bis|…|\.\.\.|\-|–|—)\s*"
    r"([+\-−]?\s*\d+(?:[.,]\d+)?)\s*°\s*([CF])\b",
    re.IGNORECASE,
)

# Humidity: % RH
_HUMIDITY_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%\s*(?:RH|r\.h\.|relative humidity)\b", re.IGNORECASE)
_HUMIDITY_PCT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%(?!\s*NaCl)", re.IGNORECASE)

# Duration: hours, minutes, cycles
_DURATION_H_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:h|hours?|stunden?)\b", re.IGNORECASE)
_DURATION_CYCLES_RE = re.compile(r"(\d+)\s*(?:cycles?|zyklen?|Zyklen)\b", re.IGNORECASE)

# Substance limit: mg/kg (= ppm) for RoHS/REACH
_SUBSTANCE_LIMIT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:mg/kg|ppm|mg\s*/\s*kg)\b", re.IGNORECASE)
_PERCENT_WEIGHT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%\s*(?:by\s+weight|weight|w/?w)\b", re.IGNORECASE)

# Vibration: g or m/s²
_VIBRATION_G_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*g\b(?!\s*/)", re.IGNORECASE)
_VIBRATION_MS2_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*m/s[²2]\b", re.IGNORECASE)

# Thermal ramp rate
_THERMAL_RATE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*°\s*[CF]\s*/\s*(?:min|h)\b", re.IGNORECASE)

# Salt concentration: % NaCl
_SALT_CONC_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%\s*NaCl\b", re.IGNORECASE)

# Corrosion class: C1–C5, CX (ISO 9223)
_CORROSION_CLASS_RE = re.compile(r"\bC([1-5X])\b")

# IP rating (env context)
_IP_RATING_RE = re.compile(r"\bIP\s*([0-6X][0-9X])\b", re.IGNORECASE)


# ──────────────────────────────────────────────────────────────────────────────
# Test type classifier
# ──────────────────────────────────────────────────────────────────────────────

_THERMAL_CYCLING_SIGNALS = {
    "thermal shock", "thermal cycling", "temperature cycling",
    "thermischer schock", "temperaturwechsel", "iec 60068-2-14",
    "temperature change", "cold start", "heat soak",
}
_HUMIDITY_SIGNALS = {
    "damp heat", "humidity", "feuchte", "feuchtwärme", "relative humidity",
    "iec 60068-2-78", "iec 60068-2-38", "85/85", "condensation",
    "moisture resistance",
}
_SALT_FOG_SIGNALS = {
    "salt fog", "salt spray", "salt mist", "salzsprüh", "salzsprühnebel",
    "iso 9227", "astm b117", "iec 60068-2-11", "corrosion test",
    "salznebeltest",
}
_VIBRATION_ENV_SIGNALS = {
    "iec 60068-2-6", "iec 60068-2-64", "sinusoidal vibration", "random vibration",
    "vibration test", "schwingungsprüfung", "resonance frequency",
}
_SHOCK_ENV_SIGNALS = {
    "mechanical shock", "drop test", "shock pulse", "iec 60068-2-27",
    "half-sine", "sawtooth pulse", "falltest",
}
_ROHS_REACH_SIGNALS = {
    "rohs", "reach", "hazardous substance", "restricted substance",
    "substance limit", "mg/kg", "cadmium", "lead", "mercury",
    "hexavalent chromium", "pbde", "svhc",
}
_CORROSION_SIGNALS = {
    "corrosion class", "iso 9223", "korrosionsschutz", "coating",
    "surface protection", "electroplating", "galvanic",
}


class EnvTestClassifier:
    """Classifies environmental test type from table caption and column headers."""

    def classify_table(self, caption: str, headers: list[str]) -> EnvTestType:
        combined = (caption + " " + " ".join(headers)).lower()
        return self._classify_text(combined)

    def classify_from_section_path(self, section_path: list[str]) -> EnvTestType:
        combined = " ".join(section_path).lower()
        return self._classify_text(combined)

    def _classify_text(self, text: str) -> EnvTestType:
        for signal in _ROHS_REACH_SIGNALS:
            if signal in text:
                return EnvTestType.ROHS_REACH
        for signal in _SALT_FOG_SIGNALS:
            if signal in text:
                return EnvTestType.SALT_FOG
        for signal in _THERMAL_CYCLING_SIGNALS:
            if signal in text:
                return EnvTestType.THERMAL_CYCLING
        for signal in _HUMIDITY_SIGNALS:
            if signal in text:
                return EnvTestType.HUMIDITY
        for signal in _SHOCK_ENV_SIGNALS:
            if signal in text:
                return EnvTestType.SHOCK
        for signal in _VIBRATION_ENV_SIGNALS:
            if signal in text:
                return EnvTestType.VIBRATION
        for signal in _CORROSION_SIGNALS:
            if signal in text:
                return EnvTestType.CORROSION
        return EnvTestType.UNKNOWN


# ──────────────────────────────────────────────────────────────────────────────
# NormalizedFact extractor
# ──────────────────────────────────────────────────────────────────────────────

def _new_fact_id() -> str:
    return str(uuid.uuid4())


class EnvFactExtractor:
    """Extracts NormalizedFact objects from environmental domain table cells."""

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

        # Substance limit (mg/kg / ppm) — check before % to avoid ambiguity
        for m in _SUBSTANCE_LIMIT_RE.finditer(cell_text):
            facts.append(NormalizedFact(
                fact_id=_new_fact_id(),
                owner_object_id=owner_object_id,
                fact_type="substance_limit",
                name="substance_limit",
                value=m.group(1).replace(",", "."),
                unit="mg/kg",
                raw_value=m.group(0),
                confidence=0.97,
            ))
        for m in _PERCENT_WEIGHT_RE.finditer(cell_text):
            facts.append(NormalizedFact(
                fact_id=_new_fact_id(),
                owner_object_id=owner_object_id,
                fact_type="substance_limit",
                name="substance_limit_pct",
                value=m.group(1).replace(",", "."),
                unit="%w/w",
                raw_value=m.group(0),
                confidence=0.93,
            ))

        # Salt concentration: % NaCl
        for m in _SALT_CONC_RE.finditer(cell_text):
            facts.append(NormalizedFact(
                fact_id=_new_fact_id(),
                owner_object_id=owner_object_id,
                fact_type="salt_concentration",
                name="salt_concentration",
                value=m.group(1).replace(",", "."),
                unit="%NaCl",
                raw_value=m.group(0),
                confidence=0.97,
            ))

        # Humidity (% RH explicit)
        for m in _HUMIDITY_RE.finditer(cell_text):
            facts.append(NormalizedFact(
                fact_id=_new_fact_id(),
                owner_object_id=owner_object_id,
                fact_type="humidity_level",
                name="humidity_level",
                value=m.group(1).replace(",", "."),
                unit="%RH",
                raw_value=m.group(0),
                confidence=0.97,
            ))
        # Bare % when column name hints humidity
        if not any(f.fact_type == "humidity_level" for f in facts):
            if any(k in col_lower for k in ("humidity", "feuchte", "rh")):
                for m in _HUMIDITY_PCT_RE.finditer(cell_text):
                    facts.append(NormalizedFact(
                        fact_id=_new_fact_id(),
                        owner_object_id=owner_object_id,
                        fact_type="humidity_level",
                        name="humidity_level",
                        value=m.group(1).replace(",", "."),
                        unit="%RH",
                        raw_value=m.group(0),
                        confidence=0.85,
                    ))

        # Temperature range (try range first, then single)
        m_range = _TEMP_RANGE_RE.search(cell_text)
        if m_range:
            facts.append(NormalizedFact(
                fact_id=_new_fact_id(),
                owner_object_id=owner_object_id,
                fact_type="temperature_range",
                name="temperature_range",
                value=f"{m_range.group(1).replace(' ', '')}/{m_range.group(2).replace(' ', '')}",
                unit=f"°{m_range.group(3).upper()}",
                raw_value=m_range.group(0),
                confidence=0.95,
            ))
        else:
            for m in _TEMP_SINGLE_RE.finditer(cell_text):
                facts.append(NormalizedFact(
                    fact_id=_new_fact_id(),
                    owner_object_id=owner_object_id,
                    fact_type="temperature_range",
                    name="temperature",
                    value=m.group(1).replace(" ", "").replace(",", "."),
                    unit=f"°{m.group(2).upper()}",
                    raw_value=m.group(0),
                    confidence=0.90,
                ))

        # Thermal ramp rate
        for m in _THERMAL_RATE_RE.finditer(cell_text):
            facts.append(NormalizedFact(
                fact_id=_new_fact_id(),
                owner_object_id=owner_object_id,
                fact_type="thermal_rate",
                name="thermal_rate",
                value=m.group(1).replace(",", "."),
                unit=m.group(0).split(m.group(1))[-1].strip(),
                raw_value=m.group(0),
                confidence=0.93,
            ))

        # Test duration
        for m in _DURATION_H_RE.finditer(cell_text):
            facts.append(NormalizedFact(
                fact_id=_new_fact_id(),
                owner_object_id=owner_object_id,
                fact_type="test_duration",
                name="test_duration_hours",
                value=m.group(1).replace(",", "."),
                unit="h",
                raw_value=m.group(0),
                confidence=0.90,
            ))
        for m in _DURATION_CYCLES_RE.finditer(cell_text):
            facts.append(NormalizedFact(
                fact_id=_new_fact_id(),
                owner_object_id=owner_object_id,
                fact_type="test_duration",
                name="test_duration_cycles",
                value=m.group(1),
                unit="cycles",
                raw_value=m.group(0),
                confidence=0.90,
            ))

        # Vibration level
        if any(k in col_lower for k in ("vibr", "accel", "beschl", "g ", "m/s")):
            for m in _VIBRATION_G_RE.finditer(cell_text):
                facts.append(NormalizedFact(
                    fact_id=_new_fact_id(),
                    owner_object_id=owner_object_id,
                    fact_type="vibration_level",
                    name="vibration_g",
                    value=m.group(1).replace(",", "."),
                    unit="g",
                    raw_value=m.group(0),
                    confidence=0.88,
                ))
            for m in _VIBRATION_MS2_RE.finditer(cell_text):
                facts.append(NormalizedFact(
                    fact_id=_new_fact_id(),
                    owner_object_id=owner_object_id,
                    fact_type="vibration_level",
                    name="vibration_ms2",
                    value=m.group(1).replace(",", "."),
                    unit="m/s²",
                    raw_value=m.group(0),
                    confidence=0.88,
                ))

        # Corrosion class
        for m in _CORROSION_CLASS_RE.finditer(cell_text):
            facts.append(NormalizedFact(
                fact_id=_new_fact_id(),
                owner_object_id=owner_object_id,
                fact_type="corrosion_class",
                name="corrosion_class",
                value=f"C{m.group(1)}",
                unit="",
                raw_value=m.group(0),
                confidence=0.90,
            ))

        # IP rating
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

        return facts

    def extract_bare_numeric_with_unit(
        self,
        cell_text: str,
        column_unit: str,
        fact_type: str,
    ) -> list[Any]:
        """Fallback: bare numeric when column header carries the unit."""
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
