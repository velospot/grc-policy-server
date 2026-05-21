from grc_policy_server.services.llm.testing_department import (
    get_testing_department_profile,
    normalize_testing_department,
)


def test_normalize_testing_department_defaults_to_emc():
    assert normalize_testing_department(None) == "EMC"
    assert normalize_testing_department("") == "EMC"
    assert normalize_testing_department("unknown") == "EMC"


def test_normalize_testing_department_accepts_emv_alias():
    assert normalize_testing_department("EMV") == "EMC"
    assert normalize_testing_department("emv") == "EMC"


def test_get_testing_department_profile_returns_expected_departments():
    assert get_testing_department_profile("EMC").department == "EMC"
    assert get_testing_department_profile("Safety").department == "Safety"
    assert get_testing_department_profile("Environment").department == "Environment"

