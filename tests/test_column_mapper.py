"""Tests for Phase C column_mapper — German and English header → entity type mappings."""

from __future__ import annotations

import pytest

from grc_policy_server.services.ingestion.ontology.column_mapper import map_header
from grc_policy_server.services.ingestion.ontology.emc_ontology import OntologyEntityType


class TestColumnMapper:
    def test_german_field_strength(self):
        assert map_header("Prüfpegel") == OntologyEntityType.FIELD_STRENGTH
        assert map_header("Feldstärke") == OntologyEntityType.FIELD_STRENGTH

    def test_english_field_strength(self):
        assert map_header("Test Level") == OntologyEntityType.FIELD_STRENGTH
        assert map_header("Field Strength") == OntologyEntityType.FIELD_STRENGTH

    def test_german_frequency_range(self):
        assert map_header("Frequenzbereich") == OntologyEntityType.FREQUENCY_RANGE

    def test_english_frequency_range(self):
        assert map_header("Frequency Range") == OntologyEntityType.FREQUENCY_RANGE
        assert map_header("Frequency") == OntologyEntityType.FREQUENCY_RANGE

    def test_german_emission_limit(self):
        assert map_header("Grenzwert") == OntologyEntityType.EMISSION_LIMIT
        assert map_header("Grenzwerte") == OntologyEntityType.EMISSION_LIMIT

    def test_english_emission_limit(self):
        assert map_header("Limit") == OntologyEntityType.EMISSION_LIMIT
        assert map_header("Emission Limit") == OntologyEntityType.EMISSION_LIMIT

    def test_german_acceptance_criterion(self):
        assert map_header("Anforderung") == OntologyEntityType.ACCEPTANCE_CRITERION
        assert map_header("Klasse") == OntologyEntityType.ACCEPTANCE_CRITERION

    def test_english_acceptance_criterion(self):
        assert map_header("Acceptance Criterion") == OntologyEntityType.ACCEPTANCE_CRITERION
        assert map_header("Class") == OntologyEntityType.ACCEPTANCE_CRITERION

    def test_german_phenomenon(self):
        assert map_header("Phänomen") == OntologyEntityType.PHENOMENON

    def test_english_phenomenon(self):
        assert map_header("Phenomenon") == OntologyEntityType.PHENOMENON

    def test_test_method_english(self):
        assert map_header("Test Method") == OntologyEntityType.TEST_METHOD

    def test_case_insensitive(self):
        assert map_header("PRÜFPEGEL") == OntologyEntityType.FIELD_STRENGTH
        assert map_header("frequency range") == OntologyEntityType.FREQUENCY_RANGE

    def test_unknown_header_returns_none(self):
        assert map_header("SomeRandomColumnXYZ123") is None

    def test_empty_header_returns_none(self):
        assert map_header("") is None
