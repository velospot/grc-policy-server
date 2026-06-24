from __future__ import annotations

from collections import Counter

from grc_policy_server.models.schemas import (
    ActionItem,
    ComparisonResult,
    Document,
    DocumentReference,
    KeyDifference,
)


def infer_testing_department(doc1: Document, doc2: Document) -> str:
    candidates = [doc1.category, doc2.category, doc1.name, doc2.name]
    for value in candidates:
        normalized = str(value or "").strip().lower()
        if normalized in {"emc", "emv"}:
            return "EMC"
        if normalized == "safety":
            return "Safety"
        if normalized in {"environment", "environmental"}:
            return "Environment"
    joined = " ".join(str(value or "").lower() for value in candidates)
    if any(token in joined for token in ("emc", "emv", "cispr", "emission")):
        return "EMC"
    if any(token in joined for token in ("safety", "iec", "hazard")):
        return "Safety"
    if any(token in joined for token in ("rohs", "reach", "environment")):
        return "Environment"
    return "EMC"


def shape_v5_auditor_result(
    result: ComparisonResult,
    *,
    doc1: Document,
    doc2: Document,
    testing_department: str,
) -> ComparisonResult:
    risk = roll_up_risk(result)
    risk_score = compute_risk_score(result)
    domain = _domain_for_department(testing_department)
    key_differences = [
        _enrich_difference(diff, risk=risk, testing_department=testing_department)
        for diff in result.keyDifferences
    ]
    shaped = result.model_copy(
        update={
            "summary": _auditor_summary(
                doc1=doc1,
                doc2=doc2,
                risk=risk,
                risk_score=risk_score,
                domain=domain,
                testing_department=testing_department,
                key_differences=key_differences,
                llm_summary=result.summary,
            ),
            "keyDifferences": key_differences,
            "actionPlan": _auditor_action_plan(
                key_differences,
                risk=risk,
                testing_department=testing_department,
            ),
            "followUpQuestions": _auditor_followups(
                key_differences,
                testing_department=testing_department,
            ),
            "comparisonMode": "auditor_grade",
            "requireHumanReview": risk in {"Critical", "High"}
            or any(diff.requiresHumanReview for diff in key_differences),
            "hiddenDiffsCount": 0,
            "suppressedDiffsCount": 0,
            "warnings": _warnings(
                existing=result.warnings,
                risk=risk,
                risk_score=risk_score,
                domain=domain,
                testing_department=testing_department,
            ),
        }
    )
    return shaped


def roll_up_risk(result: ComparisonResult) -> str:
    if any(_is_critical(diff) for diff in result.keyDifferences):
        return "Critical"
    if any(_is_high_risk(diff) for diff in result.keyDifferences):
        return "High"
    if any(_is_medium_risk(diff) for diff in result.keyDifferences):
        return "Medium"
    return "Low"


def compute_risk_score(result: ComparisonResult) -> float:
    total = max(len(result.keyDifferences), 1)
    high = sum(1 for diff in result.keyDifferences if _is_high_risk(diff))
    critical = sum(1 for diff in result.keyDifferences if _is_critical(diff))
    medium = sum(1 for diff in result.keyDifferences if _is_medium_risk(diff))
    tables = sum(1 for diff in result.keyDifferences if diff.nodeType == "table")
    score = (
        min(critical, 1) * 35.0
        + min(high / total, 1.0) * 35.0
        + min(medium / total, 1.0) * 20.0
        + min(tables / total, 1.0) * 10.0
    )
    return round(min(score, 100.0), 1)


def _enrich_difference(
    diff: KeyDifference,
    *,
    risk: str,
    testing_department: str,
) -> KeyDifference:
    if diff.complianceExplanation:
        return diff
    citation = _best_reference(diff)
    citation_text = _citation_text(citation)
    explanation = (
        f"{testing_department} deterministic comparison classified this "
        f"{diff.changeType.lower()} {diff.nodeType} change as {diff.impact or risk}. "
        f"Primary evidence: {citation_text}."
    )
    return diff.model_copy(update={"complianceExplanation": explanation})


def _auditor_summary(
    *,
    doc1: Document,
    doc2: Document,
    risk: str,
    risk_score: float,
    domain: str,
    testing_department: str,
    key_differences: list[KeyDifference],
    llm_summary: str = "",
) -> str:
    counts = Counter(diff.changeType for diff in key_differences)
    top_findings = "; ".join(
        f"{diff.changeType} in {diff.section} ({_citation_text(_best_reference(diff))})"
        for diff in key_differences[:5]
    )
    if not top_findings:
        top_findings = "No material changes detected in the extracted comparison set."
    narrative = (
        f"V5 auditor-grade comparison completed for {doc1.name} versus {doc2.name}. "
        f"Domain={domain}; testingDepartment={testing_department}; "
        f"overallRisk={risk}; deterministicRiskScore={risk_score}. "
        f"Surfaced differences: added={counts.get('ADDED', 0)}, "
        f"removed={counts.get('REMOVED', 0)}, modified={counts.get('MODIFIED', 0)}. "
        f"All detected differences are surfaced with no hidden or suppressed diff count. "
        f"Key evidence: {top_findings}"
    )
    llm_summary = str(llm_summary or "").strip()
    if llm_summary and llm_summary not in {"raw", narrative}:
        return f"{narrative} LLM change narrative: {llm_summary}"
    return narrative


def _auditor_action_plan(
    key_differences: list[KeyDifference],
    *,
    risk: str,
    testing_department: str,
) -> list[ActionItem]:
    if not key_differences:
        return []
    priority = "High" if risk in {"Critical", "High"} else risk
    actions = [
        ActionItem(
            priority=priority,
            action=(
                f"Review {len(key_differences)} surfaced {testing_department} "
                "comparison findings and verify each cited clause/table."
            ),
            timeline="Before audit sign-off",
            owner="Compliance Auditor",
        )
    ]
    table_changes = [diff for diff in key_differences if diff.nodeType == "table"]
    if table_changes:
        actions.append(
            ActionItem(
                priority="High" if risk in {"Critical", "High"} else "Medium",
                action=(
                    "Recheck changed limit, measured, margin, and result columns "
                    "against original test evidence."
                ),
                timeline="Before certification decision",
                owner="Test Lab Owner",
            )
        )
    return actions[:5]


def _auditor_followups(
    key_differences: list[KeyDifference],
    *,
    testing_department: str,
) -> list[str]:
    if not key_differences:
        return [
            f"Are there any {testing_department} clauses that were not extracted?",
            "Do the source documents have annexes or tables that require manual audit review?",
        ]
    sections = [diff.section for diff in key_differences[:3]]
    return [
        f"Do the cited changes in {', '.join(sections)} affect audit acceptance?",
        "Which changed requirements require updated evidence from the test lab?",
        "Do any changed table limits, measurements, or margins require retesting?",
        "Are all cited page and section references traceable to the uploaded source PDFs?",
    ]


def _warnings(
    *,
    existing: list[str],
    risk: str,
    risk_score: float,
    domain: str,
    testing_department: str,
) -> list[str]:
    marker = (
        "v5_auditor_grade="
        f"domain:{domain};testingDepartment:{testing_department};"
        f"overallRisk:{risk};riskScore:{risk_score};"
        "deterministic:true;llmDecision:false;hiddenDiffsCount:0;suppressedDiffsCount:0"
    )
    return [*existing, marker]


def _is_critical(diff: KeyDifference) -> bool:
    text = _diff_text(diff).lower()
    return (
        "critical" in str(diff.impact).lower()
        or "pass→fail" in text
        or "pass->fail" in text
        or "zero margin" in text
        or "negative margin" in text
    )


def _is_high_risk(diff: KeyDifference) -> bool:
    text = _diff_text(diff).lower()
    return (
        diff.changeSeverity == "high"
        or str(diff.impact).lower() == "high"
        or "limit" in text
        or "shall" in text
        or "must" in text
        or "margin" in text
    )


def _is_medium_risk(diff: KeyDifference) -> bool:
    return diff.changeSeverity == "medium" or str(diff.impact).lower() == "medium"


def _diff_text(diff: KeyDifference) -> str:
    return " ".join(
        str(value or "")
        for value in (
            diff.section,
            diff.doc1Content,
            diff.doc2Content,
            diff.markdownDiffSummary,
            diff.complianceExplanation,
        )
    )


def _best_reference(diff: KeyDifference) -> DocumentReference | None:
    return diff.doc2Reference or diff.doc1Reference


def _citation_text(reference: DocumentReference | None) -> str:
    if reference is None:
        return "no source citation available"
    page = f"p.{reference.page}" if reference.page else "page unknown"
    return f"{reference.section} {page}"


def _domain_for_department(testing_department: str) -> str:
    if testing_department == "Environment":
        return "Environmental"
    return testing_department
