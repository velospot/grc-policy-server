"""Document family profiles for compliance PDFs.

A DocumentFamilyProfile bundles family-specific extraction configuration so
that the ingestion pipeline can adapt to different document families without
hardcoding domain knowledge inside general-purpose modules.

Usage:
    from grc_policy_server.services.ingestion.document_family_profile import (
        get_profile_for_document,
        TL81000_PROFILE,
        DIN_EN_60068_PROFILE,
        DNV_CG_0339_PROFILE,
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from grc_policy_server.services.ingestion.ontology.emc_ontology import OntologyEntityType


@dataclass(frozen=True)
class DocumentFamilyProfile:
    """Immutable extraction configuration for one document family."""

    family_id: str
    domain: str
    languages: tuple[str, ...]

    # Signals used by EMCTestClassifier for caption / heading matching
    conducted_emission_signals: tuple[str, ...]
    radiated_immunity_signals: tuple[str, ...]
    transient_immunity_signals: tuple[str, ...]
    esd_signals: tuple[str, ...]

    # When True, the degenerate-table filter skips structural/list-pattern heuristics.
    # Use for technical standards where Docling-detected tables are almost always real.
    conservative_table_filter: bool = False

    # Per-entity-type canonical unit (used for column-unit inheritance)
    # Keys are OntologyEntityType.value strings to stay import-free at module level.
    column_unit_map: dict[str, str] = field(default_factory=dict)

    # Table stitching weight overrides (keys match TableIdentityResolver weight names)
    stitching_weights: dict[str, float] = field(default_factory=dict)

    # Heading/caption patterns that indicate continuation tables (regex strings)
    continuation_patterns: tuple[str, ...] = (
        r"\(\s*(?:fortgesetzt|continued|suite|cont\.?)\s*\)",
    )

    # Known header/footer text fragments to suppress (substring match, case-insensitive)
    footer_suppress_patterns: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# TL 81000 family profile (VW/Audi/Porsche/Skoda/Seat automotive EMC standard)
# ---------------------------------------------------------------------------

TL81000_PROFILE = DocumentFamilyProfile(
    family_id="tl_81000",
    domain="automotive_emc",
    languages=("de", "en"),
    conducted_emission_signals=(
        # German
        "leitungsgebundene störaussendung",
        "leitungsgebundene emission",
        "störaussendung",
        "leitungsgebunden",
        "geführte störaussendung",
        "netzgeführt",
        # English
        "conducted emission",
        "conducted disturbance",
        "conducted interference",
        "ce",
    ),
    radiated_immunity_signals=(
        # German
        "strahlungsgebundene störfestigkeit",
        "feldstärke",
        "antenne",
        "fahrzeugprüfung",
        "strahlungsimmunität",
        # English
        "radiated immunity",
        "radiated field",
        "field strength",
        "ri",
    ),
    transient_immunity_signals=(
        # German
        "transiente störfestigkeit",
        "transienten",
        "impuls",
        "impulsförmige",
        "einstellwert",
        # English
        "transient immunity",
        "transient",
        "pulse",
        "burst",
    ),
    esd_signals=(
        # German
        "elektrostatische entladung",
        "esd",
        "luftentladung",
        "kontaktentladung",
        # English
        "electrostatic discharge",
        "esd",
        "air discharge",
        "contact discharge",
    ),
    column_unit_map={
        "emission_limit": "dBuV",
        "field_strength": "V/m",
        "frequency_range": "Hz",
        "immunity_level": "V/m",
    },
    stitching_weights={
        "col_header_sim": 0.35,
        "caption_sim": 0.20,
        "adjacency": 0.10,
        "section_compat": 0.15,
        "schema_hash": 0.20,
    },
    footer_suppress_patterns=(
        "volkswagen ag vertraulich",
        "vw ag confidential",
        "confidential",
        "vertraulich",
        "nur für internen gebrauch",
        "internal use only",
    ),
)


# ---------------------------------------------------------------------------
# DIN EN 60068 family profile (IEC environmental testing — vibration, shock, …)
# ---------------------------------------------------------------------------

DIN_EN_60068_PROFILE = DocumentFamilyProfile(
    family_id="din_en_60068",
    domain="iec_environmental_testing",
    languages=("de", "en"),
    conservative_table_filter=True,
    # DIN EN 60068 covers vibration/shock testing, not EMC — no EMC signals.
    conducted_emission_signals=(),
    radiated_immunity_signals=(),
    transient_immunity_signals=(),
    esd_signals=(),
    column_unit_map={
        "frequency_range": "Hz",
        "acceleration": "m/s²",
        "psd": "(m/s²)²/Hz",
        "test_duration": "h",
    },
    stitching_weights={
        "col_header_sim": 0.40,
        "caption_sim": 0.25,
        "adjacency": 0.15,
        "section_compat": 0.10,
        "schema_hash": 0.10,
    },
    continuation_patterns=(
        r"\(\s*(?:fortgesetzt|continued|cont\.?|von\s+\d+)\s*\)",
        r"\(\d+\s+von\s+\d+\)",
    ),
    footer_suppress_patterns=(
        "din deutsches institut für normung",
        "vde verlag",
        "nur für intern",
        "beuth verlag",
    ),
)


# ---------------------------------------------------------------------------
# DNV CG-0339 family profile (DNV maritime EMC classification requirements)
# ---------------------------------------------------------------------------

DNV_CG_0339_PROFILE = DocumentFamilyProfile(
    family_id="dnv_cg_0339",
    domain="maritime_emc",
    languages=("en",),
    conservative_table_filter=True,
    conducted_emission_signals=(
        "conducted emission",
        "conducted disturbance",
        "power port",
        "conducted radio frequency",
        "conducted low frequency",
        "limits quasi-peak",
        "enclosure port emission",
    ),
    radiated_immunity_signals=(
        "radiated electromagnetic field",
        "radiated emission",
        "radiated field immunity",
        "enclosure port",
        "electromagnetic field immunity",
        "radiated susceptibility",
    ),
    transient_immunity_signals=(
        "electrical fast transient",
        "eft",
        "burst",
        "electrical slow transient",
        "surge",
        "conducted radio frequency immunity",
    ),
    esd_signals=(
        "electrostatic discharge",
        "esd",
    ),
    column_unit_map={
        "frequency_range": "Hz",
        "test_level": "V/m",
        "emission_limit": "dBuV",
        "immunity_level": "V/m",
    },
    stitching_weights={
        "col_header_sim": 0.35,
        "caption_sim": 0.25,
        "adjacency": 0.10,
        "section_compat": 0.15,
        "schema_hash": 0.15,
    },
    continuation_patterns=(
        r"\(\s*(?:continued|cont\.?)\s*\)",
    ),
    footer_suppress_patterns=(
        "dnv gl as",
        "det norske veritas",
        "classification notes",
        "dnv gl rules",
    ),
)


# ---------------------------------------------------------------------------
# Registry and lookup helpers
# ---------------------------------------------------------------------------

_PROFILES: dict[str, DocumentFamilyProfile] = {
    TL81000_PROFILE.family_id: TL81000_PROFILE,
    DIN_EN_60068_PROFILE.family_id: DIN_EN_60068_PROFILE,
    DNV_CG_0339_PROFILE.family_id: DNV_CG_0339_PROFILE,
}


def get_profile(family_id: str) -> DocumentFamilyProfile | None:
    """Return the profile for *family_id*, or None if not registered."""
    return _PROFILES.get(family_id)


_TL81000_CAPTION_SIGNALS = frozenset(
    word
    for sig in (
        TL81000_PROFILE.conducted_emission_signals
        + TL81000_PROFILE.radiated_immunity_signals
        + TL81000_PROFILE.transient_immunity_signals
        + TL81000_PROFILE.esd_signals
    )
    for word in sig.lower().split()
    if len(word) >= 4
)

_TL81000_FILENAME_HINTS = ("tl_81000", "tl81000", "tl 81000", "tl-81000")
_DIN_EN_60068_FILENAME_HINTS = ("din_en_60068", "din en 60068", "60068-2", "60068_2")
_DNV_CG_0339_FILENAME_HINTS = ("dnvgl-cg-0339", "dnv-cg-0339", "dnv_cg_0339", "cg-0339", "cg0339")


def get_profile_for_document(
    filename: str = "",
    section_path: list[str] | None = None,
) -> DocumentFamilyProfile | None:
    """Heuristically identify which profile applies to a document.

    Checks filename first (fast path), then section headings.
    Returns None when no profile can be determined.
    """
    name_lower = (filename or "").lower()
    if any(hint in name_lower for hint in _TL81000_FILENAME_HINTS):
        return TL81000_PROFILE
    if any(hint in name_lower for hint in _DIN_EN_60068_FILENAME_HINTS):
        return DIN_EN_60068_PROFILE
    if any(hint in name_lower for hint in _DNV_CG_0339_FILENAME_HINTS):
        return DNV_CG_0339_PROFILE

    if section_path:
        combined = " ".join(section_path).lower()
        # TL 81000 section headings frequently mention "prüfschärfe", "fpsc",
        # "störfestigkeit", "störaussendung" — check for ≥2 signal words
        hits = sum(1 for word in _TL81000_CAPTION_SIGNALS if word in combined)
        if hits >= 2:
            return TL81000_PROFILE

    return None
