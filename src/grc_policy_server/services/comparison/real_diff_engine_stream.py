from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator, Dict, List, Optional

from grc_policy_server.core.logging import logging
from grc_policy_server.models.schemas import (
    ActionItem,
    ChangeDetail,
    ComparisonAccuracyMetrics,
    Document,
    DocumentReference,
    KeyDifference,
    SectionAccuracyMetrics,
)
from grc_policy_server.services.comparison.clause_matcher import (
    ClauseMatch,
    MatchThresholds,
)
from grc_policy_server.services.comparison.comparison_trace import ComparisonTraceStore
from grc_policy_server.services.comparison.diff_postprocessor import random_diff_subset
from grc_policy_server.services.comparison.policy_semantics import (
    ClauseMeaning,
    clean_policy_text,
    compare_clause_meaning,
    extract_clause_meaning,
    is_non_semantic_content,
)
from grc_policy_server.services.comparison.real_diff_engine import (
    RealDiffEngine,
    severity_from_distance,
)
from grc_policy_server.services.documents.canonical_store import CanonicalDocumentStore
from grc_policy_server.services.graph.graph_neo4j_client import Neo4jClient
from grc_policy_server.services.llm.ollama_client import OllamaClient
from grc_policy_server.services.vector.weaviate_client import WeaviateClient

logger = logging.getLogger(__name__)


def impact_from(
    change_type: str,
    distance: Optional[float],
    *,
    obligation_change: str = "unchanged",
    node_type: str = "clause",
) -> str:
    """Map change signals to an impact label.  Only High / Medium / Low are produced."""
    if change_type in ("ADDED", "REMOVED"):
        return "High"
    if obligation_change in ("weakened", "strengthened"):
        return "Medium"
    if obligation_change == "modified" and node_type == "table":
        return "High" if distance is not None and distance > 0.15 else "Medium"
    if distance is None:
        return "High"
    if distance > 0.60:
        return "High"
    if distance > 0.35:
        return "Medium"
    return "Low"


@dataclass
class RealDiffEngineStream:
    weaviate: WeaviateClient
    neo4j: Neo4jClient | None
    llm: OllamaClient
    canonical_store: CanonicalDocumentStore | None = None
    trace_store: ComparisonTraceStore | None = None
    thresholds: MatchThresholds = MatchThresholds()
    topk: int = 5

    async def compare_stream(
        self,
        doc1: Document,
        doc2: Document,
        force_re_extract: bool = False,
    ) -> AsyncIterator[Dict]:
        yield {"type": "progress", "stage": "load_canonical_nodes"}
        engine = RealDiffEngine(
            weaviate=self.weaviate,
            neo4j=self.neo4j,
            llm=self.llm,
            canonical_store=self.canonical_store,
            trace_store=self.trace_store,
            thresholds=self.thresholds,
            topk=self.topk,
        )
        result = await engine.compare(
            doc1,
            doc2,
            force_re_extract=force_re_extract,
        )

        yield {
            "type": "progress",
            "stage": "structured_change_records_ready",
            "diffs": len(result.keyDifferences),
        }

        for diff in result.keyDifferences:
            yield {"type": "diff", "item": diff.model_dump()}

        yield {"type": "progress", "stage": "finalizing"}
        yield {
            "type": "done",
            "summary": result.summary,
            "actionPlan": [action.model_dump() for action in result.actionPlan],
            "followUpQuestions": result.followUpQuestions,
            "accuracyMetrics": (
                result.accuracyMetrics.model_dump()
                if result.accuracyMetrics is not None
                else None
            ),
        }

    async def _make_modified(
        self,
        left: dict,
        right: dict,
        dist: float,
        *,
        obligation_change: str,
    ) -> KeyDifference:
        left_ref = self._citation_from_neo4j_or_fallback(left.get("chunk_id"), left)
        right_ref = self._citation_from_neo4j_or_fallback(right.get("chunk_id"), right)

        # Use better formatting for tables
        node_type = left.get("node_type") or right.get("node_type") or "clause"
        if node_type == "table":
            doc1_content = self._format_table_content(left)
            doc2_content = self._format_table_content(right)
        else:
            doc1_content = self._short(str(left.get("text") or ""))
            doc2_content = self._short(str(right.get("text") or ""))

        return KeyDifference(
            changeType="MODIFIED",
            section=(
                left_ref.section
                if left_ref
                else (left.get("section_path") or "Unknown Section")
            ),
            doc1Content=doc1_content,
            doc2Content=doc2_content,
            impact=impact_from(
                "MODIFIED",
                dist,
                obligation_change=obligation_change,
                node_type=node_type,
            ),
            changeSeverity=severity_from_distance(dist, "MODIFIED"),
            doc1Reference=left_ref,
            doc2Reference=right_ref,
            nodeType=node_type,
            changes=self._extract_changes(left, right, node_type),
        )

    async def _make_removed(self, left: dict) -> KeyDifference:
        left_ref = self._citation_from_neo4j_or_fallback(left.get("chunk_id"), left)
        node_type = left.get("node_type") or "clause"

        if node_type == "table":
            doc1_content = self._format_table_content(left)
        else:
            doc1_content = self._short(str(left.get("text") or ""))

        return KeyDifference(
            changeType="REMOVED",
            section=(
                left_ref.section
                if left_ref
                else (left.get("section_path") or "Unknown Section")
            ),
            doc1Content=doc1_content,
            doc2Content=None,
            impact=impact_from("REMOVED", None),
            changeSeverity="high",
            doc1Reference=left_ref,
            doc2Reference=None,
            nodeType=node_type,
            changes=[
                ChangeDetail(type="removed", text=str(left.get("text") or doc1_content))
            ],
        )

    async def _make_added(self, right: dict) -> KeyDifference:
        right_ref = self._citation_from_neo4j_or_fallback(right.get("chunk_id"), right)
        node_type = right.get("node_type") or "clause"

        if node_type == "table":
            doc2_content = self._format_table_content(right)
        else:
            doc2_content = self._short(str(right.get("text") or ""))

        return KeyDifference(
            changeType="ADDED",
            section=(
                right_ref.section
                if right_ref
                else (right.get("section_path") or "Unknown Section")
            ),
            doc1Content=None,
            doc2Content=doc2_content,
            impact=impact_from("ADDED", None),
            changeSeverity="high",
            doc1Reference=None,
            doc2Reference=right_ref,
            nodeType=node_type,
            changes=[
                ChangeDetail(type="added", text=str(right.get("text") or doc2_content))
            ],
        )

    def _is_non_semantic_node(self, node: dict) -> bool:
        """Return True if the node's content has no semantic diff value."""
        text = str(
            node.get("clean_text") or clean_policy_text(str(node.get("text") or ""))
        ).strip()
        return is_non_semantic_content(text)

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
            # Return first few lines of markdown table
            lines = markdown.strip().split("\n")[:4]
            return "\n".join(lines) + ("..." if len(markdown.split("\n")) > 4 else "")

        return self._short(str(node.get("text") or ""), n=120)

    def _citation_from_neo4j_or_fallback(
        self, chunk_id: Optional[str], fallback: dict
    ) -> Optional[DocumentReference]:
        if chunk_id and self.neo4j is not None:
            citation = self.neo4j.get_chunk_citation(chunk_id=str(chunk_id))
            if citation:
                source_text = self._reference_source_text(fallback) or str(
                    citation.get("sourceText") or ""
                )
                citation["sourceText"] = source_text
                return DocumentReference(**citation)

        return DocumentReference(
            section=fallback.get("section_path", "Unknown Section"),
            page=int(fallback.get("page_number") or fallback.get("page") or 0),
            lineStart=fallback.get("line_start"),
            lineEnd=fallback.get("line_end"),
            sourceText=self._reference_source_text(fallback),
        )

    def _reference_source_text(self, node: dict) -> str:
        if node.get("node_type") == "table":
            markdown = str(node.get("markdown_text") or "").strip()
            if markdown:
                return markdown
        return str(node.get("text") or "")

    def _meaning_change(self, left: dict, right: dict, language: str = "") -> str:
        # For tables, compare structural differences instead of semantics
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

        for index, meaning in zip(
            indexes,
            await self.llm.extract_policy_meanings(
                texts=texts,
                markdown_texts=markdown_texts,
                language=language,
            ),
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

    def _short(self, text: str, n: int = 90) -> str:
        t = " ".join((text or "").split())
        return t if len(t) <= n else t[:n] + "..."

    def _action_plan(self, diffs: List[KeyDifference]) -> List[ActionItem]:
        actions: List[ActionItem] = []
        for diff in diffs:
            if diff.impact == "High":
                actions.append(
                    ActionItem(
                        priority="High",
                        action=f"Assess controls impacted by {diff.changeType.lower()} changes in {diff.section}",
                        timeline="60 days",
                        owner="Compliance Team",
                    )
                )
        return actions[:5]

    async def _followups(
        self,
        *,
        doc1_name: str,
        doc2_name: str,
        diffs: List[KeyDifference],
        language: str,
    ) -> List[str]:
        sampled = random_diff_subset(diffs, max_items=10)
        if not sampled:
            return [
                "Are there any material compliance requirement changes between these versions?",
                "Which sections require immediate policy updates?",
            ]

        try:
            questions = await self.llm.generate_followups(
                doc1_name=doc1_name,
                doc2_name=doc2_name,
                key_differences=sampled,
                max_questions=4,
                language=language,
            )
            questions = [question.strip() for question in questions if question.strip()]
            if questions:
                return questions[:4]
        except Exception:
            logger.exception("failed to generate LLM follow-up questions")

        return [f"What controls must be updated in {diff.section}?" for diff in sampled[:4]]

    async def _two_step_summary(
        self,
        *,
        doc1_name: str,
        doc2_name: str,
        diffs: List[KeyDifference],
        language: str,
    ) -> str:
        if not diffs:
            return "No material differences were detected."

        explanations = await self._explain_differences(diffs, language=language)
        summarize_explanations = getattr(self.llm, "summarize_explanations", None)
        if callable(summarize_explanations):
            try:
                return await summarize_explanations(
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
            key_differences=diffs,
            language=language,
        )

    async def _explain_differences(
        self,
        diffs: List[KeyDifference],
        *,
        language: str,
    ) -> list[dict[str, str]]:
        tasks = [
            self.llm.summarize_diff(
                old_text=self._diff_text(diff.doc1Reference, diff.doc1Content),
                new_text=self._diff_text(diff.doc2Reference, diff.doc2Content),
                section=diff.section,
                language=language,
            )
            for diff in diffs
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        explanations: list[dict[str, str]] = []
        for diff, result in zip(diffs, results, strict=False):
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

    def _diff_text(self, reference: DocumentReference | None, fallback: str | None) -> str:
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
        """Compute accuracy metrics from clause matching results.

        Confidence levels based on match distance:
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

        # Find removed/modified lines
        for line in left_lines:
            if line not in right_set:
                # Check if it was modified
                match = self._find_similar_line(line, right_lines)
                if match:
                    changes.append(
                        ChangeDetail(
                            type="modified", text=line, oldValue=line, newValue=match
                        )
                    )
                else:
                    changes.append(ChangeDetail(type="removed", text=line))

        # Find added lines
        modified_new = {c.newValue for c in changes if c.type == "modified"}
        for line in right_lines:
            if line not in left_set and line not in modified_new:
                changes.append(ChangeDetail(type="added", text=line))

        return changes

    def _find_similar_line(self, line: str, candidates: list[str]) -> str | None:
        """Find a similar line in candidates."""
        from difflib import SequenceMatcher

        for candidate in candidates:
            if SequenceMatcher(None, line.lower(), candidate.lower()).ratio() > 0.6:
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

        # Modified cells
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

        # New cells in added rows
        for pos, right_val in right_cell_map.items():
            if pos not in left_cell_map and right_val:
                row, col = pos
                if row >= left_rows:
                    changes.append(
                        ChangeDetail(
                            type="added",
                            text=right_val,
                            location=f"Row {row + 1}, Col {col + 1}",
                        )
                    )

        return changes
