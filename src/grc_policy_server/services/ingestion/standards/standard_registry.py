from __future__ import annotations

import re
from pydantic import BaseModel

_YEAR_RE = re.compile(r"\b(20\d{2}|19\d{2})\b")

_ALIAS_TO_ID: dict[str, str] = {
    "cispr 25": "cispr_25",
    "cispr25": "cispr_25",
    "cispr 32": "cispr_32",
    "cispr32": "cispr_32",
    "cispr 35": "cispr_35",
    "cispr35": "cispr_35",
    "iec 61000-4-2": "iec_61000_4_2",
    "iec 61000-4-3": "iec_61000_4_3",
    "iec 61000-4-4": "iec_61000_4_4",
    "iec 61000-4-5": "iec_61000_4_5",
    "iec 61000-4-6": "iec_61000_4_6",
    "iec 61000-4-8": "iec_61000_4_8",
    "iec 61000-4-11": "iec_61000_4_11",
    "iec 61000-3-2": "iec_61000_3_2",
    "iec 61000-3-3": "iec_61000_3_3",
    "iec 62368-1": "iec_62368_1",
    "iec 60950-1": "iec_60950_1",
    "iec 60065": "iec_60065",
    "fcc part 15": "fcc_part_15",
    "fcc 15": "fcc_part_15",
    "iso 14001": "iso_14001",
    "iso 45001": "iso_45001",
    "iso 9001": "iso_9001",
    "iso/iec 17025": "iso_iec_17025",
    "en 55032": "en_55032",
    "en55032": "en_55032",
    "en 55035": "en_55035",
    "en55035": "en_55035",
    "en 61000-3-2": "en_61000_3_2",
    "en 61000-3-3": "en_61000_3_3",
}

# Known valid publication years per standard ID.  Empty set = any year accepted.
_KNOWN_VERSIONS: dict[str, set[str]] = {
    "cispr_25": {"2008", "2012", "2016", "2021"},
    "cispr_32": {"2012", "2015", "2022"},
    "cispr_35": {"2017", "2023"},
    "en_55032": {"2012", "2015", "2022"},
    "en_55035": {"2017", "2023"},
    "fcc_part_15": set(),
    "iso_9001": {"2008", "2015"},
    "iso_14001": {"2004", "2015"},
    "iso_45001": {"2018"},
    "iso_iec_17025": {"2005", "2017"},
}


class StandardResolution(BaseModel):
    standard_id: str
    version: str = "unknown"
    confidence: float = 0.9
    review_flag: bool = False


class StandardRegistry:
    """Resolve raw standard references to canonical IDs and publication years."""

    def resolve(self, raw_ref: str) -> StandardResolution:
        normalized = re.sub(r"\s+", " ", (raw_ref or "").strip().lower())
        standard_id = self._lookup_id(normalized)
        if standard_id is None:
            return StandardResolution(
                standard_id=re.sub(r"[^a-z0-9]", "_", normalized) or "unknown",
                version="unknown",
                confidence=0.5,
                review_flag=True,
            )
        year = self._extract_year(raw_ref, standard_id)
        if year is None:
            return StandardResolution(
                standard_id=standard_id,
                version="unknown",
                confidence=0.85,
                review_flag=True,
            )
        return StandardResolution(
            standard_id=standard_id,
            version=year,
            confidence=0.95,
            review_flag=False,
        )

    def _lookup_id(self, normalized: str) -> str | None:
        if normalized in _ALIAS_TO_ID:
            return _ALIAS_TO_ID[normalized]
        stripped = re.sub(r"[-:]+$", "", normalized).strip()
        return _ALIAS_TO_ID.get(stripped)

    def _extract_year(self, raw_ref: str, standard_id: str) -> str | None:
        years = _YEAR_RE.findall(raw_ref or "")
        if not years:
            return None
        known = _KNOWN_VERSIONS.get(standard_id, set())
        for year in reversed(years):
            if not known or year in known:
                return year
        return years[-1]


_registry = StandardRegistry()


def resolve_standard(raw_ref: str) -> StandardResolution:
    return _registry.resolve(raw_ref)
