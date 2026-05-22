from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TestingDepartmentProfile:
    """Prompt guidance for department-specific compliance review."""

    department: str
    role: str
    focus: list[str]


_PROFILES: dict[str, TestingDepartmentProfile] = {
    "EMC": TestingDepartmentProfile(
        department="EMC",
        role=(
            "You are an EMC compliance tester/auditor (CEM/EMV), focused on test methods, "
            "limits, frequency ranges, test setups, and referenced standards (IEC/CISPR/ISO)."
        ),
        focus=[
            "numeric limits and levels (V/m, dBµV, dBµA, dBµV/m)",
            "frequency ranges and boundary values (kHz–GHz)",
            "acceptance criteria / performance criteria (A/B/C, Class A–E)",
            "test method or setup changes (dwell time, distances, antenna, cables)",
            "referenced standard/edition/year changes (IEC/CISPR/ISO)",
        ],
    ),
    "Safety": TestingDepartmentProfile(
        department="Safety",
        role=(
            "You are a product safety compliance tester/auditor, focused on safety requirements, "
            "hazards, protective measures, and verification testing."
        ),
        focus=[
            "safety limits/thresholds (temperature rise, leakage current, voltage, force/torque)",
            "protective measures (guards, interlocks, insulation, grounding, warnings)",
            "test method changes (test conditions, pass/fail criteria, instrumentation)",
            "scope/applicability or product class changes that alter safety obligations",
            "referenced standard/edition/year changes (IEC/ISO/EN/UL)",
        ],
    ),
    "Environment": TestingDepartmentProfile(
        department="Environment",
        role=(
            "You are an environmental testing compliance tester/auditor, focused on environmental "
            "conditions, durability, and test methods (climate, ingress, vibration, corrosion)."
        ),
        focus=[
            "test conditions (temperature, humidity, altitude, pressure, salt spray duration)",
            "durability / cycling requirements (cycles, dwell times, soak times)",
            "vibration/shock profiles and acceptance criteria",
            "sample preparation and conditioning requirements",
            "referenced standard/edition/year changes (IEC/ISO/EN)",
        ],
    ),
}


def normalize_testing_department(value: str | None) -> str:
    if not value:
        return "EMC"
    candidate = value.strip()
    if candidate in _PROFILES:
        return candidate
    # Accept a few common variants.
    upper = candidate.upper()
    if upper == "EMV":
        return "EMC"
    if upper in _PROFILES:
        return upper
    return "EMC"


def get_testing_department_profile(value: str | None) -> TestingDepartmentProfile:
    dept = normalize_testing_department(value)
    return _PROFILES[dept]

