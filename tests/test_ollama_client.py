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

    assert "Write the output in German (Deutsch)." in prompt
    assert "write the summary in English" not in prompt
    assert "Explain changes meaningfully (what changed), not impact/severity." in prompt
