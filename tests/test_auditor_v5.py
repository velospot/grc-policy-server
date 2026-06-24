from grc_policy_server.models.schemas import (
    ActionItem,
    ComparisonResult,
    Document,
    DocumentReference,
    KeyDifference,
)
from grc_policy_server.services.comparison.auditor_v5 import shape_v5_auditor_result


def _doc(document_id: str, category: str = "EMC") -> Document:
    return Document(
        id=document_id,
        name=f"{document_id}.pdf",
        version="1.0",
        uploadDate="2026-06-16",
        size="1 KB",
        category=category,
    )


def test_shape_v5_auditor_result_preserves_schema_with_auditor_content():
    result = ComparisonResult(
        summary="LLM says the EMC limit changed from 40 to 46 dBµV/m and needs review.",
        keyDifferences=[
            KeyDifference(
                changeType="MODIFIED",
                section="8.4",
                doc1Content="Limit is 40 dBµV/m",
                doc2Content="Limit is 46 dBµV/m",
                impact="High",
                changeSeverity="high",
                doc1Reference=DocumentReference(
                    section="8.4",
                    page=73,
                    sourceText="Limit is 40 dBµV/m",
                ),
                doc2Reference=DocumentReference(
                    section="8.4",
                    page=74,
                    sourceText="Limit is 46 dBµV/m",
                ),
                nodeType="table",
            )
        ],
        actionPlan=[
            ActionItem(
                priority="Low",
                action="old",
                timeline="later",
                owner="n/a",
            )
        ],
        followUpQuestions=[],
        hiddenDiffsCount=3,
        suppressedDiffsCount=2,
    )

    shaped = shape_v5_auditor_result(
        result,
        doc1=_doc("doc-a"),
        doc2=_doc("doc-b"),
        testing_department="EMC",
    )

    assert shaped.comparisonMode == "auditor_grade"
    assert shaped.hiddenDiffsCount == 0
    assert shaped.suppressedDiffsCount == 0
    assert shaped.requireHumanReview is True
    assert "overallRisk=High" in shaped.summary
    assert "LLM change narrative:" in shaped.summary
    assert "40 to 46 dBµV/m" in shaped.summary
    assert "8.4 p.74" in shaped.summary
    assert "deterministicRiskScore" in shaped.summary
    assert shaped.actionPlan[0].owner == "Compliance Auditor"
    assert shaped.followUpQuestions
    assert shaped.keyDifferences[0].complianceExplanation
    assert any("v5_auditor_grade=" in warning for warning in shaped.warnings)
