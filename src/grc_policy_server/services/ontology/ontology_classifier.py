"""Standalone ontology classification agent for GRC compliance nodes.

Classifies each `ParsedChunk` into the 10-type universal compliance ontology
defined in AGENTS.md (OntologyMappingAgent spec).

Architecture:
- Standalone service — does NOT depend on BaseLLM or any existing LLM client.
- Uses httpx directly against any OpenAI-compatible chat endpoint (Ollama, vLLM).
- Async batch processing with configurable concurrency semaphore.
- Graceful fallback: any error → type="Section", confidence=0.0, review_flag=True.

Per AGENTS.md:
  confidence < 0.70 → type must be "Section", review_flag=True (human review queue)
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

ONTOLOGY_TYPES: frozenset[str] = frozenset(
    {
        "Requirement",
        "Observation",
        "Measurement",
        "Threshold",
        "Control",
        "Evidence",
        "Deviation",
        "Risk",
        "Standard",
        "Section",
    }
)

CONFIDENCE_GATE = 0.70  # items below this go to human review queue

_SYSTEM_PROMPT = """\
You are a compliance document ontology classifier. Your task is to classify a \
document node into exactly one of the following 10 types:

  Requirement   — a normative obligation or prohibition (shall, must, doit, muss)
  Observation   — a documented finding or test result
  Measurement   — a quantitative measurement or test reading with a numeric value
  Threshold     — a limit, tolerance, or pass/fail boundary value
  Control       — a mitigating measure or corrective action
  Evidence      — supporting documentation, records, or proof
  Deviation     — a non-conformance, exception, or departure from a requirement
  Risk          — an identified risk or hazard
  Standard      — a reference to a normative standard, regulation, or specification
  Section       — structural or non-semantic content (headings, boilerplate, TOC)

Rules:
1. Return ONLY valid JSON — no prose, no explanation.
2. Set confidence to a float in [0.0, 1.0] reflecting your certainty.
3. If confidence < 0.70 you MUST set type to "Section".
4. For Measurement nodes include: frequency_hz, limit, measured, unit, result in properties.
5. For Deviation nodes include: requirement_id, observation_id in properties when identifiable.

Response format:
{"type": "<one of the 10 types>", "confidence": <float>, "properties": {<optional key-value>}}
"""


@dataclass
class OntologyClassification:
    ontology_type: str
    confidence: float
    review_flag: bool
    properties: dict = field(default_factory=dict)


_FALLBACK = OntologyClassification(
    ontology_type="Section",
    confidence=0.0,
    review_flag=True,
    properties={},
)


class OntologyClassifier:
    """Classify ParsedChunks into the 10-type universal compliance ontology.

    Uses any OpenAI-compatible chat endpoint (Ollama /api/chat or vLLM).
    All exceptions are caught per-node; the caller always gets a list of
    the same length as the input with `Section` as the safe fallback.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_sec: float = 30.0,
        semaphore_limit: int = 5,
        confidence_threshold: float = CONFIDENCE_GATE,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_sec = timeout_sec
        self._sem = asyncio.Semaphore(semaphore_limit)
        self._confidence_threshold = confidence_threshold

    async def classify_batch(
        self,
        chunks: list,  # list[ParsedChunk] — avoid direct import to keep standalone
        *,
        domain: str = "",
        doc_type: str = "",
    ) -> list[OntologyClassification]:
        """Classify all chunks concurrently, honouring the semaphore limit."""
        if not chunks:
            return []
        tasks = [
            self._classify_one(chunk, domain=domain, doc_type=doc_type)
            for chunk in chunks
        ]
        return list(await asyncio.gather(*tasks))

    async def _classify_one(
        self,
        chunk,  # ParsedChunk
        *,
        domain: str,
        doc_type: str,
    ) -> OntologyClassification:
        async with self._sem:
            try:
                return await self._call_and_parse(chunk, domain=domain, doc_type=doc_type)
            except Exception:
                logger.debug(
                    "ontology classification failed for chunk_type=%s — using Section fallback",
                    getattr(chunk, "chunk_type", "unknown"),
                    exc_info=True,
                )
                return _FALLBACK

    async def _call_and_parse(self, chunk, *, domain: str, doc_type: str) -> OntologyClassification:
        import httpx

        title = str(chunk.title or "").strip()
        content = str(chunk.text or "").strip()
        node_type = str(getattr(chunk, "chunk_type", "") or "")

        user_msg = (
            f"node_type: {node_type}\n"
            f"title: {title or '(none)'}\n"
            f"content: {content[:800]}\n"
            f"domain: {domain or 'general'}\n"
            f"doc_type: {doc_type or 'unknown'}"
        )

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.0,
            "max_tokens": 256,
        }

        async with httpx.AsyncClient(timeout=self._timeout_sec) as client:
            resp = await client.post(
                f"{self._base_url}/v1/chat/completions",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        raw_content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
        return self._parse_response(raw_content)

    def _parse_response(self, raw: str) -> OntologyClassification:
        # Strip markdown code fences if the LLM wrapped its JSON.
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(
                line for line in lines if not line.startswith("```")
            ).strip()

        parsed = json.loads(text)
        ont_type = str(parsed.get("type") or "Section").strip()
        confidence = float(parsed.get("confidence") or 0.0)
        properties = dict(parsed.get("properties") or {})

        # Enforce AGENTS.md confidence gate.
        if ont_type not in ONTOLOGY_TYPES:
            ont_type = "Section"
            confidence = 0.0
        if confidence < self._confidence_threshold:
            ont_type = "Section"

        return OntologyClassification(
            ontology_type=ont_type,
            confidence=confidence,
            review_flag=confidence < self._confidence_threshold,
            properties=properties,
        )
