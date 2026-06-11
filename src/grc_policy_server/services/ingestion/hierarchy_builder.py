from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import re
from typing import Iterable

from grc_policy_server.services.comparison.policy_semantics import extract_clause_meaning
from grc_policy_server.services.ingestion.hierarchy_models import (
    DocumentHierarchy,
    HierarchyNode,
    ParsedChunk,
)
from grc_policy_server.utils.hashing import normalize_text, pure_text_hash as _pure_text_hash, sha256_hex, slugify_text, stable_uuid

_TOC_TITLES = {
    "contents",
    "table of contents",
    "toc",
    "index",
    "document index",
}

# Non-compliance section detection — expanded beyond TOC to cover all editorial/metadata
# sections that do not contain testable compliance requirements.  Applied at ingestion time
# so these nodes are never indexed and never surface in comparison output.
_NON_COMPLIANCE_RE = re.compile(
    r"""(?xi)
    (?:
        # Forewords and prefaces (EN/DE/FR/fused)
        \b(?:vorwort|nationalesvorwort|nationaler\s+vorwort|national\s+foreword
            |europaisches\s+vorwort|europ[aä]isches\s+vorwort|foreword|preface
            |avant.propos|danksagung|acknowledgements?)\b
        # Copyright / reproduction notices (including OCR-fused variants)
        | \bvervielf[aä]ltigung\b
        | vervielfaltigung.auchfur          # fused OCR: "Vervielfaltigung-auchfur..."
        | \bnachdruck\b | \bcopyright\b | \burheberrecht\b | \bimpressum\b
        | \breproduction\b | \bintellectual\s+property\b
        | \blegal\s+notice\b | \bdisclaimer\b
        # Effective date / application date
        | \banwendungsbeginn\b | \beffective\s+date\b | \bdate\s+of\s+application\b
        | \bg[uü]ltigkeitsbeginn\b
        # Amendment / revision / previous editions
        | \b[aä]nderungen\b | \b[aä]nderungsverzeichnis\b
        | \bfr[uü]here\s+ausgaben\b | \bfruhereausgaben\b  # fused OCR
        | \bamendment\b | \brevision\s+history\b | \bchange\s+log\b
        | \brevisionshistorie\b | \bdokument\s+history\b | \bchange\s+record\b
        # Relationship sections and cross-reference metadata
        | \bzusammenhang\s+mit\b | zusammenhangmit   # fused OCR
        | \brelationship\s+with\b
        | \bnormative\s+verweisungen\b
        # European/international standard metadata headers
        | \beurop[aä]ische\s+norm\b | \beuropean\s+standard\b
        | \bnorme\s+europ[eé]enne\b
        # TOC / figure lists (in addition to _TOC_TITLES)
        | \binhaltsverzeichnis\b | \babbildungsverzeichnis\b | \btabellenverzeichnis\b
        | \btable\s+of\s+contents\b | \blist\s+of\s+(?:figures|tables)\b
        # Blank pages
        | \bblank\s+page\b | \bintentionally\s+left\s+blank\b
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Terms to match after stripping all non-alphanumeric characters (catches fused OCR text)
_NON_COMPLIANCE_FUSED_TERMS = frozenset({
    "nationalesvorwort",
    "europaischesvorwort",
    "europaischenorm",
    "vervielfaltigung",
    "anwendungsbeginn",
    "fruhereausgaben",
    "anderungen",
    "revisionshistorie",
    "zusammenhangmit",
    "anderungsverzeichnis",
})

_TOC_LINE_RE = re.compile(
    r"^\s*(?:[A-Za-z0-9][A-Za-z0-9 .,'()\-/]{2,}?)(?:\.{2,}|\s{2,})(?:[A-Za-z]?\d+[A-Za-z]?)\s*$"
)
_CLAUSE_MARKER_RE = re.compile(
    r"^\s*((?:section|clause|article|appendix|annex)?\s*[A-Za-z]?\d+(?:\.\d+)*[A-Za-z]?)\b",
    re.IGNORECASE,
)
_VERSION_SUFFIX_RE = re.compile(
    r"(?i)(?:[-_ ](?:v(?:ersion)?[-_ ]?)?\d+(?:\.\d+)*)$"
)
_MAX_SECTION_TEXT = 5000

# Section number hierarchy expansion -------------------------------------------
# Matches titles that start with a multi-level dotted numeric prefix:
#   "5.1.2.2 Durchfuhrung" → prefix="5.1.2.2", rest="Durchfuhrung"
#   "10.4"                 → prefix="10.4",     rest=""
_SEC_NUM_PREFIX_RE = re.compile(r"^(\d+(?:\.\d+)+)\s*(.*)")
# Detects OCR fusion where a number and a word are run together without a space:
#   "5.1.3.2Durchfuhrung" → "5.1.3.2 Durchfuhrung"
_SEC_FUSION_RE = re.compile(r"(\d+(?:\.\d+)+)([A-Za-zÄÖÜäöüß])")
# Label fused with number, then letter: "Table16Minimum" → "Table 16 Minimum"
_LABEL_NUM_FUSION_RE = re.compile(
    r"\b(Tabellen?|Table|Figure|Figur|Bild|Abbildung|Annexe?|Appendix|Abschnitt|Section)\s*"
    r"(\d+(?:\.\d+)*)([A-Za-zÄÖÜäöüß])",
    re.IGNORECASE,
)
# Label fused with number then dash-separator: "Tabelle42-Prufung" → "Tabelle 42 Prufung"
_LABEL_DASH_RE = re.compile(
    r"\b(Tabellen?|Table|Figure|Figur|Bild|Abbildung|Annexe?|Appendix|Abschnitt|Section)\s*"
    r"(\d+(?:\.\d+)*)\s*[-–]\s*([A-Za-zÄÖÜäöüß])",
    re.IGNORECASE,
)
# Digit immediately followed by uppercase letter (after label fix): "16Min" → "16 Min"
_DIGIT_UPPER_RE = re.compile(r"(\d)([A-ZÄÖÜ])")
# Lowercase-to-uppercase word boundary (CamelCase): "PrufungNummer" → "Prüfung Nummer"
# Does NOT split all-caps tokens like "EUT", "EMV" because they have no preceding lowercase.
_CAMEL_BOUNDARY_RE = re.compile(r"([a-zäöüß])([A-ZÄÖÜ])")

# Header/footer suppression -----------------------------------------------
# Patterns that identify page headers and footers to exclude from hierarchy.
_PAGE_NUMBER_ONLY_RE = re.compile(r"^\s*(?:seite\s+)?\d+(?:\s+von\s+\d+)?\s*$", re.IGNORECASE)
_CONFIDENTIALITY_FRAGMENTS = (
    "volkswagen ag vertraulich",
    "vw ag confidential",
    "confidential",
    "vertraulich",
    "nur für internen gebrauch",
    "internal use only",
    "company confidential",
    "proprietary",
)


def _is_header_footer_chunk(chunk: "ParsedChunk") -> bool:
    """Return True if *chunk* looks like a repeating page header or footer."""
    text = (chunk.text or "").strip()
    if not text or len(text) > 200:
        return False
    if _PAGE_NUMBER_ONLY_RE.fullmatch(text):
        return True
    text_lower = text.lower()
    return any(frag in text_lower for frag in _CONFIDENTIALITY_FRAGMENTS)


def filter_header_footer_chunks(chunks: Iterable["ParsedChunk"]) -> list["ParsedChunk"]:
    """Remove page header/footer chunks from *chunks*.

    Operates in two passes:
    1. Suppress obvious single-line confidentiality stamps and page number lines.
    2. Suppress short text chunks (≤120 chars) that appear on ≥40% of pages —
       these are repeating headers/footers even if the text is less obvious.
    """
    chunk_list = list(chunks)
    if not chunk_list:
        return chunk_list

    # Pass 1: pattern-based suppression
    pass1 = [c for c in chunk_list if not _is_header_footer_chunk(c)]

    # Pass 2: frequency-based suppression for short text on many pages
    pages = {c.page_number for c in pass1 if c.page_number is not None}
    total_pages = max(len(pages), 1)
    threshold = max(2, int(total_pages * 0.4))

    text_page_map: dict[str, set[int]] = {}
    for c in pass1:
        txt = (c.text or "").strip()
        if 0 < len(txt) <= 120 and c.page_number is not None:
            text_page_map.setdefault(txt, set()).add(c.page_number)

    frequent_texts = {txt for txt, pg_set in text_page_map.items() if len(pg_set) >= threshold}

    return [
        c for c in pass1
        if not ((c.text or "").strip() in frequent_texts)
    ]
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_SUMMARY_OBLIGATION_RE = re.compile(
    r"\b(shall|must|required|should|may|muss|müssen|soll|sollen|"
    r"doit|doivent|devrait|peut|obligatoire|requis|exig[eé])\b",
    re.IGNORECASE,
)
_SUMMARY_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
_SUMMARY_DURATION_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(day|days|week|weeks|month|months|year|years|"
    r"hour|hours|minute|minutes|second|seconds|%|percent)\b",
    re.IGNORECASE,
)


_STD_REF_IN_TITLE_RE = re.compile(
    r"\b(CISPR|IEC|ISO|EN|FCC)\s*[\d/]+",
    re.IGNORECASE,
)


def _stable_id_for_section(
    title: str,
    section_path: tuple[str, ...] | list[str],
    doc_family: str,
) -> str:
    """Compute a stable_id for a section node using a priority fallback chain.

    Priority 1 (most stable): standard_id + clause number extracted from title.
    Priority 2: doc_family + numeric section prefix (e.g. "5.1.2") + title slug.
    Priority 3: doc_family + slug chain (existing behaviour).
    """
    # Priority 1: title contains a standard reference AND a clause number
    std_match = _STD_REF_IN_TITLE_RE.search(title or "")
    clause_match = _CLAUSE_MARKER_RE.match(title or "")
    if std_match and clause_match:
        std_slug = re.sub(r"\s+", "_", std_match.group(0).strip().lower())
        clause = clause_match.group(1).strip()
        return stable_uuid(f"section::{std_slug}::{clause}")

    # Priority 2: title starts with a numeric prefix (e.g. "5.1 Scope")
    num_match = _SEC_NUM_PREFIX_RE.match(title or "")
    if num_match:
        numeric_prefix = num_match.group(1)
        title_slug = slugify_text(num_match.group(2) or "") or "section"
        return stable_uuid(f"section::{doc_family}::{numeric_prefix}::{title_slug}")

    # Priority 3: slug chain (original behaviour)
    stable_key = "/".join(slugify_text(part) or "section" for part in section_path)
    return stable_uuid(f"section::{doc_family}::{stable_key}")


def build_document_hierarchy(
    *,
    document_id: str,
    filename: str,
    parsed_chunks: Iterable[ParsedChunk],
    content_hash: str,
) -> DocumentHierarchy:
    doc_family = document_family_from_filename(filename)
    document_stable_id = stable_uuid(f"document::{doc_family}")

    nodes: list[HierarchyNode] = []
    section_nodes: dict[tuple[str, ...], HierarchyNode] = {}
    section_buffers: dict[tuple[str, ...], list[str]] = defaultdict(list)
    section_exclusions: dict[tuple[str, ...], str] = {}
    section_leaf_ordinals: dict[tuple[str, ...], int] = defaultdict(int)
    section_ordinal = 0

    filtered_chunks = filter_header_footer_chunks(parsed_chunks)
    for chunk in sorted(filtered_chunks, key=lambda item: (item.page_number or 0, item.ordinal)):
        section_titles = _normalize_section_titles(chunk)
        section_path = " / ".join(section_titles) if section_titles else "Unknown Section"
        inherited_reason = _get_inherited_exclusion(section_titles, section_exclusions)
        section_lineage_ids = [document_id]

        if section_titles:
            parent_id = document_id
            lineage: list[str] = []
            for depth, title in enumerate(section_titles, start=1):
                current_path = section_titles[:depth]
                current_reason = section_exclusions.get(current_path)
                if current_reason is None:
                    current_reason = _classify_toc_block(
                        title=title,
                        section_titles=current_path,
                        text="" if depth != len(section_titles) else chunk.text,
                        labels=chunk.labels,
                    )
                    if current_reason is not None:
                        section_exclusions[current_path] = current_reason

                node = section_nodes.get(current_path)
                if node is None:
                    section_ordinal += 1
                    stable_id = _stable_id_for_section(title, current_path, doc_family)
                    node_id = stable_uuid(f"{document_id}::section::{stable_id}")
                    node = HierarchyNode(
                        node_id=node_id,
                        stable_id=stable_id,
                        content_hash="",
                        document_id=document_id,
                        document_stable_id=document_stable_id,
                        node_type="section",
                        parent_id=parent_id,
                        title=title,
                        text="",
                        section_path=" / ".join(current_path),
                        section_titles=list(current_path),
                        page_number=chunk.page_number,
                        ordinal=section_ordinal,
                        indexable=False,
                        excluded_from_index=current_reason is not None,
                        exclusion_reason=current_reason,
                        source=chunk.source,
                        lineage=lineage.copy(),
                        lineage_ids=section_lineage_ids.copy(),
                        metadata={
                            "anchor_text": title,
                            "source_labels": list(chunk.labels),
                            "source_refs": list(chunk.source_refs),
                        },
                    )
                    section_nodes[current_path] = node
                    nodes.append(node)
                else:
                    if node.page_number is None:
                        node.page_number = chunk.page_number
                    if current_reason and not node.excluded_from_index:
                        node.excluded_from_index = True
                        node.exclusion_reason = current_reason

                parent_id = node.node_id
                lineage.append(title)
                section_lineage_ids.append(node.node_id)

            leaf_reason = _classify_toc_block(
                title=chunk.title or section_titles[-1],
                section_titles=section_titles,
                text=chunk.text,
                labels=chunk.labels,
            )
            if leaf_reason is not None:
                section_exclusions[section_titles] = leaf_reason
                section_nodes[section_titles].excluded_from_index = True
                section_nodes[section_titles].exclusion_reason = leaf_reason
                inherited_reason = leaf_reason
            elif inherited_reason is None:
                inherited_reason = _get_inherited_exclusion(section_titles, section_exclusions)

        if chunk.chunk_type == "heading":
            continue

        if not section_titles:
            section_titles = ("Unsectioned",)
            section_path = " / ".join(section_titles)
            inherited_reason = _classify_toc_block(
                title=chunk.title,
                section_titles=section_titles,
                text=chunk.text,
                labels=chunk.labels,
            )
            if section_titles not in section_nodes:
                section_ordinal += 1
                stable_id = stable_uuid(f"section::{doc_family}::unsectioned")
                node_id = stable_uuid(f"{document_id}::section::{stable_id}")
                section_node = HierarchyNode(
                    node_id=node_id,
                    stable_id=stable_id,
                    content_hash="",
                    document_id=document_id,
                    document_stable_id=document_stable_id,
                    node_type="section",
                    parent_id=document_id,
                    title="Unsectioned",
                    text="",
                    section_path=section_path,
                    section_titles=list(section_titles),
                    page_number=chunk.page_number,
                    ordinal=section_ordinal,
                    indexable=False,
                    excluded_from_index=inherited_reason is not None,
                    exclusion_reason=inherited_reason,
                    source=chunk.source,
                    lineage=[],
                    lineage_ids=[document_id],
                    metadata={"anchor_text": "Unsectioned"},
                )
                section_nodes[section_titles] = section_node
                nodes.append(section_node)
            section_lineage_ids = [document_id, section_nodes[section_titles].node_id]

        section_leaf_ordinals[section_titles] += 1
        anchor_text = _anchor_text(chunk)
        # For tables, prefer structured clean_text if available
        if chunk.chunk_type == "table" and chunk.metadata.get("table_clean_text"):
            clean_text = str(chunk.metadata.get("table_clean_text"))
        else:
            clean_text = str(chunk.metadata.get("clean_text") or normalize_text(chunk.text))
        normalized_text = normalize_text(clean_text or chunk.text)
        node_text = chunk.text.strip()
        if chunk.chunk_type == "table" and chunk.markdown_text:
            # Persist full markdown table as primary content for retrieval/comparison.
            node_text = chunk.markdown_text.strip()
        content_digest = sha256_hex(normalized_text.encode("utf-8")) if normalized_text else ""
        pure_hash = _pure_text_hash(clean_text or chunk.text)
        stable_id = stable_uuid(
            f"{chunk.chunk_type}::{doc_family}::{section_path}::{anchor_text}"
        )
        node_id = stable_uuid(
            "::".join(
                [
                    document_id,
                    chunk.chunk_type,
                    stable_id,
                    str(section_leaf_ordinals[section_titles]),
                    content_digest,
                ]
            )
        )
        exclusion_reason = inherited_reason or _classify_toc_block(
            title=chunk.title,
            section_titles=section_titles,
            text=chunk.text,
            labels=chunk.labels,
        )
        indexable = (
            chunk.chunk_type in {"clause", "table"}
            and bool(normalized_text)
            and exclusion_reason is None
        )
        if chunk.chunk_type == "figure":
            indexable = False

        node = HierarchyNode(
            node_id=node_id,
            stable_id=stable_id,
            content_hash=content_digest,
            pure_text_hash=pure_hash,
            document_id=document_id,
            document_stable_id=document_stable_id,
            node_type=chunk.chunk_type,
            parent_id=section_nodes[section_titles].node_id,
            title=chunk.title,
            text=node_text,
            section_path=section_path,
            section_titles=list(section_titles),
            page_number=chunk.page_number,
            ordinal=chunk.ordinal,
            indexable=indexable,
            excluded_from_index=exclusion_reason is not None,
            exclusion_reason=exclusion_reason,
            source=chunk.source,
            lineage=list(section_titles),
            lineage_ids=section_lineage_ids,
            metadata={
                **chunk.metadata,
                "clean_text": clean_text,
                "markdown_text": chunk.markdown_text,
                "canonical_text": str(chunk.metadata.get("comparison_text") or chunk.metadata.get("canonical_text") or normalized_text),
                "anchor_text": anchor_text,
                "docling_path": chunk.docling_path,
                "source_labels": list(chunk.labels),
                "source_refs": list(chunk.source_refs),
            },
        )
        nodes.append(node)

        if node.node_type in {"clause", "table"} and not node.excluded_from_index and node.text:
            for depth in range(1, len(section_titles) + 1):
                section_buffers[section_titles[:depth]].append(clean_text or node.text)

    for path, section_node in section_nodes.items():
        aggregated_text = _aggregate_section_text(section_buffers.get(path, []))
        summary = summarize_section_fragments(section_buffers.get(path, []))
        section_node.text = aggregated_text
        section_node.content_hash = (
            sha256_hex(normalize_text(aggregated_text).encode("utf-8"))
            if aggregated_text
            else ""
        )
        section_node.indexable = bool(aggregated_text) and not section_node.excluded_from_index
        section_node.metadata["descendant_text_fragments"] = len(section_buffers.get(path, []))
        section_node.metadata["clean_text"] = aggregated_text
        section_node.metadata["summary_text"] = summary["summary_text"]
        section_node.metadata["summary_obligations"] = summary["obligations"]
        section_node.metadata["summary_numbers"] = summary["numbers"]
        section_node.metadata["summary_sentences"] = summary["sentence_count"]

    indexable_nodes = [
        node
        for node in nodes
        if node.node_type in {"section", "clause", "table"}
        and node.indexable
        and node.text
        and not node.excluded_from_index
    ]

    counts = Counter(node.node_type for node in nodes)
    return DocumentHierarchy(
        document_id=document_id,
        document_stable_id=document_stable_id,
        document_family=doc_family,
        content_hash=content_hash,
        nodes=nodes,
        indexable_nodes=indexable_nodes,
        metadata={
            "node_counts": dict(counts),
            "excluded_nodes": sum(1 for node in nodes if node.excluded_from_index),
            "ocr_nodes": sum(1 for node in nodes if node.source == "pytesseract"),
        },
    )


def document_family_from_filename(filename: str) -> str:
    stem = Path(filename).stem.strip()
    if not stem:
        return "document"
    simplified = _VERSION_SUFFIX_RE.sub("", stem).strip() or stem
    return slugify_text(simplified) or "document"


def _repair_section_title_fusion(title: str) -> str:
    """Insert spaces at word boundaries fused together by OCR/PDF extraction.

    Handles three patterns:
    - Dotted decimal prefix: "5.1.3.2Durchfuhrung" → "5.1.3.2 Durchfuhrung"
    - Label + number: "Table16Minimum" → "Table 16 Minimum"
    - CamelCase boundary: "PrufungNummer" → "Prufung Nummer"
    """
    t = _SEC_FUSION_RE.sub(r"\1 \2", title)         # dotted decimal prefix
    t = _LABEL_DASH_RE.sub(r"\1 \2 \3", t)          # label + number + dash + text
    t = _LABEL_NUM_FUSION_RE.sub(r"\1 \2 \3", t)    # label + number + letter
    t = _DIGIT_UPPER_RE.sub(r"\1 \2", t)             # digit → uppercase boundary
    t = _CAMEL_BOUNDARY_RE.sub(r"\1 \2", t)          # camelCase word boundary
    # Collapse any double-spaces introduced by multiple passes
    t = re.sub(r"  +", " ", t).strip()
    return t


def _expand_numeric_section_path(titles: tuple[str, ...]) -> tuple[str, ...]:
    """Expand a single-element flat path with a multi-level section number into ancestors.

    When Docling emits all section headers as body siblings (flat parse tree), the
    section hierarchy implied by the dotted number is lost.  This function reconstructs
    it so that "5.1.2.2 Durchfuhrung" becomes ("5", "5.1", "5.1.2", "5.1.2.2 Durchfuhrung").

    Only applied when the input tuple has exactly one element (Docling flat case).
    Multi-element tuples are returned unchanged (Docling already nested them correctly).
    """
    if len(titles) != 1:
        return titles

    raw = _repair_section_title_fusion(titles[0])
    m = _SEC_NUM_PREFIX_RE.match(raw)
    if not m:
        # Non-numeric heading (e.g. "Legende", "Foreword") — return repaired string only
        return (raw,) if raw != titles[0] else titles

    num_part = m.group(1)   # "5.1.2.2"
    rest = m.group(2).strip()  # "Durchfuhrung"
    components = num_part.split(".")

    if len(components) <= 1:
        # Single-level like "5" — no ancestor expansion needed
        return (raw,)

    # Build intermediate ancestors: ("5", "5.1", "5.1.2") + full title
    ancestors = tuple(".".join(components[:i]) for i in range(1, len(components)))
    full_title = f"{num_part} {rest}".strip() if rest else num_part
    return ancestors + (full_title,)


def _normalize_section_titles(chunk: ParsedChunk) -> tuple[str, ...]:
    titles = tuple(title.strip() for title in chunk.section_path if title and title.strip())
    if titles:
        return _expand_numeric_section_path(titles)
    if chunk.chunk_type == "heading" and chunk.title:
        t = chunk.title.strip()
        return _expand_numeric_section_path((t,))
    return ()


def _anchor_text(chunk: ParsedChunk) -> str:
    if chunk.chunk_type == "section":
        return chunk.title or (chunk.section_path[-1] if chunk.section_path else "section")

    candidates = [chunk.title or "", chunk.text]
    for candidate in candidates:
        normalized = normalize_text(candidate)
        if not normalized:
            continue
        match = _CLAUSE_MARKER_RE.match(candidate)
        if match:
            return normalize_text(match.group(1))
        tokens = normalized.split()
        return " ".join(tokens[:12])

    if chunk.section_path:
        return normalize_text(chunk.section_path[-1])
    return chunk.chunk_type


def _is_non_compliance_title(title: str) -> bool:
    """Return True when a section title is non-compliance content (foreword, copyright, etc.).

    Uses two strategies:
    1. Regex matching on normalized title (handles spaced text)
    2. Fused-term matching after stripping all non-alphanumeric chars (handles OCR artifacts)
    """
    if not title:
        return False
    norm = normalize_text(title)
    if _NON_COMPLIANCE_RE.search(norm):
        return True
    # Fused/OCR variant: strip all non-alphanumeric and check against known fused terms
    stripped = re.sub(r"[^a-zA-Z0-9]", "", norm).lower()
    return any(term in stripped for term in _NON_COMPLIANCE_FUSED_TERMS)


def _classify_toc_block(
    *,
    title: str | None,
    section_titles: tuple[str, ...],
    text: str,
    labels: tuple[str, ...],
) -> str | None:
    normalized_title = normalize_text(title or "")
    normalized_sections = {normalize_text(part) for part in section_titles}
    normalized_labels = {normalize_text(label) for label in labels}

    if "document_index" in normalized_labels:
        return "document_index"
    if normalized_title in _TOC_TITLES or normalized_sections & _TOC_TITLES:
        return "table_of_contents"

    # Non-compliance editorial sections: foreword, copyright, dates, amendments, etc.
    # Check the section title and all ancestor section titles.
    if _is_non_compliance_title(title or ""):
        return "non_compliance_section"
    for part in section_titles:
        if _is_non_compliance_title(part):
            return "non_compliance_section"

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 3:
        return None

    toc_like_lines = sum(1 for line in lines if _TOC_LINE_RE.match(line))
    if toc_like_lines >= 3 and toc_like_lines / len(lines) >= 0.5:
        return "table_of_contents"
    return None


def _get_inherited_exclusion(
    section_titles: tuple[str, ...],
    exclusions: dict[tuple[str, ...], str],
) -> str | None:
    for depth in range(1, len(section_titles) + 1):
        reason = exclusions.get(section_titles[:depth])
        if reason is not None:
            return reason
    return None


def _aggregate_section_text(parts: list[str]) -> str:
    if not parts:
        return ""
    out: list[str] = []
    size = 0
    for part in parts:
        cleaned = " ".join(part.split())
        if not cleaned:
            continue
        if size + len(cleaned) > _MAX_SECTION_TEXT:
            remaining = _MAX_SECTION_TEXT - size
            if remaining > 0:
                out.append(cleaned[:remaining].rstrip())
            break
        out.append(cleaned)
        size += len(cleaned) + 2
    return "\n\n".join(out)


def summarize_section_fragments(parts: list[str]) -> dict[str, object]:
    """Create a compact, deterministic summary for section alignment."""
    sentences: list[str] = []
    for part in parts:
        for sentence in _SENTENCE_SPLIT_RE.split(part or ""):
            cleaned = " ".join(sentence.split())
            if cleaned:
                sentences.append(cleaned)

    if not sentences:
        return {
            "summary_text": "",
            "obligations": [],
            "numbers": [],
            "sentence_count": 0,
        }

    scored: list[tuple[int, int]] = []
    for idx, sentence in enumerate(sentences):
        score = 0
        if _SUMMARY_OBLIGATION_RE.search(sentence):
            score += 2
        if _SUMMARY_DURATION_RE.search(sentence) or _SUMMARY_NUMBER_RE.search(sentence):
            score += 1
        scored.append((score, idx))

    selected: list[int]
    if any(score > 0 for score, _ in scored):
        top = sorted(scored, key=lambda item: (-item[0], item[1]))[:6]
        selected = sorted(idx for _, idx in top)
    else:
        selected = list(range(min(2, len(sentences))))

    summary_text = " ".join(sentences[idx] for idx in selected).strip()
    obligations: list[str] = []
    for idx in selected:
        meaning = extract_clause_meaning(sentences[idx])
        if meaning.obligation:
            obligations.append(meaning.obligation)
    numbers = sorted(set(_SUMMARY_NUMBER_RE.findall(summary_text)))

    return {
        "summary_text": summary_text,
        "obligations": obligations,
        "numbers": numbers,
        "sentence_count": len(selected),
    }
