"""Evidence Extraction Agent — AGENTS.md Agent 5.

Given a MODIFIED node pair (structured JSON only, no raw text), uses an
OpenAI-compatible LLM to extract only the compliance-relevant changes.

What to IGNORE:
  - Whitespace, punctuation, unicode normalisation
  - Reformatted tables with the same values
  - Reordered rows (same data, different order)
  - Header/footer repetition

What to REPORT:
  - Numeric value changes (limits, frequencies, voltages, margins)
  - Pass/fail status changes
  - Added or removed test cases
  - Changed standard/regulation references
  - New or removed normative requirements

Architecture:
  - Standalone service — does NOT depend on BaseLLM.
  - Uses httpx against any OpenAI-compatible endpoint.
  - Async batch with Semaphore; any exception → _NO_CHANGE fallback.
  - Gated by `evidence_extraction_enabled: bool = False` (opt-in).
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_CHANGE_TYPES = frozenset(
    {
        "measurement_failure",
        "limit_change",
        "setup_change",
        "requirement_change",
        "status_change",
    }
)

_SYSTEM_PROMPT = """\
You are a compliance document evidence extractor. You receive a pair of \
compliance document nodes (old version and new version) as structured JSON \
and must identify only the meaningful compliance-relevant changes.

IGNORE completely:
  - Whitespace, punctuation, unicode normalisation differences
  - Tables reformatted with the same values
  - Rows reordered with the same data
  - Header or footer repetition

REPORT only:
  - Numeric value changes (limits, frequencies, voltages, margins, dB values)
  - Pass/fail status changes
  - Added or removed test cases
  - Changed normative standard or regulation references (e.g. IEC 61000, CISPR)
  - New or removed normative requirements (shall, must, doit, muss)

Return ONLY valid JSON — no prose, no code fences.

Response format:
{
  "has_meaningful_change": true|false,
  "change_type": "<one of: measurement_failure|limit_change|setup_change|requirement_change|status_change|>",
  "severity": "<high|medium|low|>",
  "evidence": [
    {"field": "<field name>", "doc_a_value": "<old>", "doc_b_value": "<new>",
     "delta": "<optional computed delta>", "compliance_impact": "<brief impact>"}
  ]
}

If there is no meaningful change, set has_meaningful_change=false and leave
change_type, severity, and evidence empty.
"""


@dataclass
class EvidenceResult:
    has_meaningful_change: bool
    change_type: str = ""
    severity: str = ""
    evidence: list[dict] = field(default_factory=list)


_NO_CHANGE = EvidenceResult(has_meaningful_change=False)


class EvidenceExtractionAgent:
    """Extract meaningful compliance changes from MODIFIED node pairs.

    Input per pair: {"left": node_dict, "right": node_dict, "alignment": str}
    Only key fields are sent to the LLM — NOT raw document text.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_sec: float = 20.0,
        semaphore_limit: int = 5,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_sec = timeout_sec
        self._sem = asyncio.Semaphore(semaphore_limit)

    async def extract_batch(self, pairs: list[dict]) -> list[EvidenceResult]:
        """Extract evidence for all pairs concurrently."""
        if not pairs:
            return []
        tasks = [self._extract_one(pair) for pair in pairs]
        return list(await asyncio.gather(*tasks))

    async def _extract_one(self, pair: dict) -> EvidenceResult:
        async with self._sem:
            try:
                return await self._call_and_parse(pair)
            except Exception:
                logger.debug(
                    "evidence extraction failed for pair — using no-change fallback",
                    exc_info=True,
                )
                return _NO_CHANGE

    async def _call_and_parse(self, pair: dict) -> EvidenceResult:
        import httpx

        left = pair.get("left") or {}
        right = pair.get("right") or {}
        alignment = str(pair.get("alignment") or "semantic")

        # Send only structured metadata — no raw text blobs.
        user_msg = json.dumps(
            {
                "alignment": alignment,
                "node_a": _node_summary(left),
                "node_b": _node_summary(right),
            },
            ensure_ascii=False,
        )

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.0,
            "max_tokens": 512,
        }

        async with httpx.AsyncClient(timeout=self._timeout_sec) as client:
            resp = await client.post(
                f"{self._base_url}/v1/chat/completions",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        raw = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
        return self._parse_response(raw)

    def _parse_response(self, raw: str) -> EvidenceResult:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(line for line in lines if not line.startswith("```")).strip()

        parsed = json.loads(text)
        has_change = bool(parsed.get("has_meaningful_change", False))
        change_type = str(parsed.get("change_type") or "")
        if change_type not in _CHANGE_TYPES:
            change_type = ""
        severity = str(parsed.get("severity") or "")
        if severity not in ("high", "medium", "low"):
            severity = ""
        evidence = list(parsed.get("evidence") or [])

        return EvidenceResult(
            has_meaningful_change=has_change,
            change_type=change_type,
            severity=severity,
            evidence=evidence,
        )


def _node_summary(node: dict) -> dict:
    """Extract only the compliance-relevant structured fields from a node dict.

    Raw text blobs are excluded — the agent receives only typed metadata so
    it cannot hallucinate content that isn't in the structured data.
    """
    return {
        "ontology_type": node.get("ontology_type"),
        "node_type": node.get("node_type"),
        "section": node.get("section_path") or node.get("section") or "",
        "obligation": node.get("obligation") or "",
        "numeric_facts": node.get("numeric_changes") or [],
        "table_changes": (node.get("table_changes") or [])[:10],  # cap at 10
        "requirement_verb": node.get("requirement_verb_change"),
        "importance_label": node.get("importance_label") or "",
    }
