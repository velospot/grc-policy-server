import pytest

from grc_policy_server.models.schemas import DocumentReference, KeyDifference
from grc_policy_server.services.llm.ollama_client import OllamaClient


def test_compact_diffs_for_summary_concatenates_section_content():
    client = OllamaClient()
    diffs = [
        KeyDifference(
            changeType="MODIFIED",
            section="Access Control",
            doc1Content="Admins must use MFA.",
            doc2Content="Admins and vendors must use MFA.",
            impact="High",
            doc1Reference=DocumentReference(
                section="Access Control",
                page=2,
                sourceText="Admins must use MFA.",
            ),
            doc2Reference=DocumentReference(
                section="Access Control",
                page=2,
                sourceText="Admins and vendors must use MFA.",
            ),
        ),
        KeyDifference(
            changeType="MODIFIED",
            section="Access Control",
            doc1Content="Privileged accounts require manager approval.",
            doc2Content="Privileged accounts require security approval.",
            impact="High",
            doc1Reference=DocumentReference(
                section="Access Control",
                page=2,
                sourceText="Privileged accounts require manager approval.",
            ),
            doc2Reference=DocumentReference(
                section="Access Control",
                page=2,
                sourceText="Privileged accounts require security approval.",
            ),
        ),
    ]

    compact = client._compact_diffs_for_summary(diffs)

    assert len(compact) == 2
    assert "impact" not in compact[0]
    assert "Admins must use MFA." in compact[0]["doc1SectionContent"]
    assert (
        "Privileged accounts require manager approval."
        in compact[0]["doc1SectionContent"]
    )
    assert "Admins and vendors must use MFA." in compact[0]["doc2SectionContent"]
    assert (
        "Privileged accounts require security approval."
        in compact[0]["doc2SectionContent"]
    )


def test_prompt_summarize_changes_uses_detected_language():
    client = OllamaClient()
    prompt = client._prompt_summarize_changes(
        doc1_name="Dokument A",
        doc2_name="Dokument B",
        diffs=[],
        language="de",
    )

    assert "Sie sind Compliance-Analyst." in prompt
    assert "Dokument A: Dokument A" in prompt
    assert "Dokument B: Dokument B" in prompt
    assert "Zusammenfassung:" in prompt
    assert "Summary:" not in prompt


def test_prompt_summarize_changes_falls_back_to_english_for_unknown_language():
    client = OllamaClient()
    prompt = client._prompt_summarize_changes(
        doc1_name="Policy V1",
        doc2_name="Policy V2",
        diffs=[],
        language="unknown",
    )

    assert "You are a compliance analyst." in prompt
    assert "Document A: Policy V1" in prompt
    assert "Document B: Policy V2" in prompt
    assert "Summary:" in prompt
    assert "Zusammenfassung:" not in prompt
    assert "Résumé :" not in prompt


def test_prompt_summarize_changes_uses_canonical_diffs_json():
    client = OllamaClient()
    prompt = client._prompt_summarize_changes(
        doc1_name="A",
        doc2_name="B",
        diffs=[{"b": 2, "a": 1}],
        language="en",
    )

    assert 'Differences JSON:\n[{"a":1,"b":2}]' in prompt


@pytest.mark.anyio
async def test_detect_language_deterministic_german():
    client = OllamaClient()
    text = "Die Richtlinie legt fest, dass alle Benutzer MFA verwenden müssen."

    detected = await client.detect_language(text)

    assert detected == "de"


@pytest.mark.anyio
async def test_detect_language_deterministic_french():
    client = OllamaClient()
    text = "La politique exige que les utilisateurs doivent activer l'accès sécurisé."

    detected = await client.detect_language(text)

    assert detected == "fr"
