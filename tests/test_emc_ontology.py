"""Tests for Phase C EMC ontology module.

Covers EMCTestClassifier, UnitNormalizer, NormalizedFactExtractor.
"""

from __future__ import annotations

import pytest

from grc_policy_server.services.ingestion.ontology.emc_ontology import (
    EMC_DOMAIN_ROW_KEYS,
    EMCTestClassifier,
    EMCTestType,
    NormalizedFactExtractor,
    OntologyEntityType,
    UnitNormalizer,
)


class TestEMCTestClassifier:
    clf = EMCTestClassifier()

    def test_classifies_radiated_immunity(self):
        result = self.clf.classify_table("Radiated Immunity", ["Phenomenon", "Frequency Range", "Level"])
        assert result == EMCTestType.RADIATED_IMMUNITY

    def test_classifies_conducted_emissions(self):
        result = self.clf.classify_table("Conducted Emissions", ["Frequency", "Limit"])
        assert result == EMCTestType.CONDUCTED_EMISSIONS

    def test_classifies_esd(self):
        result = self.clf.classify_table("ESD Requirements", ["Voltage", "Discharge Type"])
        assert result == EMCTestType.ESD

    def test_classifies_transient_immunity(self):
        result = self.clf.classify_table("Transient Immunity", ["Pulse Type", "Level"])
        assert result == EMCTestType.TRANSIENT_IMMUNITY

    def test_returns_unknown_for_unrecognised(self):
        result = self.clf.classify_table("General Info", ["Name", "Value"])
        assert result == EMCTestType.UNKNOWN

    def test_classify_from_section_path_radiated(self):
        result = self.clf.classify_from_section_path(["6.1 Strahlungsimmunität"])
        assert result == EMCTestType.RADIATED_IMMUNITY

    def test_classify_from_section_path_esd(self):
        result = self.clf.classify_from_section_path(["5.3 Electrostatic Discharge"])
        assert result == EMCTestType.ESD

    def test_german_radiated_immunity(self):
        result = self.clf.classify_table("Strahlungsimmunität", ["Frequenzbereich", "Prüfpegel"])
        assert result == EMCTestType.RADIATED_IMMUNITY


class TestUnitNormalizer:
    norm = UnitNormalizer()

    def test_normalize_khz(self):
        hz, unit = self.norm.normalize_frequency("150", "kHz")
        assert hz == 150_000.0
        assert unit == "Hz"

    def test_normalize_mhz(self):
        hz, unit = self.norm.normalize_frequency("30", "MHz")
        assert hz == 30_000_000.0
        assert unit == "Hz"

    def test_normalize_ghz(self):
        hz, unit = self.norm.normalize_frequency("1", "GHz")
        assert hz == 1_000_000_000.0
        assert unit == "Hz"

    def test_normalize_hz_unchanged(self):
        hz, unit = self.norm.normalize_frequency("50", "Hz")
        assert hz == 50.0
        assert unit == "Hz"

    def test_handles_comma_decimal(self):
        hz, unit = self.norm.normalize_frequency("2,4", "GHz")
        assert hz == 2_400_000_000.0

    def test_invalid_value_returns_zero(self):
        hz, unit = self.norm.normalize_frequency("N/A", "MHz")
        assert hz == 0.0

    def test_normalize_unit_dbuv(self):
        unit, family = self.norm.normalize_unit("dBuV")
        assert unit == "dBuV"
        assert family == "emission_limit"

    def test_normalize_unit_vm(self):
        unit, family = self.norm.normalize_unit("V/m")
        assert unit == "V/m"
        assert family == "field_strength"


class TestNormalizedFactExtractor:
    ext = NormalizedFactExtractor()

    def test_extract_frequency_range(self):
        facts = self.ext.extract_from_cell("150 kHz – 30 MHz", "frequency_range", "t001")
        freq_facts = [f for f in facts if f.fact_type == "frequency_range"]
        assert len(freq_facts) >= 1
        f = freq_facts[0]
        assert f.unit == "Hz"
        assert "150000" in f.value

    def test_extract_field_strength(self):
        facts = self.ext.extract_from_cell("30 V/m", "test_level", "t001")
        fs_facts = [f for f in facts if f.fact_type == "field_strength"]
        assert len(fs_facts) == 1
        assert fs_facts[0].value == "30"
        assert fs_facts[0].unit == "V/m"

    def test_extract_emission_limit_dbuv(self):
        facts = self.ext.extract_from_cell("46 dBuV", "limit", "t001")
        lim_facts = [f for f in facts if f.fact_type == "emission_limit"]
        assert len(lim_facts) == 1
        assert lim_facts[0].value == "46"
        assert lim_facts[0].unit == "dBuV"

    def test_extract_normative_term_shall(self):
        facts = self.ext.extract_from_cell("The device shall withstand", "requirement", "t001")
        nt_facts = [f for f in facts if f.fact_type == "normative_term"]
        assert len(nt_facts) == 1
        assert nt_facts[0].value == "shall"
        assert nt_facts[0].unit == "mandatory"

    def test_extract_normative_term_muss(self):
        facts = self.ext.extract_from_cell("Das Gerät muss bestehen", "anforderung", "t001")
        nt_facts = [f for f in facts if f.fact_type == "normative_term"]
        assert len(nt_facts) == 1
        assert nt_facts[0].unit == "mandatory"

    def test_extract_class_a(self):
        facts = self.ext.extract_from_cell("Class A performance", "criterion", "t001")
        cls_facts = [f for f in facts if f.fact_type == "acceptance_criterion"]
        assert len(cls_facts) == 1
        assert cls_facts[0].value == "class_a"

    def test_empty_cell_returns_empty(self):
        facts = self.ext.extract_from_cell("", "col", "t001")
        assert facts == []

    def test_whitespace_cell_returns_empty(self):
        facts = self.ext.extract_from_cell("   ", "col", "t001")
        assert facts == []

    def test_fact_ids_are_unique(self):
        facts = self.ext.extract_from_cell("30 V/m shall", "col", "t001")
        ids = [f.fact_id for f in facts]
        assert len(ids) == len(set(ids))

    def test_owner_object_id_is_set(self):
        facts = self.ext.extract_from_cell("100 V/m", "level", "TABLE-XYZ")
        assert all(f.owner_object_id == "TABLE-XYZ" for f in facts)


class TestEMCDomainRowKeys:
    def test_radiated_immunity_has_required_fields(self):
        keys = EMC_DOMAIN_ROW_KEYS[EMCTestType.RADIATED_IMMUNITY]
        assert "phenomenon" in keys
        assert "frequency_range" in keys
        assert "acceptance_criterion" in keys

    def test_esd_has_voltage_level(self):
        keys = EMC_DOMAIN_ROW_KEYS[EMCTestType.ESD]
        assert "voltage_level" in keys
        assert "discharge_type" in keys

    def test_conducted_emissions_has_detector(self):
        keys = EMC_DOMAIN_ROW_KEYS[EMCTestType.CONDUCTED_EMISSIONS]
        assert "detector" in keys

    def test_all_test_types_covered(self):
        for test_type in [
            EMCTestType.RADIATED_IMMUNITY,
            EMCTestType.CONDUCTED_EMISSIONS,
            EMCTestType.ESD,
            EMCTestType.TRANSIENT_IMMUNITY,
        ]:
            assert test_type in EMC_DOMAIN_ROW_KEYS
            assert len(EMC_DOMAIN_ROW_KEYS[test_type]) >= 3
