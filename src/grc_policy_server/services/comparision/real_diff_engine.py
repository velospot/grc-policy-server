from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import List, Optional

from grc_policy_server.core.logging import logging
from grc_policy_server.models.schemas import (
    ActionItem,
    ChangeDetail,
    ComparisonAccuracyMetrics,
    ComparisonResult,
    Document,
    DocumentReference,
    KeyDifference,
    SectionAccuracyMetrics,
)
from grc_policy_server.services.comparision.clause_matcher import (
    ClauseMatch,
    ClauseMatcher,
    MatchThresholds,
)
from grc_policy_server.services.comparision.diff_postprocessor import (
    build_section_alignment_maps,
    # filter_key_differences,
    # find_unchanged_section_pairs,
    random_diff_subset,
)
from grc_policy_server.services.comparision.policy_semantics import (
    ClauseMeaning,
    clean_policy_text,
    compare_clause_meaning,
    extract_clause_meaning,
    is_non_semantic_content,
)
from grc_policy_server.services.graph.graph_neo4j_client import Neo4jClient
from grc_policy_server.services.llm.ollama_client import OllamaClient
from grc_policy_server.services.vector.weaviate_client import WeaviateClient

logger = logging.getLogger(__name__)


def severity_from_distance(distance: Optional[float], change_type: str) -> str:
    """changeSeverity is the *inverse* of matchScore (distance = 1 − matchScore).

    matchScore ≈ 1.0  →  distance ≈ 0.0  →  "low"   (barely changed)
    matchScore ≈ 0.5  →  distance ≈ 0.5  →  "medium"
    matchScore ≈ 0.0  →  distance ≈ 1.0  →  "high"  (completely different)

    ADDED / REMOVED nodes have no counterpart so they are always "high".
    """
    if change_type in ("ADDED", "REMOVED"):
        return "high"
    if distance is None:
        return "high"
    # distance > 0.60  →  matchScore < 0.40
    if distance > 0.60:
        return "high"
    # distance > 0.35  →  matchScore < 0.65
    if distance > 0.35:
        return "medium"
    # distance ≤ 0.35  →  matchScore ≥ 0.65
    return "low"


def impact_from_distance(
    distance: Optional[float],
    change_type: str,
    *,
    obligation_change: str = "unchanged",
    node_type: str = "clause",
) -> str:
    if change_type == "ADDED":
        return "High"
    if change_type == "REMOVED":
        return "High"
    if obligation_change == "weakened":
        return "Critical"
    if obligation_change == "strengthened":
        return "High"
    # For tables, "modified" means structural/content changes detected
    if obligation_change == "modified" and node_type == "table":
        # Tables with detected changes should be at least Medium impact
        if distance is not None and distance > 0.15:
            return "High"
        return "Medium"
    if distance is None:
        return "High"
    if distance > 0.45:
        return "Critical"
    if distance > 0.35:
        return "High"
    if distance > 0.25:
        return "Medium"
    return "Low"


@dataclass
class RealDiffEngine:
    weaviate: WeaviateClient
    neo4j: Neo4jClient | None
    llm: OllamaClient
    thresholds: MatchThresholds = MatchThresholds()
    topk: int = 5
    max_diffs: int = 40

    async def compare(
        self,
        doc1: Document,
        doc2: Document,
        force_re_extract: bool = False,
    ) -> ComparisonResult:
        left_nodes = self.weaviate.fetch_chunks_by_document(doc1.id)
        right_nodes = self.weaviate.fetch_chunks_by_document(doc2.id)

        # Detect language from first document's text for better LLM accuracy
        language = await self._detect_document_language(left_nodes)
        logger.info("detected document language=%s", language or "unknown")

        left_nodes = await self._enrich_nodes_with_semantics(
            left_nodes, force_re_extract=force_re_extract, language=language
        )
        right_nodes = await self._enrich_nodes_with_semantics(
            right_nodes, force_re_extract=force_re_extract, language=language
        )
        logger.info(
            "compare left_nodes=%s right_nodes=%s", len(left_nodes), len(right_nodes)
        )

        matcher = ClauseMatcher(
            search_fn=self.weaviate.search_section_in_document,
            thresholds=self.thresholds,
            topk=self.topk,
            language=language,
        )
        matching = matcher.match(
            left_nodes=left_nodes,
            right_nodes=right_nodes,
            target_document_id=doc2.id,
        )
        left_to_right, right_to_left = build_section_alignment_maps(
            matching.section_matches
        )
        # unchanged_section_pairs = find_unchanged_section_pairs(
        #     section_matches=matching.section_matches,
        #     left_nodes=left_nodes,
        #     right_nodes=right_nodes,
        # )

        diffs: List[KeyDifference] = []

        for match in matching.matches:
            # Skip nodes whose content carries no semantic diff signal
            # (page numbers, bare section refs, single digits, etc.)
            if self._is_non_semantic_node(match.left) and self._is_non_semantic_node(
                match.right
            ):
                continue
            meaning_change = self._meaning_change(match.left, match.right, language)
            if (
                match.distance <= self.thresholds.unchanged_distance
                and meaning_change == "unchanged"
            ):
                continue
            left_ref = self._citation_from_neo4j_or_fallback(match.left)
            right_ref = self._citation_from_neo4j_or_fallback(match.right)

            # Use better formatting for tables
            node_type = (
                match.left.get("node_type") or match.right.get("node_type") or "clause"
            )
            is_table = node_type == "table"
            if is_table:
                doc1_content = self._format_table_content(match.left)
                doc2_content = self._format_table_content(match.right)
            else:
                doc1_content = self._short(str(match.left.get("text") or ""))
                doc2_content = self._short(str(match.right.get("text") or ""))

            diffs.append(
                KeyDifference(
                    changeType="MODIFIED",
                    section=(
                        left_ref.section
                        if left_ref
                        else str(match.left.get("section_path") or "Unknown Section")
                    ),
                    doc1Content=doc1_content,
                    doc2Content=doc2_content,
                    impact=impact_from_distance(
                        match.distance,
                        "MODIFIED",
                        obligation_change=meaning_change,
                        node_type=node_type,
                    ),
                    changeSeverity=severity_from_distance(match.distance, "MODIFIED"),
                    doc1Reference=left_ref,
                    doc2Reference=right_ref,
                    nodeType=node_type,
                    changes=self._extract_changes(match.left, match.right, node_type),
                )
            )

        for left_node in matching.removed:
            if self._is_non_semantic_node(left_node):
                continue
            left_ref = self._citation_from_neo4j_or_fallback(left_node)
            node_type = left_node.get("node_type") or "clause"
            is_table = node_type == "table"
            doc1_content = (
                self._format_table_content(left_node)
                if is_table
                else self._short(str(left_node.get("text") or ""))
            )

            diffs.append(
                KeyDifference(
                    changeType="REMOVED",
                    section=(
                        left_ref.section
                        if left_ref
                        else str(left_node.get("section_path") or "Unknown Section")
                    ),
                    doc1Content=doc1_content,
                    doc2Content=None,
                    impact=impact_from_distance(None, "REMOVED"),
                    changeSeverity="high",
                    doc1Reference=left_ref,
                    doc2Reference=None,
                    nodeType=node_type,
                    changes=[
                        ChangeDetail(
                            type="removed",
                            text=str(left_node.get("text") or doc1_content),
                        )
                    ],
                )
            )

        for right_node in matching.added:
            if self._is_non_semantic_node(right_node):
                continue
            right_ref = self._citation_from_neo4j_or_fallback(right_node)
            node_type = right_node.get("node_type") or "clause"
            is_table = node_type == "table"
            doc2_content = (
                self._format_table_content(right_node)
                if is_table
                else self._short(str(right_node.get("text") or ""))
            )

            diffs.append(
                KeyDifference(
                    changeType="ADDED",
                    section=(
                        right_ref.section
                        if right_ref
                        else str(right_node.get("section_path") or "Unknown Section")
                    ),
                    doc1Content=None,
                    doc2Content=doc2_content,
                    impact=impact_from_distance(None, "ADDED"),
                    changeSeverity="high",
                    doc1Reference=None,
                    doc2Reference=right_ref,
                    nodeType=node_type,
                    changes=[
                        ChangeDetail(
                            type="added",
                            text=str(right_node.get("text") or doc2_content),
                        )
                    ],
                )
            )

        diffs.sort(
            key=lambda diff: (
                ("Critical", "High", "Medium", "Low").index(diff.impact)
                if diff.impact in ("Critical", "High", "Medium", "Low")
                else 2
            )
        )

        # only_changed_diffs = filter_key_differences(
        #     diffs,
        #     unchanged_section_pairs=unchanged_section_pairs,
        #     left_to_right=left_to_right,
        #     right_to_left=right_to_left,
        # )

        await self._populate_markdown_diff_summaries(diffs, language=language)

        summary = await self._two_step_summary(
            doc1_name=doc1.name,
            doc2_name=doc2.name,
            key_differences=diffs,
            language=language,
        )
        accuracy_metrics = self._compute_accuracy_metrics(matching.matches)

        return ComparisonResult(
            summary=summary,
            keyDifferences=diffs,
            actionPlan=self._action_plan(diffs),
            followUpQuestions=await self._follow_ups(
                doc1_name=doc1.name,
                doc2_name=doc2.name,
                diffs=diffs,
                language=language,
            ),
            accuracyMetrics=accuracy_metrics,
        )

    def _meaning_change(self, left: dict, right: dict, language: str = "") -> str:
        if left.get("node_type") == "table" or right.get("node_type") == "table":
            return self._table_meaning_change(left, right)
        return compare_clause_meaning(
            self._node_meaning(left),
            self._node_meaning(right),
            language,
        ).obligation_change

    def _table_meaning_change(self, left: dict, right: dict) -> str:
        """Detect structural/content differences in tables."""
        left_rows = left.get("table_num_rows", 0)
        left_cols = left.get("table_num_cols", 0)
        right_rows = right.get("table_num_rows", 0)
        right_cols = right.get("table_num_cols", 0)

        # Dimension change = structural modification
        if left_rows != right_rows or left_cols != right_cols:
            return "modified"

        # Check cell content differences
        left_cells = left.get("table_cells") or []
        right_cells = right.get("table_cells") or []

        left_cell_map = {
            (c.get("row", 0), c.get("col", 0)): str(c.get("text", "")).strip().lower()
            for c in left_cells
        }
        right_cell_map = {
            (c.get("row", 0), c.get("col", 0)): str(c.get("text", "")).strip().lower()
            for c in right_cells
        }

        # Any cell content difference = modified
        all_positions = set(left_cell_map.keys()) | set(right_cell_map.keys())
        for pos in all_positions:
            if left_cell_map.get(pos) != right_cell_map.get(pos):
                return "modified"

        return "unchanged"

    def _format_table_content(self, node: dict) -> str:
        """Format table content for display in diffs."""
        title = node.get("title") or ""
        num_rows = node.get("table_num_rows", 0)
        num_cols = node.get("table_num_cols", 0)

        if num_rows and num_cols:
            table_desc = f"Table ({num_rows} rows × {num_cols} cols)"
            if title:
                table_desc = f"{title}: {table_desc}"
            return table_desc

        # Fallback to markdown or text
        markdown = node.get("markdown_text") or ""
        if markdown:
            lines = markdown.strip().split("\n")[:4]
            return "\n".join(lines) + ("..." if len(markdown.split("\n")) > 4 else "")

        return self._short(str(node.get("text") or ""), n=120)

    async def _enrich_nodes_with_semantics(
        self,
        nodes: list[dict],
        force_re_extract: bool = False,
        language: str = "",
    ) -> list[dict]:
        enriched = [dict(node) for node in nodes]
        indexes: list[int] = []
        texts: list[str] = []
        markdown_texts: list[str] = []

        for index, node in enumerate(enriched):
            if not node.get("clean_text"):
                node["clean_text"] = clean_policy_text(str(node.get("text") or ""))
            if node.get("node_type") != "clause":
                continue
            # Skip if already has semantics, unless force_re_extract is True
            if not force_re_extract and any(
                node.get(field)
                for field in ("obligation", "subject", "action", "object", "condition")
            ):
                continue
            text = str(node.get("text") or "").strip()
            if not text:
                continue
            indexes.append(index)
            texts.append(text)
            # Use markdown if available, otherwise fall back to plain text
            markdown_texts.append(str(node.get("markdown_text") or text))

        if not texts:
            return enriched

        try:
            extracted = await self.llm.extract_policy_meanings(
                texts=texts,
                markdown_texts=markdown_texts,
                language=language,
            )
        except TypeError:
            # Backward compatibility with older stubs/fakes in unit tests.
            extracted = await self.llm.extract_policy_meanings(texts=texts)  # type: ignore[call-arg]

        for index, meaning in zip(
            indexes,
            extracted,
            strict=False,
        ):
            for field, value in meaning.items():
                enriched[index][field] = str(value or "")

        return enriched

    async def _detect_document_language(self, nodes: list[dict]) -> str:
        """Detect language from first few chunks of text."""
        sample_texts = []
        for node in nodes[:5]:
            text = str(node.get("text") or "").strip()
            if text:
                sample_texts.append(text)
            if len(" ".join(sample_texts)) > 500:
                break
        if not sample_texts:
            return ""
        sample = " ".join(sample_texts)[:500]
        return await self.llm.detect_language(sample)

    def _node_meaning(self, node: dict) -> ClauseMeaning:
        obligation = str(node.get("obligation") or "")
        subject = str(node.get("subject") or "")
        action = str(node.get("action") or "")
        obj = str(node.get("object") or "")
        condition = str(node.get("condition") or "")
        if obligation or subject or action or obj or condition:
            return ClauseMeaning(obligation, subject, action, obj, condition)
        return extract_clause_meaning(str(node.get("text") or ""))

    def _citation_from_neo4j_or_fallback(self, chunk: dict) -> DocumentReference:
        chunk_id = chunk.get("chunk_id")
        if chunk_id and self.neo4j is not None:
            citation = self.neo4j.get_chunk_citation(chunk_id=str(chunk_id))
            if citation:
                source_text = self._reference_source_text(chunk) or str(
                    citation.get("sourceText") or ""
                )
                citation["sourceText"] = source_text
                return DocumentReference(**citation)
        page = chunk.get("page_number")
        if page is None:
            page = chunk.get("page")
        source_text = self._reference_source_text(chunk)
        return DocumentReference(
            section=str(chunk.get("section_path") or "Unknown Section"),
            page=int(page or 0),
            lineStart=chunk.get("line_start"),
            lineEnd=chunk.get("line_end"),
            sourceText=source_text,
        )

    def _reference_source_text(self, chunk: dict) -> str:
        if chunk.get("node_type") == "table":
            markdown = str(chunk.get("markdown_text") or "").strip()
            if markdown:
                return markdown
        return str(chunk.get("text") or "")

    def _is_non_semantic_node(self, node: dict) -> bool:
        """Return True if the node's content has no semantic diff value."""
        text = str(
            node.get("clean_text") or clean_policy_text(str(node.get("text") or ""))
        ).strip()
        return is_non_semantic_content(text)

    def _short(self, text: str, n: int = 90) -> str:
        t = " ".join((text or "").split())
        return t if len(t) <= n else t[:n] + "..."

    def _action_plan(self, diffs: List[KeyDifference]) -> List[ActionItem]:
        actions: List[ActionItem] = []
        for diff in diffs:
            if diff.impact in ("Critical", "High"):
                actions.append(
                    ActionItem(
                        priority="Immediate" if diff.impact == "Critical" else "High",
                        action=f"Assess compliance impact of {diff.changeType.lower()} items in {diff.section}",
                        timeline="30 days" if diff.impact == "Critical" else "60 days",
                        owner="Compliance Team",
                    )
                )
        return actions[:5]

    async def _follow_ups(
        self,
        *,
        doc1_name: str,
        doc2_name: str,
        diffs: List[KeyDifference],
        language: str,
    ) -> List[str]:
        sampled_diffs = random_diff_subset(diffs, max_items=10)
        if not sampled_diffs:
            return [
                "Are there any material compliance requirement changes between these versions?",
                "Which sections require immediate policy updates?",
            ]

        try:
            questions = await self.llm.generate_followups(
                doc1_name=doc1_name,
                doc2_name=doc2_name,
                key_differences=sampled_diffs,
                max_questions=4,
                language=language,
            )
            questions = [question.strip() for question in questions if question.strip()]
            if questions:
                return questions[:4]
        except Exception:
            logger.exception("failed to generate LLM follow-up questions")

        return [
            f"What controls or evidence must be updated for {diff.section}?"
            for diff in sampled_diffs[:4]
        ]

    async def _two_step_summary(
        self,
        *,
        doc1_name: str,
        doc2_name: str,
        key_differences: List[KeyDifference],
        language: str,
    ) -> str:
        if not key_differences:
            return "No material differences were detected."

        explanations = await self._explain_differences(
            key_differences, language=language
        )

        summarize_explanations = getattr(self.llm, "summarize_explanations", None)
        if callable(summarize_explanations):
            try:
                return await self.llm.summarize_explanations(
                    doc1_name=doc1_name,
                    doc2_name=doc2_name,
                    explanations=explanations,
                    language=language,
                )
            except Exception:
                logger.exception("failed two-step summary aggregation, falling back")

        return await self.llm.summarize_changes(
            doc1_name=doc1_name,
            doc2_name=doc2_name,
            key_differences=key_differences,
            language=language,
        )

    async def _explain_differences(
        self,
        key_differences: List[KeyDifference],
        *,
        language: str,
    ) -> list[dict[str, str]]:
        capped = key_differences[: self.max_diffs]
        tasks = [
            self.llm.summarize_diff(
                old_text=self._diff_text(diff.doc1Reference, diff.doc1Content),
                new_text=self._diff_text(diff.doc2Reference, diff.doc2Content),
                section=diff.section,
                language=language,
            )
            for diff in capped
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        explanations: list[dict[str, str]] = []
        for diff, result in zip(capped, results, strict=False):
            if isinstance(result, Exception):
                explanation = (
                    f"{diff.changeType} change in {diff.section}: "
                    "content changed based on canonical comparison."
                )
            else:
                explanation = str(result or "").strip()
            explanations.append(
                {
                    "changeType": diff.changeType,
                    "section": diff.section,
                    "nodeType": diff.nodeType,
                    "explanation": explanation,
                }
            )
        return explanations

    def _diff_text(
        self,
        reference: DocumentReference | None,
        fallback: str | None,
    ) -> str:
        if reference and reference.sourceText:
            return reference.sourceText
        return str(fallback or "")

    def _section_accuracy_metrics(
        self, matches: list[ClauseMatch]
    ) -> list[SectionAccuracyMetrics]:
        sections: dict[str, list[float]] = {}
        for match in matches:
            section = str(
                match.right.get("section_path")
                or match.left.get("section_path")
                or "Unknown Section"
            )
            sections.setdefault(section, []).append(match.distance)

        metrics: list[SectionAccuracyMetrics] = []
        for section, distances in sections.items():
            count = len(distances)
            avg_distance = sum(distances) / count
            avg_score = 1.0 - avg_distance
            high = sum(1 for d in distances if d <= self.thresholds.unchanged_distance)
            med = sum(
                1
                for d in distances
                if self.thresholds.unchanged_distance
                < d
                <= self.thresholds.max_match_distance
            )
            low = count - high - med
            confidence = (high * 1.0 + med * 0.7 + low * 0.3) / count

            metrics.append(
                SectionAccuracyMetrics(
                    section=section,
                    avg_match_distance=round(avg_distance, 4),
                    avg_match_score=round(avg_score, 4),
                    match_count=count,
                    confidence=round(confidence, 4),
                )
            )

        return sorted(metrics, key=lambda m: m.section)

    def _compute_accuracy_metrics(
        self, matches: list[ClauseMatch]
    ) -> ComparisonAccuracyMetrics:
        """Confidence levels based on match distance:
        - High: distance <= 0.20 (very similar clauses)
        - Medium: distance <= 0.35 (reasonably similar)
        - Low: distance > 0.35 (weak match)
        """
        if not matches:
            return ComparisonAccuracyMetrics(
                avg_match_distance=0.0,
                avg_match_score=None,
                high_confidence_matches=0,
                medium_confidence_matches=0,
                low_confidence_matches=0,
                total_matches=0,
                overall_confidence=0.0,
                confidence_breakdown={
                    "stable_id": 0,
                    "section_stable_id": 0,
                    "section_alignment": 0,
                    "vector_search": 0,
                },
                section_metrics=[],
            )

        distances = [m.distance for m in matches]
        avg_distance = sum(distances) / len(distances)
        avg_score = 1.0 - avg_distance

        high_conf = sum(1 for d in distances if d <= self.thresholds.unchanged_distance)
        medium_conf = sum(
            1
            for d in distances
            if self.thresholds.unchanged_distance
            < d
            <= self.thresholds.max_match_distance
        )
        low_conf = sum(1 for d in distances if d > self.thresholds.max_match_distance)

        breakdown: dict[str, int] = {}
        for m in matches:
            breakdown[m.matched_by] = breakdown.get(m.matched_by, 0) + 1

        weighted_confidence = (
            high_conf * 1.0 + medium_conf * 0.7 + low_conf * 0.3
        ) / len(matches)

        return ComparisonAccuracyMetrics(
            avg_match_distance=round(avg_distance, 4),
            avg_match_score=round(avg_score, 4),
            high_confidence_matches=high_conf,
            medium_confidence_matches=medium_conf,
            low_confidence_matches=low_conf,
            total_matches=len(matches),
            overall_confidence=round(weighted_confidence, 4),
            confidence_breakdown=breakdown,
            section_metrics=self._section_accuracy_metrics(matches),
        )

    async def _populate_markdown_diff_summaries(
        self, diffs: List[KeyDifference], *, language: str = ""
    ) -> None:
        """Generate markdownDiffSummary for every diff in parallel via LLM."""
        tasks = [
            self.llm.generate_markdown_diff_summary(
                node_type=diff.nodeType,
                change_type=diff.changeType,
                doc1_source_text=(
                    diff.doc1Reference.sourceText if diff.doc1Reference else None
                ),
                doc2_source_text=(
                    diff.doc2Reference.sourceText if diff.doc2Reference else None
                ),
                language=language,
            )
            for diff in diffs
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for diff, result in zip(diffs, results, strict=False):
            if isinstance(result, Exception):
                logger.warning(
                    "markdownDiffSummary generation failed for section=%s: %s",
                    diff.section,
                    result,
                )
            else:
                diff.markdownDiffSummary = str(result or "").strip() or None

    def _extract_changes(
        self, left: dict, right: dict, node_type: str
    ) -> List[ChangeDetail]:
        """Extract specific changes between two nodes for UI highlighting."""
        if node_type == "table":
            return self._extract_table_changes(left, right)
        return self._extract_text_changes(left, right)

    def _extract_text_changes(self, left: dict, right: dict) -> List[ChangeDetail]:
        """Extract line-level changes between two text chunks."""
        changes: List[ChangeDetail] = []
        left_text = str(left.get("text") or "")
        right_text = str(right.get("text") or "")

        # Split into lines/items (handle bullet points)
        left_lines = [
            line.strip()
            for line in left_text.replace(" - ", "\n- ").split("\n")
            if line.strip()
        ]
        right_lines = [
            r.strip()
            for r in right_text.replace(" - ", "\n- ").split("\n")
            if r.strip()
        ]

        left_set = set(left_lines)
        right_set = set(right_lines)

        # Find removed lines
        for line in left_lines:
            if line not in right_set:
                # Check if it was modified (similar line exists)
                modified_match = self._find_similar_line(line, right_lines)
                if modified_match:
                    changes.append(
                        ChangeDetail(
                            type="modified",
                            text=line,
                            oldValue=line,
                            newValue=modified_match,
                        )
                    )
                else:
                    changes.append(
                        ChangeDetail(
                            type="removed",
                            text=line,
                        )
                    )

        # Find added lines (excluding those already matched as modified)
        modified_new_values = {c.newValue for c in changes if c.type == "modified"}
        for line in right_lines:
            if line not in left_set and line not in modified_new_values:
                changes.append(
                    ChangeDetail(
                        type="added",
                        text=line,
                    )
                )

        return changes

    def _find_similar_line(self, line: str, candidates: list[str]) -> str | None:
        """Find a similar line in candidates (for detecting modifications)."""
        from difflib import SequenceMatcher

        line_lower = line.lower()
        for candidate in candidates:
            ratio = SequenceMatcher(None, line_lower, candidate.lower()).ratio()
            if ratio > 0.6:  # More than 60% similar
                return candidate
        return None

    def _extract_table_changes(self, left: dict, right: dict) -> List[ChangeDetail]:
        """Extract cell-level changes between two tables."""
        changes: List[ChangeDetail] = []

        left_rows = left.get("table_num_rows", 0)
        right_rows = right.get("table_num_rows", 0)

        # Dimension changes
        if left_rows != right_rows:
            if right_rows > left_rows:
                changes.append(
                    ChangeDetail(
                        type="added",
                        text=f"{right_rows - left_rows} row(s) added",
                        location=f"Rows {left_rows + 1}-{right_rows}",
                    )
                )
            else:
                changes.append(
                    ChangeDetail(
                        type="removed",
                        text=f"{left_rows - right_rows} row(s) removed",
                    )
                )

        # Cell-level changes
        left_cells = left.get("table_cells") or []
        right_cells = right.get("table_cells") or []

        left_cell_map = {
            (c.get("row", 0), c.get("col", 0)): str(c.get("text", "")).strip()
            for c in left_cells
        }
        right_cell_map = {
            (c.get("row", 0), c.get("col", 0)): str(c.get("text", "")).strip()
            for c in right_cells
        }

        # Find modified cells (same position, different content)
        for pos, left_val in left_cell_map.items():
            right_val = right_cell_map.get(pos)
            if right_val is not None and left_val != right_val:
                row, col = pos
                changes.append(
                    ChangeDetail(
                        type="modified",
                        text="Cell changed",
                        oldValue=left_val,
                        newValue=right_val,
                        location=f"Row {row + 1}, Col {col + 1}",
                    )
                )

        # Find cells in new rows
        for pos, right_val in right_cell_map.items():
            if pos not in left_cell_map and right_val:
                row, col = pos
                if row >= left_rows:  # New row
                    changes.append(
                        ChangeDetail(
                            type="added",
                            text=right_val,
                            location=f"Row {row + 1}, Col {col + 1}",
                        )
                    )

        return changes
