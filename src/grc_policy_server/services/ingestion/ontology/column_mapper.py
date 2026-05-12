"""Maps raw table column headers (German + English) to OntologyEntityType values.

Case-insensitive lookups. Covers automotive EMC standard column naming conventions
(primarily TL 81000, CISPR 25, ISO 11452 family).
"""

from __future__ import annotations

from grc_policy_server.services.ingestion.ontology.emc_ontology import OntologyEntityType

# Maps normalized (lowercased, stripped) header text → OntologyEntityType
HEADER_TO_ENTITY: dict[str, OntologyEntityType] = {
    # Field strength / test level — German
    "prüfpegel": OntologyEntityType.FIELD_STRENGTH,
    "prüffeld": OntologyEntityType.FIELD_STRENGTH,
    "feldstärke": OntologyEntityType.FIELD_STRENGTH,
    "feldstaerke": OntologyEntityType.FIELD_STRENGTH,
    "pegel": OntologyEntityType.FIELD_STRENGTH,
    "störpegel": OntologyEntityType.FIELD_STRENGTH,
    "stoerpegel": OntologyEntityType.FIELD_STRENGTH,
    "immunisierungspegel": OntologyEntityType.IMMUNITY_LEVEL,
    "immunisierungsfeldstärke": OntologyEntityType.IMMUNITY_LEVEL,
    # Field strength — English
    "test level": OntologyEntityType.FIELD_STRENGTH,
    "field strength": OntologyEntityType.FIELD_STRENGTH,
    "level": OntologyEntityType.FIELD_STRENGTH,
    "immunity level": OntologyEntityType.IMMUNITY_LEVEL,
    # Frequency range — German
    "frequenzbereich": OntologyEntityType.FREQUENCY_RANGE,
    "frequenz": OntologyEntityType.FREQUENCY_RANGE,
    "frequenzband": OntologyEntityType.FREQUENCY_RANGE,
    "frequenzbereich (mhz)": OntologyEntityType.FREQUENCY_RANGE,
    # Frequency range — English
    "frequency range": OntologyEntityType.FREQUENCY_RANGE,
    "frequency": OntologyEntityType.FREQUENCY_RANGE,
    "freq. range": OntologyEntityType.FREQUENCY_RANGE,
    "freq range": OntologyEntityType.FREQUENCY_RANGE,
    # Emission limit — German
    "grenzwert": OntologyEntityType.EMISSION_LIMIT,
    "störaussendungsgrenzwert": OntologyEntityType.EMISSION_LIMIT,
    "grenzwerte": OntologyEntityType.EMISSION_LIMIT,
    "emissionsgrenzwert": OntologyEntityType.EMISSION_LIMIT,
    # Emission limit — English
    "limit": OntologyEntityType.EMISSION_LIMIT,
    "emission limit": OntologyEntityType.EMISSION_LIMIT,
    "limit class": OntologyEntityType.EMISSION_LIMIT,
    "limits": OntologyEntityType.EMISSION_LIMIT,
    # Acceptance criterion — German
    "anforderung": OntologyEntityType.ACCEPTANCE_CRITERION,
    "anforderungen": OntologyEntityType.ACCEPTANCE_CRITERION,
    "beurteilungskriterium": OntologyEntityType.ACCEPTANCE_CRITERION,
    "klasse": OntologyEntityType.ACCEPTANCE_CRITERION,
    "bewertungskriterium": OntologyEntityType.ACCEPTANCE_CRITERION,
    # Acceptance criterion — English
    "acceptance criterion": OntologyEntityType.ACCEPTANCE_CRITERION,
    "acceptance criteria": OntologyEntityType.ACCEPTANCE_CRITERION,
    "criterion": OntologyEntityType.ACCEPTANCE_CRITERION,
    "criteria": OntologyEntityType.ACCEPTANCE_CRITERION,
    "class": OntologyEntityType.ACCEPTANCE_CRITERION,
    "performance criterion": OntologyEntityType.ACCEPTANCE_CRITERION,
    # Phenomenon — German
    "phänomen": OntologyEntityType.PHENOMENON,
    "phaenomen": OntologyEntityType.PHENOMENON,
    "störphänomen": OntologyEntityType.PHENOMENON,
    "stoerphaenomen": OntologyEntityType.PHENOMENON,
    "prüfung": OntologyEntityType.PHENOMENON,
    # Phenomenon — English
    "phenomenon": OntologyEntityType.PHENOMENON,
    "test": OntologyEntityType.PHENOMENON,
    "test type": OntologyEntityType.PHENOMENON,
    "disturbance": OntologyEntityType.PHENOMENON,
    # Test method — German
    "prüfverfahren": OntologyEntityType.TEST_METHOD,
    "testmethode": OntologyEntityType.TEST_METHOD,
    "prüfmethode": OntologyEntityType.TEST_METHOD,
    "messverfahren": OntologyEntityType.TEST_METHOD,
    # Test method — English
    "test method": OntologyEntityType.TEST_METHOD,
    "method": OntologyEntityType.TEST_METHOD,
    "measurement method": OntologyEntityType.TEST_METHOD,
    # Normative term — German/English
    "normative requirement": OntologyEntityType.NORMATIVE_TERM,
    "anforderungsart": OntologyEntityType.NORMATIVE_TERM,
    "verbindlichkeit": OntologyEntityType.NORMATIVE_TERM,
    # TL 81000 specific — test severity / category
    "prüfschärfe": OntologyEntityType.IMMUNITY_LEVEL,
    "pruefschaerfe": OntologyEntityType.IMMUNITY_LEVEL,
    "prüfstufe": OntologyEntityType.IMMUNITY_LEVEL,
    "prufstufe": OntologyEntityType.IMMUNITY_LEVEL,
    "kategorie": OntologyEntityType.IMMUNITY_LEVEL,  # partial: kategorie 1/2/3
    # TL 81000 — impulse/transient parameters
    "impulstyp": OntologyEntityType.PHENOMENON,
    "impuls": OntologyEntityType.PHENOMENON,
    # TL 81000 — voltage columns
    "betriebsspannung": OntologyEntityType.FIELD_STRENGTH,
    "prüfspannung": OntologyEntityType.FIELD_STRENGTH,
    "pruefspannung": OntologyEntityType.FIELD_STRENGTH,
    "nennspannung": OntologyEntityType.FIELD_STRENGTH,
    "versorgungsspannung": OntologyEntityType.FIELD_STRENGTH,
    "u s in v": OntologyEntityType.FIELD_STRENGTH,
    "u in v": OntologyEntityType.FIELD_STRENGTH,
    "spannung in v": OntologyEntityType.FIELD_STRENGTH,
    "spannungspegel": OntologyEntityType.FIELD_STRENGTH,
    # TL 81000 — port / coupling
    "anschluss": OntologyEntityType.PHENOMENON,
    "schnittstelle": OntologyEntityType.PHENOMENON,
    "kopplung": OntologyEntityType.TEST_METHOD,
    "kopplungspfad": OntologyEntityType.TEST_METHOD,
}


def map_header(raw_header: str) -> OntologyEntityType | None:
    """Return the entity type for a raw column header, or None if not recognized."""
    normalized = raw_header.strip().lower()
    if not normalized:
        return None
    # Direct lookup
    result = HEADER_TO_ENTITY.get(normalized)
    if result is not None:
        return result
    # Partial match: check if any key is a substring of the header
    for key, entity in HEADER_TO_ENTITY.items():
        if key in normalized or normalized in key:
            return entity
    return None
