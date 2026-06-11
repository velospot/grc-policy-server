from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from grc_policy_server.services.comparison.policy_semantics import extract_clause_meaning
from grc_policy_server.services.ingestion.ontology.emc_ontology import (
    EMCTestClassifier,
    NormalizedFactExtractor as EMCFactExtractor,
)
from grc_policy_server.services.ingestion.ontology.environment_ontology import (
    EnvFactExtractor,
    EnvTestClassifier,
)
from grc_policy_server.services.ingestion.ontology.safety_ontology import (
    SafetyFactExtractor,
    SafetyTestClassifier,
)
from grc_policy_server.utils.hashing import normalize_text, sha256_hex, stable_uuid

GraphLayer = Literal["meta", "layout", "compliance"]

ONTOLOGY_VERSION = "1.2"

# LaTeX-to-readable substitution table for common compliance document formulas.
_LATEX_SUBS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\\frac\{([^}]+)\}\{([^}]+)\}"), r"(\1)/(\2)"),
    (re.compile(r"\\sqrt\{([^}]+)\}"), r"sqrt(\1)"),
    (re.compile(r"\\cdot"), "×"),
    (re.compile(r"\\leq"), "≤"),
    (re.compile(r"\\geq"), "≥"),
    (re.compile(r"\\pm"), "±"),
    (re.compile(r"\\times"), "×"),
    (re.compile(r"\\approx"), "≈"),
    (re.compile(r"\\neq"), "≠"),
    (re.compile(r"_\{([^}]+)\}"), r"_\1"),
    (re.compile(r"\^\{([^}]+)\}"), r"^\1"),
    (re.compile(r"\\mathrm\{([^}]+)\}"), r"\1"),
    (re.compile(r"\\text\{([^}]+)\}"), r"\1"),
    (re.compile(r"[{}\\]"), ""),
]


def _formula_to_readable(latex: str) -> str:
    """Convert common LaTeX patterns to plain-text for auditor display."""
    text = (latex or "").strip()
    for pattern, repl in _LATEX_SUBS:
        text = pattern.sub(repl, text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_row_semantic_keys(
    cells: list[dict[str, Any]],
    domain: str,
) -> dict[int, str]:
    """Return a map of row_index → row semantic key for EMC/safety table rows.

    Uses the first non-header cell in each row as the row identifier — e.g.
    "LF", "MF", "contact_discharge" — which is stable across document versions
    even when test limits change.  Combined with the domain prefix this gives
    keys like "emc:lf" or "safety:contact_discharge" that survive table restructuring.
    """
    if not cells:
        return {}
    row_first_cell: dict[int, str] = {}
    for cell in cells:
        row = int(cell.get("row") or 0)
        col = int(cell.get("col") or 0)
        if bool(cell.get("is_header", False)):
            continue
        if col == 0:
            text = str(cell.get("text") or "").strip()
            if text and row not in row_first_cell:
                row_first_cell[row] = re.sub(r"\s+", "_", text.lower())[:40]
    if not row_first_cell:
        return {}
    domain_prefix = domain.lower() if domain and domain != "General" else ""
    return {
        row: f"{domain_prefix}:{text}" if domain_prefix else text
        for row, text in row_first_cell.items()
    }
_STANDARD_RE = re.compile(
    r"\b(CISPR\s*25|CISPR\s*32|IEC\s*61000(?:[-\s]\d+)*|FCC\s*Part\s*15|"
    r"ISO\s*14001|ISO\s*45001|ISO\s*9001|ISO/IEC\s*17025|EN\s*55032|EN\s*55035)\b",
    re.IGNORECASE,
)
_CLAUSE_RE = re.compile(
    r"\b(?:clause|section|abschnitt|article|annex|appendix)?\s*"
    r"([A-Za-z]?\d+(?:[.\-]\d+){0,5}[A-Za-z]?)\b",
    re.IGNORECASE,
)


class DoclingGraphNode(BaseModel):
    node_id: str
    stable_id: str
    layer: GraphLayer
    label: str
    document_id: str
    source_node_id: str | None = None
    ontology_type: str | None = None
    title: str = ""
    text: str = ""
    language: str = ""
    page: int | None = None
    bbox_refs: list[dict[str, Any]] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)


class DoclingGraphEdge(BaseModel):
    from_node: str
    to_node: str
    rel_type: str
    source: str = "layerA"
    confidence: float = 1.0
    ontology_version: str = ONTOLOGY_VERSION
    model_version: str = ""
    review_flag: bool = False
    properties: dict[str, Any] = Field(default_factory=dict)


class DoclingGraphArtifact(BaseModel):
    document_id: str
    document_stable_id: str
    ontology_version: str = ONTOLOGY_VERSION
    nodes: list[DoclingGraphNode] = Field(default_factory=list)
    edges: list[DoclingGraphEdge] = Field(default_factory=list)
    ignored_nodes: list[dict[str, Any]] = Field(default_factory=list)


class DoclingGraphAdapter:
    """Convert normalized Docling hierarchy records into a tri-layer graph artifact.

    This follows the docling-graph pattern of validated Pydantic objects with
    stable IDs and explicit edge metadata, but stays dependency-free because
    upstream docling-graph currently advertises Python 3.10–3.12 support while
    this service targets Python 3.13.
    """

    def build_artifact(
        self,
        *,
        document_id: str,
        filename: str,
        document_stable_id: str,
        document_family: str,
        content_hash: str,
        nodes: list[dict[str, Any]],
        metadata: dict[str, Any],
        resolved_standards: dict[str, Any] | None = None,
    ) -> DoclingGraphArtifact:
        graph_nodes: list[DoclingGraphNode] = []
        edges: list[DoclingGraphEdge] = []
        ignored: list[dict[str, Any]] = []

        document_node_id = f"meta:document:{document_id}"
        graph_nodes.append(
            DoclingGraphNode(
                node_id=document_node_id,
                stable_id=document_stable_id or document_id,
                layer="meta",
                label="Document",
                document_id=document_id,
                title=filename,
                properties={
                    "filename": filename,
                    "document_family": document_family,
                    "content_hash": content_hash,
                    "metadata": metadata,
                },
            )
        )

        language = _language_from_nodes(nodes)
        if language:
            language_id = f"meta:language:{document_id}:{language}"
            graph_nodes.append(
                DoclingGraphNode(
                    node_id=language_id,
                    stable_id=stable_uuid(f"language::{language}"),
                    layer="meta",
                    label="Language",
                    document_id=document_id,
                    title=language,
                    language=language,
                )
            )
            edges.append(DoclingGraphEdge(from_node=document_node_id, to_node=language_id, rel_type="HAS_LANGUAGE"))

        page_node_ids: dict[int, str] = {}
        layout_node_ids: dict[str, str] = {}
        standard_node_ids: dict[str, str] = {}

        for record in nodes:
            source_node_id = str(record.get("node_id") or "")
            if not source_node_id:
                continue
            page = _coerce_int(record.get("page_number") or record.get("page"))
            if page is not None and page not in page_node_ids:
                page_id = f"layout:page:{document_id}:{page}"
                page_node_ids[page] = page_id
                graph_nodes.append(
                    DoclingGraphNode(
                        node_id=page_id,
                        stable_id=stable_uuid(f"{document_stable_id}::page::{page}"),
                        layer="layout",
                        label="Page",
                        document_id=document_id,
                        page=page,
                        properties={"page_number": page},
                    )
                )
                edges.append(DoclingGraphEdge(from_node=document_node_id, to_node=page_id, rel_type="CONTAINS"))

            layout = _layout_node_from_record(document_id=document_id, record=record)
            graph_nodes.append(layout)
            layout_node_ids[source_node_id] = layout.node_id
            edges.append(DoclingGraphEdge(from_node=document_node_id, to_node=layout.node_id, rel_type="CONTAINS"))
            if page is not None:
                edges.append(DoclingGraphEdge(from_node=page_node_ids[page], to_node=layout.node_id, rel_type="CONTAINS"))
            parent_id = str(record.get("parent_id") or "")
            if parent_id and parent_id in layout_node_ids:
                edges.append(DoclingGraphEdge(from_node=layout_node_ids[parent_id], to_node=layout.node_id, rel_type="HAS_CHILD"))

            if bool(record.get("excluded_from_index")):
                ignored.append(
                    {
                        "node_id": source_node_id,
                        "type": record.get("node_type") or "unknown",
                        "source_page": page,
                        "reason": record.get("exclusion_reason") or "excluded_from_compliance_graph",
                        "title": record.get("title"),
                    }
                )
                continue

            compliance_nodes, compliance_edges = _compliance_nodes_from_record(
                document_id=document_id,
                document_stable_id=document_stable_id,
                record=record,
                layout_node_id=layout.node_id,
                standard_node_ids=standard_node_ids,
                resolved_standards=resolved_standards or {},
            )
            graph_nodes.extend(compliance_nodes)
            edges.extend(compliance_edges)

        _resolved = resolved_standards or {}
        for standard_ref, node_id in standard_node_ids.items():
            resolution = _resolved.get(standard_ref) or _resolved.get(standard_ref.upper())
            std_version = getattr(resolution, "version", "unknown") if resolution else "unknown"
            std_review = getattr(resolution, "review_flag", True) if resolution else True
            std_confidence = getattr(resolution, "confidence", 0.9) if resolution else 0.9
            graph_nodes.append(
                DoclingGraphNode(
                    node_id=node_id,
                    stable_id=stable_uuid(f"standard::{standard_ref.lower()}"),
                    layer="compliance",
                    label="Standard",
                    document_id=document_id,
                    ontology_type="Standard",
                    title=standard_ref,
                    properties={
                        "standard_ref": standard_ref,
                        "standard_version": std_version,
                        "standard_confidence": std_confidence,
                        "review_flag": std_review,
                    },
                )
            )

        return DoclingGraphArtifact(
            document_id=document_id,
            document_stable_id=document_stable_id,
            nodes=graph_nodes,
            edges=_dedupe_edges(edges),
            ignored_nodes=ignored,
        )


def _layout_node_from_record(*, document_id: str, record: dict[str, Any]) -> DoclingGraphNode:
    node_type = str(record.get("node_type") or "clause")
    label = {
        "section": "Section",
        "clause": "Clause",
        "table": "Table",
        "figure": "Figure",
    }.get(node_type, "LayoutNode")
    metadata = dict(record.get("metadata") or {})
    return DoclingGraphNode(
        node_id=f"layout:{record.get('node_id')}",
        stable_id=str(record.get("stable_id") or record.get("node_id") or ""),
        layer="layout",
        label=label,
        document_id=document_id,
        source_node_id=str(record.get("node_id") or ""),
        title=str(record.get("title") or ""),
        text=str(record.get("text") or ""),
        language=str(metadata.get("detected_language") or ""),
        page=_coerce_int(record.get("page_number")),
        bbox_refs=list(metadata.get("bbox_refs") or record.get("bbox_refs") or []),
        properties={
            "node_type": node_type,
            "section_path": record.get("section_path") or "Unknown Section",
            "section_titles": record.get("section_titles") or [],
            "ordinal": record.get("ordinal") or 0,
            "docling_path": metadata.get("docling_path"),
            "source": record.get("source") or "docling",
            "excluded_from_index": bool(record.get("excluded_from_index")),
            "exclusion_reason": record.get("exclusion_reason"),
            "_original_text": str(record.get("text") or "").strip()[:800],
        },
    )


def _compliance_nodes_from_record(
    *,
    document_id: str,
    document_stable_id: str,
    record: dict[str, Any],
    layout_node_id: str,
    standard_node_ids: dict[str, str],
    resolved_standards: dict[str, Any] | None = None,
) -> tuple[list[DoclingGraphNode], list[DoclingGraphEdge]]:
    node_type = str(record.get("node_type") or "")
    if node_type not in {"section", "clause", "table"}:
        return [], []

    metadata = dict(record.get("metadata") or {})
    text = str(record.get("text") or "")
    clean_text = str(metadata.get("clean_text") or normalize_text(text))
    ontology_type = str(metadata.get("ontology_type") or "").strip()
    if not ontology_type:
        ontology_type = _deterministic_ontology_type(node_type=node_type, text=clean_text, metadata=metadata)
    if ontology_type == "Section" and node_type != "section":
        return [], []

    # Noise gate: skip trivially short fragments (e.g. "erforderlich optional", "A optional")
    # that would pollute the compliance graph and create meaningless diffs.
    # Measurement and Standard nodes are exempt (tables can have minimal text cells).
    _NOISE_EXEMPT = {"Standard", "Measurement"}
    if ontology_type not in _NOISE_EXEMPT and len(clean_text.split()) < 3:
        return [], []

    source_node_id = str(record.get("node_id") or "")
    compliance_id = f"compliance:{source_node_id}"
    properties = _base_compliance_properties(record, metadata)
    # Phase 3.2: store content_hash for fallback alignment in comparison
    content_hash_val = sha256_hex(normalize_text(clean_text)[:300].encode("utf-8"))
    properties["content_hash"] = content_hash_val
    # Fix A: store original (pre-normalization) text so auditors see proper casing/punctuation
    properties["_original_text"] = str(record.get("text") or "").strip()[:800]
    # Formula: store LaTeX and human-readable form when present
    formula_latex = str(metadata.get("formula_latex") or "").strip()
    if formula_latex:
        properties["formula_latex"] = formula_latex
        properties["formula_display"] = _formula_to_readable(formula_latex)

    graph_nodes = [
        DoclingGraphNode(
            node_id=compliance_id,
            stable_id=str(record.get("stable_id") or source_node_id),
            layer="compliance",
            label=ontology_type,
            document_id=document_id,
            source_node_id=source_node_id,
            ontology_type=ontology_type,
            title=str(record.get("title") or record.get("section_path") or ""),
            text=clean_text,
            language=str(metadata.get("detected_language") or ""),
            page=_coerce_int(record.get("page_number")),
            bbox_refs=list(metadata.get("bbox_refs") or []),
            properties=properties,
        )
    ]
    edges = [
        DoclingGraphEdge(from_node=compliance_id, to_node=layout_node_id, rel_type="SOURCED_FROM"),
    ]

    # Layer B: standard references (PART_OF for Requirements, REFERENCES_STANDARD otherwise)
    std_refs_found = _extract_standard_refs(f"{record.get('section_path') or ''}\n{text}")
    for standard_ref in std_refs_found:
        standard_node_id = standard_node_ids.setdefault(
            standard_ref,
            f"compliance:standard:{stable_uuid(f'{document_stable_id}::{standard_ref.lower()}')}",
        )
        edges.append(
            DoclingGraphEdge(
                from_node=compliance_id,
                to_node=standard_node_id,
                rel_type="PART_OF" if ontology_type == "Requirement" else "REFERENCES_STANDARD",
                source="layerB",
                confidence=0.9,
                review_flag=True,
                properties={"standard_ref": standard_ref},
            )
        )
        # Layer B: EVIDENCE_FOR — clause/section nodes that reference a known standard
        if ontology_type in {"Section", "Observation", "Evidence"} and standard_ref in standard_node_ids:
            edges.append(
                DoclingGraphEdge(
                    from_node=compliance_id,
                    to_node=standard_node_id,
                    rel_type="EVIDENCE_FOR",
                    source="layerB",
                    confidence=0.85,
                    review_flag=False,
                    properties={"standard_ref": standard_ref},
                )
            )

    if node_type == "table":
        fact_nodes, fact_edges = _fact_nodes_from_table(
            document_id=document_id,
            owner_node_id=compliance_id,
            source_node_id=source_node_id,
            metadata=metadata,
            text=text,
            table_title=str(record.get("title") or ""),
        )
        graph_nodes.extend(fact_nodes)
        edges.extend(fact_edges)

        # Layer B: HAS_LIMIT — link Measurement owner to any Threshold fact nodes
        if ontology_type == "Measurement":
            for fn in fact_nodes:
                if fn.ontology_type == "Threshold":
                    edges.append(
                        DoclingGraphEdge(
                            from_node=compliance_id,
                            to_node=fn.node_id,
                            rel_type="HAS_LIMIT",
                            source="layerB",
                            confidence=0.90,
                            review_flag=False,
                        )
                    )

    # Layer C: REFERENCES_CLAUSE — Requirement nodes mention other clause numbers in body
    if ontology_type == "Requirement":
        for clause_ref in _extract_clause_refs(clean_text):
            # Emit edge to a placeholder node; comparison engine resolves these cross-refs
            clause_target_id = f"compliance:clause_ref:{stable_uuid(f'{document_stable_id}::clause::{clause_ref}')}"
            edges.append(
                DoclingGraphEdge(
                    from_node=compliance_id,
                    to_node=clause_target_id,
                    rel_type="REFERENCES_CLAUSE",
                    source="layerC",
                    confidence=0.90,
                    review_flag=False,
                    properties={"clause_ref": clause_ref},
                )
            )

    return graph_nodes, edges


def _deterministic_ontology_type(*, node_type: str, text: str, metadata: dict[str, Any]) -> str:
    if node_type == "section":
        return "Section"
    if node_type == "table":
        table_type = str(metadata.get("table_type") or metadata.get("emc_test_type") or "")
        if table_type and table_type != "unknown":
            return "Measurement"
        if _has_measurement_fact(metadata):
            return "Measurement"
        return "Evidence"
    meaning = extract_clause_meaning(text)
    if meaning.obligation:
        return "Requirement"
    lowered = text.lower()
    if any(term in lowered for term in ("observation", "measured", "gemessen", "observé", "inspection finding")):
        return "Observation"
    if any(term in lowered for term in ("risk", "hazard", "gefahr", "risque")):
        return "Risk"
    return "Section"


def _base_compliance_properties(record: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    text = str(record.get("text") or "")
    meaning = extract_clause_meaning(str(metadata.get("clean_text") or text))
    return {
        "clause": _extract_clause(record.get("section_path") or text),
        "section_path": record.get("section_path") or "Unknown Section",
        "classification": {
            "method": "deterministic_keyword",
            "model_version": "",
            "confidence": float(metadata.get("ontology_confidence") or 0.85),
        },
        "obligation": meaning.obligation,
        "subject": meaning.subject,
        "action": meaning.action,
        "object": meaning.object,
        "condition": meaning.condition,
        "extraction_confidence": float(metadata.get("extraction_quality_score") or 1.0),
    }


def _fact_nodes_from_table(
    *,
    document_id: str,
    owner_node_id: str,
    source_node_id: str,
    metadata: dict[str, Any],
    text: str,
    table_title: str = "",
) -> tuple[list[DoclingGraphNode], list[DoclingGraphEdge]]:
    table_structure = metadata.get("table_structure") or {}
    cells = list(table_structure.get("cells") or [])
    headers = list(metadata.get("table_headers") or table_structure.get("headers") or [])
    caption = str(metadata.get("normalized_caption") or metadata.get("caption") or "")
    section_path = metadata.get("section_path") or []
    domain = _detect_table_domain(caption=caption, headers=headers, section_path=section_path)
    extractor = _fact_extractor_for_domain(domain)

    graph_nodes: list[DoclingGraphNode] = []
    edges: list[DoclingGraphEdge] = []
    if not cells and text:
        cells = [{"row": 0, "col": 0, "text": text, "is_header": False}]

    # Build row semantic keys: {row_idx → "emc:lf" / "safety:contact_discharge"}
    row_semantic_keys = _extract_row_semantic_keys(cells, domain)

    for cell in cells:
        if bool(cell.get("is_header")):
            continue
        col = _coerce_int(cell.get("col")) or 0
        header = str(headers[col]) if col < len(headers) else ""
        cell_text = str(cell.get("text") or "").strip()
        for fact in extractor.extract_from_cell(cell_text, header, source_node_id):
            fact_type = str(getattr(fact, "fact_type", "numeric") or "numeric")
            ontology_type = _ontology_type_for_fact(fact_type)
            fact_id = f"compliance:fact:{source_node_id}:{getattr(fact, 'fact_id', stable_uuid(cell_text))}"
            graph_nodes.append(
                DoclingGraphNode(
                    node_id=fact_id,
                    stable_id=stable_uuid(f"{source_node_id}::{fact_type}::{getattr(fact, 'raw_value', cell_text)}"),
                    layer="compliance",
                    label=ontology_type,
                    document_id=document_id,
                    source_node_id=source_node_id,
                    ontology_type=ontology_type,
                    title=str(getattr(fact, "name", fact_type)),
                    text=cell_text,
                    properties={
                        "domain": domain,
                        "fact_type": fact_type,
                        "name": getattr(fact, "name", fact_type),
                        "value": getattr(fact, "value", ""),
                        "unit": getattr(fact, "unit", ""),
                        "raw_value": getattr(fact, "raw_value", cell_text),
                        "confidence": float(getattr(fact, "confidence", 1.0) or 1.0),
                        "row": cell.get("row"),
                        "col": cell.get("col"),
                        "column_header": header,
                        "table_title": table_title,
                        "row_semantic_key": row_semantic_keys.get(int(cell.get("row") or 0), ""),
                    },
                )
            )
            edges.append(
                DoclingGraphEdge(
                    from_node=owner_node_id,
                    to_node=fact_id,
                    rel_type="HAS_FACT",
                    source="layerB",
                    confidence=float(getattr(fact, "confidence", 1.0) or 1.0),
                )
            )
    return graph_nodes, edges


def _detect_table_domain(*, caption: str, headers: list[str], section_path: list[str]) -> str:
    if EMCTestClassifier().classify_table(caption, headers).value != "unknown":
        return "EMC"
    if EMCTestClassifier().classify_from_section_path(section_path).value != "unknown":
        return "EMC"
    if SafetyTestClassifier().classify_table(caption, headers).value != "unknown":
        return "Safety"
    if SafetyTestClassifier().classify_from_section_path(section_path).value != "unknown":
        return "Safety"
    if EnvTestClassifier().classify_table(caption, headers).value != "unknown":
        return "Environment"
    if EnvTestClassifier().classify_from_section_path(section_path).value != "unknown":
        return "Environment"
    return "General"


def _fact_extractor_for_domain(domain: str) -> Any:
    if domain == "Safety":
        return SafetyFactExtractor()
    if domain == "Environment":
        return EnvFactExtractor()
    return EMCFactExtractor()


def _ontology_type_for_fact(fact_type: str) -> str:
    if fact_type in {"emission_limit", "acceptance_criterion"}:
        return "Threshold"
    if fact_type in {"frequency_range", "field_strength", "numeric"}:
        return "Measurement"
    if fact_type == "normative_term":
        return "Requirement"
    return "Evidence"


def _has_measurement_fact(metadata: dict[str, Any]) -> bool:
    table_structure = metadata.get("table_structure") or {}
    headers = " ".join(str(h) for h in (metadata.get("table_headers") or table_structure.get("headers") or []))
    return bool(re.search(r"\b(freq|frequency|mhz|khz|ghz|limit|dbuv|v/m|spannung|pegel|emission)\b", headers, re.IGNORECASE))


def _extract_standard_refs(text: str) -> list[str]:
    refs: list[str] = []
    for match in _STANDARD_RE.finditer(text or ""):
        ref = re.sub(r"\s+", " ", match.group(1).strip().upper())
        if ref not in refs:
            refs.append(ref)
    return refs


def _extract_clause(text: str) -> str:
    match = _CLAUSE_RE.search(str(text or ""))
    return match.group(1) if match else ""


def _extract_clause_refs(text: str) -> list[str]:
    """Extract all unique clause numbers referenced in body text (Layer C)."""
    refs: list[str] = []
    for match in _CLAUSE_RE.finditer(str(text or "")):
        ref = match.group(1).strip()
        if ref and ref not in refs and len(ref) >= 3:
            refs.append(ref)
    return refs[:10]  # cap to avoid edge explosion on verbose documents


def _language_from_nodes(nodes: list[dict[str, Any]]) -> str:
    for node in nodes:
        metadata = node.get("metadata") or {}
        language = str(metadata.get("detected_language") or "").strip()
        if language:
            return language
    return ""


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _dedupe_edges(edges: list[DoclingGraphEdge]) -> list[DoclingGraphEdge]:
    seen: set[tuple[str, str, str]] = set()
    result: list[DoclingGraphEdge] = []
    for edge in edges:
        key = (edge.from_node, edge.to_node, edge.rel_type)
        if key in seen:
            continue
        seen.add(key)
        result.append(edge)
    return result

