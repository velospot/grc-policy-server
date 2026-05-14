"""Maps raw table column headers (German + English) to OntologyEntityType values.

Case-insensitive lookups. Covers automotive EMC standard column naming conventions
(TL 81000, CISPR 25, ISO 11452), IEC environmental testing (DIN EN 60068),
and maritime EMC requirements (DNV CG-0339).
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
    # TL 81000 — split-header fragments (hyphen-broken cell text from PDF extraction)
    "wert u in db": OntologyEntityType.EMISSION_LIMIT,
    "u in db": OntologyEntityType.EMISSION_LIMIT,
    "grenzwert u in db": OntologyEntityType.EMISSION_LIMIT,
    "(μv)": OntologyEntityType.EMISSION_LIMIT,
    "(uv)": OntologyEntityType.EMISSION_LIMIT,
    "(dbμv)": OntologyEntityType.EMISSION_LIMIT,
    "(dbuv)": OntologyEntityType.EMISSION_LIMIT,
    "bw f in khz": OntologyEntityType.FREQUENCY_RANGE,
    "f in mhz": OntologyEntityType.FREQUENCY_RANGE,
    "f in khz": OntologyEntityType.FREQUENCY_RANGE,
    "e in v/m": OntologyEntityType.FIELD_STRENGTH,
    "u in v": OntologyEntityType.FIELD_STRENGTH,
    "feld in v/m": OntologyEntityType.FIELD_STRENGTH,
    "messempfänger": OntologyEntityType.TEST_METHOD,
    "messempfaenger": OntologyEntityType.TEST_METHOD,
    "detektor": OntologyEntityType.TEST_METHOD,
    "detector": OntologyEntityType.TEST_METHOD,
    "messpunkt": OntologyEntityType.PHENOMENON,
    "messbereich": OntologyEntityType.FREQUENCY_RANGE,
    "bandbreite": OntologyEntityType.FREQUENCY_RANGE,
    "bandwidth": OntologyEntityType.FREQUENCY_RANGE,
    # -----------------------------------------------------------------------
    # DIN EN 60068 — IEC environmental testing (vibration, shock, climate)
    # -----------------------------------------------------------------------
    # Test duration — German / English
    "prüfdauer": OntologyEntityType.NUMERIC_LIMIT,
    "prüfdauer je achse": OntologyEntityType.NUMERIC_LIMIT,
    "test duration": OntologyEntityType.NUMERIC_LIMIT,
    "dauer": OntologyEntityType.NUMERIC_LIMIT,
    "duration": OntologyEntityType.NUMERIC_LIMIT,
    # Test severity / category
    "kat.": OntologyEntityType.IMMUNITY_LEVEL,
    "severity": OntologyEntityType.IMMUNITY_LEVEL,
    "schweregrad": OntologyEntityType.IMMUNITY_LEVEL,
    "prüfungsart": OntologyEntityType.PHENOMENON,
    # ASD / PSD columns — vibration spectral density
    "asd": OntologyEntityType.NUMERIC_LIMIT,
    "power spectral density": OntologyEntityType.NUMERIC_LIMIT,
    "psd": OntologyEntityType.NUMERIC_LIMIT,
    "spektrale beschleunigungsdichte": OntologyEntityType.NUMERIC_LIMIT,
    # ASD split-header cell fragments (e.g. "asd f 1", "asd fa, fb")
    "asd f 1": OntologyEntityType.NUMERIC_LIMIT,
    "asd f 2": OntologyEntityType.NUMERIC_LIMIT,
    "asd f a , f b": OntologyEntityType.NUMERIC_LIMIT,
    "asd fa, fb": OntologyEntityType.NUMERIC_LIMIT,
    # Frequency breakpoints used as column headers in DIN 60068-2-64 tables
    "f 1": OntologyEntityType.FREQUENCY_RANGE,
    "f 2": OntologyEntityType.FREQUENCY_RANGE,
    "f a": OntologyEntityType.FREQUENCY_RANGE,
    "f b": OntologyEntityType.FREQUENCY_RANGE,
    "f c": OntologyEntityType.FREQUENCY_RANGE,
    "f d": OntologyEntityType.FREQUENCY_RANGE,
    # Acceleration / displacement
    "beschleunigung": OntologyEntityType.FIELD_STRENGTH,
    "acceleration": OntologyEntityType.FIELD_STRENGTH,
    "a effektiv": OntologyEntityType.FIELD_STRENGTH,
    "a rms": OntologyEntityType.FIELD_STRENGTH,
    "displacement": OntologyEntityType.FIELD_STRENGTH,
    "auslenkung": OntologyEntityType.FIELD_STRENGTH,
    # Axes / count
    "achsen": OntologyEntityType.NUMERIC_LIMIT,
    "anzahl der achsen": OntologyEntityType.NUMERIC_LIMIT,
    "number of axes": OntologyEntityType.NUMERIC_LIMIT,
    # Description / specification
    "beschreibung": OntologyEntityType.PHENOMENON,
    "spezifikation": OntologyEntityType.NORMATIVE_TERM,
    "specification": OntologyEntityType.NORMATIVE_TERM,
    # -----------------------------------------------------------------------
    # DNV CG-0339 — maritime EMC classification requirements
    # -----------------------------------------------------------------------
    # Test parameters / disturbance type
    "parameters": OntologyEntityType.PHENOMENON,
    "parameter": OntologyEntityType.PHENOMENON,
    # Installation class / EMC class
    "installation class": OntologyEntityType.ACCEPTANCE_CRITERION,
    "emc class": OntologyEntityType.ACCEPTANCE_CRITERION,
    # Installation location / area
    "location": OntologyEntityType.PHENOMENON,
    "area": OntologyEntityType.PHENOMENON,
    # Test level columns
    "minimum test level": OntologyEntityType.IMMUNITY_LEVEL,
    "test levels": OntologyEntityType.IMMUNITY_LEVEL,
    "test voltage": OntologyEntityType.FIELD_STRENGTH,
    "rated supply voltage": OntologyEntityType.FIELD_STRENGTH,
    # Port types
    "enclosure port": OntologyEntityType.PHENOMENON,
    "power port": OntologyEntityType.PHENOMENON,
    "signal port": OntologyEntityType.PHENOMENON,
    "i/o port": OntologyEntityType.PHENOMENON,
    # Bandwidth / measurement
    "measuring bandwidth": OntologyEntityType.FREQUENCY_RANGE,
    "frequency sweep range": OntologyEntityType.FREQUENCY_RANGE,
    "frequency sweep range (hz)": OntologyEntityType.FREQUENCY_RANGE,
    # Emission limits
    "limits (quasi-peak)": OntologyEntityType.EMISSION_LIMIT,
    "limits quasi-peak": OntologyEntityType.EMISSION_LIMIT,
    "quasi-peak": OntologyEntityType.EMISSION_LIMIT,
    "average": OntologyEntityType.EMISSION_LIMIT,
    # -----------------------------------------------------------------------
    # Test / sequence number (row-index column) — German + English + generic
    # Index-only columns that carry no physical unit; mapped to TEST_NUMBER so
    # that column-unit inheritance does not synthesise spurious NormalizedFacts
    # for bare integer cells like "1", "2", "3".
    # -----------------------------------------------------------------------
    "prüf.nr": OntologyEntityType.TEST_NUMBER,
    "prüf nr": OntologyEntityType.TEST_NUMBER,
    "prüfnr": OntologyEntityType.TEST_NUMBER,
    "prüf-nr": OntologyEntityType.TEST_NUMBER,
    "prüfnummer": OntologyEntityType.TEST_NUMBER,
    "lfd.nr": OntologyEntityType.TEST_NUMBER,
    "lfd. nr.": OntologyEntityType.TEST_NUMBER,
    "lfd. nr": OntologyEntityType.TEST_NUMBER,
    "lfd-nr": OntologyEntityType.TEST_NUMBER,
    "lfdnr": OntologyEntityType.TEST_NUMBER,
    "test.nr": OntologyEntityType.TEST_NUMBER,
    "test nr": OntologyEntityType.TEST_NUMBER,
    "testnr": OntologyEntityType.TEST_NUMBER,
    "test-nr": OntologyEntityType.TEST_NUMBER,
    "testnummer": OntologyEntityType.TEST_NUMBER,
    "test number": OntologyEntityType.TEST_NUMBER,
    "no.": OntologyEntityType.TEST_NUMBER,
    "lfd. no.": OntologyEntityType.TEST_NUMBER,
    "nr.": OntologyEntityType.TEST_NUMBER,
    "nr": OntologyEntityType.TEST_NUMBER,
}

# Default canonical unit for each entity type (used for column-unit inheritance)
ENTITY_TYPE_DEFAULT_UNIT: dict[OntologyEntityType, str] = {
    OntologyEntityType.EMISSION_LIMIT: "dBuV",
    OntologyEntityType.FIELD_STRENGTH: "V/m",
    OntologyEntityType.FREQUENCY_RANGE: "Hz",
    OntologyEntityType.TEST_NUMBER: "",  # index column, no physical unit
}


_MIN_PARTIAL_MATCH_LEN = 5  # ignore fragments shorter than this in partial matching


def map_header(raw_header: str) -> OntologyEntityType | None:
    """Return the entity type for a raw column header, or None if not recognized."""
    normalized = raw_header.strip().lower()
    if not normalized:
        return None
    # Direct lookup
    result = HEADER_TO_ENTITY.get(normalized)
    if result is not None:
        return result
    # De-hyphenated direct lookup: "grenz-" → "grenz"
    if normalized.endswith("-"):
        de_hyph = normalized.rstrip("-").strip()
        result = HEADER_TO_ENTITY.get(de_hyph)
        if result is not None:
            return result
    # Partial match: only apply when both strings are long enough to avoid false
    # positives from unit fragments like "khz", "in", "bw" matching long keys.
    if len(normalized) >= _MIN_PARTIAL_MATCH_LEN:
        for key, entity in HEADER_TO_ENTITY.items():
            if len(key) >= _MIN_PARTIAL_MATCH_LEN and (key in normalized or normalized in key):
                return entity
    # De-hyphenated partial match (same length guard)
    if normalized.endswith("-"):
        de_hyph = normalized.rstrip("-").strip()
        if len(de_hyph) >= _MIN_PARTIAL_MATCH_LEN:
            for key, entity in HEADER_TO_ENTITY.items():
                if len(key) >= _MIN_PARTIAL_MATCH_LEN and (key in de_hyph or de_hyph in key):
                    return entity
    return None
