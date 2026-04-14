from grc_policy_server.models.schemas import (
    ActionItem,
    ComparisonResult,
    DocumentReference,
    KeyDifference,
)


def generate_mock_comparison(doc1, doc2) -> ComparisonResult:
    return ComparisonResult(
        summary=(
            f"Comprehensive comparison between {doc1.name} and {doc2.name} "
            "reveals significant updates in compliance requirements, risk "
            "assessment methodologies, and governance frameworks."
        ),
        keyDifferences=[
            KeyDifference(
                changeType="ADDED",
                section="Risk Assessment Framework",
                doc1Content="Manual risk scoring with quarterly reviews",
                doc2Content="AI-assisted continuous risk monitoring with real-time alerts",
                impact="High",
                doc1Reference=DocumentReference(
                    section="Section 3.2",
                    page=45,
                    lineStart=1245,
                    lineEnd=1252,
                    sourceText=(
                        "3.2 Risk Assessment Methodology\n\n"
                        "The organization shall conduct risk assessments on a quarterly "
                        "basis using the manual scoring framework outlined in Appendix C."
                    ),
                ),
                doc2Reference=DocumentReference(
                    section="Section 2.1",
                    page=23,
                    lineStart=678,
                    lineEnd=686,
                    sourceText=(
                        "2.1 AI-Driven Risk Assessment\n\n"
                        "The organization shall implement continuous risk monitoring "
                        "using AI-assisted tools that provide real-time risk detection."
                    ),
                ),
            ),
            KeyDifference(
                changeType="REMOVED",
                section="Data Privacy Requirements",
                doc1Content="GDPR compliance baseline",
                doc2Content="GDPR + AI Act compliance with enhanced data governance",
                impact="High",
                doc1Reference=DocumentReference(
                    section="Section 5.1",
                    page=78,
                    sourceText=(
                        "5.1 Data Privacy Framework\n\n"
                        "All data processing activities must comply with GDPR requirements."
                    ),
                ),
                doc2Reference=DocumentReference(
                    section="Section 4.3",
                    page=56,
                    sourceText=(
                        "4.3 Enhanced Data Privacy and AI Governance\n\n"
                        "Data processing must comply with both GDPR and the EU AI Act."
                    ),
                ),
            ),
            KeyDifference(
                changeType="MODIFIED",
                section="Audit Trail Requirements",
                doc1Content="12-month retention period",
                doc2Content="24-month retention with immutable logging",
                impact="Medium",
                doc1Reference=DocumentReference(
                    section="Section 7.4",
                    page=112,
                    sourceText=(
                        "7.4 Audit Trail Management\n\n"
                        "All system activities shall be logged and retained for a minimum "
                        "period of 12 months."
                    ),
                ),
                doc2Reference=DocumentReference(
                    section="Section 6.2",
                    page=89,
                    sourceText=(
                        "6.2 Immutable Audit Logging\n\n"
                        "Critical system activities must be captured in immutable audit logs."
                    ),
                ),
            ),
        ],
        actionPlan=[
            ActionItem(
                priority="Immediate",
                action="Implement AI Act compliance measures for data governance",
                timeline="30 days",
                owner="Compliance Team",
            ),
            ActionItem(
                priority="High",
                action="Upgrade audit trail system to support 24-month immutable logging",
                timeline="60 days",
                owner="IT Security",
            ),
            ActionItem(
                priority="Medium",
                action="Deploy AI-assisted risk monitoring tools",
                timeline="90 days",
                owner="Risk Management",
            ),
        ],
        followUpQuestions=[
            "What are the specific technical requirements for implementing immutable audit logging?",
            "How does the AI Act impact our current data processing agreements?",
            "What are the resource requirements for continuous risk monitoring implementation?",
            "Are there any grandfather clauses for existing systems in the 2025 guidelines?",
        ],
    )
