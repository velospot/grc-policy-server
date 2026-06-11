from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic import BaseModel, Field

from grc_policy_server.models.schemas import GraphChangeRecord, KeyDifference
from grc_policy_server.services.llm.base import BaseLLM
from grc_policy_server.utils.hashing import sha256_hex

logger = logging.getLogger(__name__)

_MAX_EXPLANATION_CHARS = 900
_MAX_MARKDOWN_CHARS = 1200


class GraphExplanationOutput(BaseModel):
    complianceExplanation: str = ""
    markdownDiffSummary: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reviewFlag: bool = True
    modelVersion: str = ""
    inputHash: str = ""
    outputHash: str = ""
    schemaVersion: str = "1.0"


class GraphExplanationAgent:
    """LLM explanation layer for graph-tree comparison.

    The agent explains existing graph changes only. It does not create findings,
    change severity, alter PASS/FAIL, or change extracted measurement values.
    On timeout/schema/LLM failure, callers keep the deterministic rationale.
    """

    def __init__(
        self,
        *,
        llm: BaseLLM,
        timeout_sec: float = 20.0,
        confidence_threshold: float = 0.70,
    ) -> None:
        self._llm = llm
        self._timeout_sec = timeout_sec
        self._confidence_threshold = confidence_threshold

    async def explain_key_difference(
        self,
        *,
        change: GraphChangeRecord,
        key_difference: KeyDifference,
        testing_department: str = "",
        language: str = "",
    ) -> KeyDifference:
        try:
            output = await asyncio.wait_for(
                self._explain(
                    change=change,
                    key_difference=key_difference,
                    testing_department=testing_department,
                    language=language,
                ),
                timeout=self._timeout_sec,
            )
        except Exception:
            logger.debug("graph explanation failed; using deterministic fallback", exc_info=True)
            return key_difference

        if output.confidence < self._confidence_threshold:
            key_difference.requiresHumanReview = True
        if output.complianceExplanation:
            key_difference.complianceExplanation = output.complianceExplanation
        if output.markdownDiffSummary:
            key_difference.markdownDiffSummary = output.markdownDiffSummary
        if output.reviewFlag:
            key_difference.requiresHumanReview = True
        return key_difference

    async def _explain(
        self,
        *,
        change: GraphChangeRecord,
        key_difference: KeyDifference,
        testing_department: str,
        language: str,
    ) -> GraphExplanationOutput:
        input_payload = {
            "change": change.model_dump(mode="json"),
            "key_difference": {
                "changeType": key_difference.changeType,
                "section": key_difference.section,
                "doc1Content": key_difference.doc1Content,
                "doc2Content": key_difference.doc2Content,
                "impact": key_difference.impact,
                "changeSeverity": key_difference.changeSeverity,
                "nodeType": key_difference.nodeType,
                "changes": [item.model_dump(mode="json") for item in key_difference.changes],
            },
            "testing_department": testing_department,
        }
        input_hash = sha256_hex(str(input_payload).encode("utf-8"))
        doc1_text = _prompt_text("OLD", key_difference.doc1Content, change.doc1Node)
        doc2_text = _prompt_text("NEW", key_difference.doc2Content, change.doc2Node)

        markdown = await self._llm.generate_markdown_diff_summary(
            node_type=key_difference.nodeType,
            change_type=key_difference.changeType,
            doc1_source_text=doc1_text,
            doc2_source_text=doc2_text,
            language=language,
            testing_department=testing_department or None,
        )
        markdown = _clean_text(markdown, _MAX_MARKDOWN_CHARS)
        explanation = _plain_explanation_from_markdown(markdown) or key_difference.complianceExplanation or key_difference.impact
        explanation = _clean_text(explanation, _MAX_EXPLANATION_CHARS)
        output_hash = sha256_hex(f"{explanation}\n{markdown}".encode("utf-8"))
        confidence = 0.82 if explanation and explanation != key_difference.impact else 0.72
        return GraphExplanationOutput(
            complianceExplanation=explanation,
            markdownDiffSummary=markdown or None,
            confidence=confidence,
            reviewFlag=bool(key_difference.requiresHumanReview),
            modelVersion=_model_version(self._llm),
            inputHash=input_hash,
            outputHash=output_hash,
        )


def _prompt_text(label: str, content: str | None, node: Any | None) -> str:
    node_props = getattr(node, "properties", None) or {}
    facts = []
    for key in ("ontologyType", "title", "sectionPath", "page"):
        value = getattr(node, key, None) if node is not None else None
        if value:
            facts.append(f"{key}: {value}")
    for key in ("fact_type", "name", "value", "unit", "clause", "standard_ref", "standard_version"):
        value = node_props.get(key)
        if value is not None and value != "":
            facts.append(f"{key}: {value}")
    return f"{label}\n" + "\n".join(facts + [f"content: {content or ''}"])


def _clean_text(text: str | None, max_chars: int) -> str:
    cleaned = " ".join(str(text or "").strip().split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rsplit(" ", 1)[0] + "…"


def _plain_explanation_from_markdown(markdown: str) -> str:
    text = markdown.replace("```diff", "").replace("```", "")
    text = text.replace("<span style=\"color:red\">", "").replace("<span style=\"color:green\">", "")
    text = text.replace("</span>", "").replace("~~", "").replace("**", "")
    lines = [line.strip("- >\t ") for line in text.splitlines() if line.strip()]
    return _clean_text(" ".join(lines), _MAX_EXPLANATION_CHARS)


def _model_version(llm: BaseLLM) -> str:
    settings = getattr(llm, "settings", None)
    return str(getattr(settings, "chat_model", "") or type(llm).__name__)

