"""Report Generation Agent — AGENTS.md Agent 7.

Assembles the final structured audit report from a ComparisonResult.
No additional LLM calls — the executive summary is already in result.summary.
Report structure is template-based so it works reliably on local hardware.

Report sections (per AGENTS.md):
  1. Executive Summary
  2. Critical Findings (HIGH severity)
  3. Warnings (MEDIUM severity)
  4. Informational (LOW severity)
  5. Ignored Changes Panel — MANDATORY for audit trust
  6. Traceability Table — section, page, clause, doc references
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ReportAgent:
    """Assembles a structured markdown/JSON audit report from a ComparisonResult."""

    def assemble_markdown(
        self,
        result: Any,
        *,
        doc1_name: str,
        doc2_name: str,
    ) -> str:
        sections: list[str] = [
            "# Compliance Audit Report\n",
            f"**Document A:** {doc1_name}  \n**Document B:** {doc2_name}\n",
        ]

        if result.summary:
            sections.append(f"## Executive Summary\n\n{result.summary}\n")

        diffs = list(result.keyDifferences or [])
        high = [d for d in diffs if getattr(d, "changeSeverity", "") == "high"]
        medium = [d for d in diffs if getattr(d, "changeSeverity", "") == "medium"]
        low = [d for d in diffs if getattr(d, "changeSeverity", "") == "low"]

        if high:
            sections.append("## Critical Findings (HIGH Severity)\n")
            sections.append(_render_diff_table(high))
        if medium:
            sections.append("## Warnings (MEDIUM Severity)\n")
            sections.append(_render_diff_table(medium))
        if low:
            sections.append("## Informational (LOW Severity)\n")
            sections.append(_render_diff_table(low))

        # MANDATORY ignored-changes panel.
        hidden = getattr(result, "hiddenDiffsCount", 0) or 0
        sections.append("## Ignored Changes Panel\n")
        if hidden > 0:
            sections.append(
                f"**{hidden} low-severity change(s) were hidden** in simple mode. "
                f"Re-run with `auditMode=true` to see all changes.\n"
            )
        else:
            sections.append("No changes were hidden. All detected differences are shown above.\n")

        if diffs:
            sections.append("## Traceability Table\n")
            sections.append(_render_traceability_table(diffs))

        return "\n".join(sections)

    def assemble_json(
        self,
        result: Any,
        *,
        doc1_name: str,
        doc2_name: str,
    ) -> dict:
        diffs = list(result.keyDifferences or [])
        return {
            "doc1_name": doc1_name,
            "doc2_name": doc2_name,
            "summary": result.summary,
            "comparison_mode": result.comparisonMode,
            "requires_human_review": result.requireHumanReview,
            "hidden_diffs_count": getattr(result, "hiddenDiffsCount", 0) or 0,
            "total_diffs": len(diffs),
            "severity_counts": {
                "high": sum(1 for d in diffs if getattr(d, "changeSeverity", "") == "high"),
                "medium": sum(1 for d in diffs if getattr(d, "changeSeverity", "") == "medium"),
                "low": sum(1 for d in diffs if getattr(d, "changeSeverity", "") == "low"),
            },
            "findings": [_diff_to_dict(d) for d in diffs],
            "accuracy_metrics": (
                result.accuracyMetrics.model_dump() if result.accuracyMetrics else None
            ),
        }


def _render_diff_table(diffs: list) -> str:
    rows = ["| Section | Change | Severity | Human Review |\n",
            "|---------|--------|----------|--------------|\n"]
    for d in diffs:
        section = str(getattr(d, "section", "") or "").replace("|", "\\|")
        ct = str(getattr(d, "changeType", "") or "")
        sev = str(getattr(d, "changeSeverity", "") or "").upper()
        review = "⚠️ Yes" if getattr(d, "requiresHumanReview", False) else "No"
        rows.append(f"| {section} | {ct} | {sev} | {review} |\n")
    return "".join(rows) + "\n"


def _render_traceability_table(diffs: list) -> str:
    rows = ["| Section | Page | Node Type | Change | Doc A | Doc B |\n",
            "|---------|------|-----------|--------|-------|-------|\n"]
    for d in diffs:
        section = str(getattr(d, "section", "") or "").replace("|", "\\|")
        ref1 = getattr(d, "doc1Reference", None)
        ref2 = getattr(d, "doc2Reference", None)
        page = str(getattr(ref1 or ref2, "page", "") or "")
        nt = str(getattr(d, "nodeType", "") or "")
        ct = str(getattr(d, "changeType", "") or "")
        rows.append(
            f"| {section} | {page} | {nt} | {ct} | {_ref_str(ref1)} | {_ref_str(ref2)} |\n"
        )
    return "".join(rows) + "\n"


def _ref_str(ref: Any) -> str:
    if ref is None:
        return "—"
    section = str(getattr(ref, "section", "") or "").replace("|", "\\|")
    page = getattr(ref, "page", None)
    return (f"p.{page} " if page else "") + section[:30]


def _diff_to_dict(d: Any) -> dict:
    ref1 = getattr(d, "doc1Reference", None)
    ref2 = getattr(d, "doc2Reference", None)
    return {
        "change_type": getattr(d, "changeType", ""),
        "section": getattr(d, "section", ""),
        "severity": getattr(d, "changeSeverity", ""),
        "node_type": getattr(d, "nodeType", ""),
        "requires_human_review": getattr(d, "requiresHumanReview", False),
        "doc1_page": getattr(ref1, "page", None) if ref1 else None,
        "doc2_page": getattr(ref2, "page", None) if ref2 else None,
        "markdown_diff_summary": getattr(d, "markdownDiffSummary", None),
    }
