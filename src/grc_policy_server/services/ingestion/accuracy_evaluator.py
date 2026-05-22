"""Deep accuracy evaluation for extracted compliance PDF nodes.

Runs the enrichment pipeline (NormalizedFact extraction, EMC classification,
row key extraction, column mapping) against canonical_nodes.json files and
reports coverage/accuracy metrics per document and table.

Usage:
    python -m grc_policy_server.services.ingestion.accuracy_evaluator
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)


@dataclass
class TableAccuracyMetrics:
    """Per-table enrichment accuracy."""

    table_index: int
    caption: str
    emc_test_type: str
    num_rows: int
    num_cols: int
    headers: list[str]
    header_entity_types: list[str]      # OntologyEntityType value or "" for unknown
    column_mapped_pct: float            # % headers mapped to entity type
    data_cells_total: int
    data_cells_with_facts: int
    fact_bearing_cells_pct: float       # % data cells with ≥1 NormalizedFact
    rows_with_key: int
    row_key_coverage_pct: float         # % rows with non-empty row key
    fact_type_counts: dict[str, int]    # frequency_range→n, field_strength→n, etc.
    sample_row_keys: list[str]


@dataclass
class DocumentAccuracyMetrics:
    """Aggregate accuracy metrics for one document."""

    document_id: str
    filename: str
    document_family: str = ""            # e.g. "din_en_60068", "dnv_cg_0339", "tl_81000"
    total_tables: int = 0
    tables_classified: int = 0          # non-UNKNOWN type
    emc_type_distribution: dict[str, int] = field(default_factory=dict)
    overall_fact_coverage: float = 0.0  # % data cells with ≥1 fact
    overall_row_key_coverage: float = 0.0
    overall_column_mapped_pct: float = 0.0
    total_normalized_facts: int = 0
    table_metrics: list[TableAccuracyMetrics] = field(default_factory=list)
    table_source_distribution: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


@dataclass
class AccuracyDriftReport:
    """Drift analysis comparing accuracy snapshots over time for a document."""

    document_id: str
    snapshots: list[dict]              # [{timestamp, overall_fact_coverage, ...}]
    delta_fact_coverage: float         # latest - previous snapshot
    delta_row_key_coverage: float
    delta_column_mapped_pct: float
    trend: Literal["improving", "stable", "degrading"]


class AccuracyEvaluator:
    """Evaluates enrichment accuracy by running the ontology pipeline on stored nodes."""

    def __init__(self, upload_root: Path) -> None:
        self.upload_root = upload_root
        self._init_pipeline()

    def _init_pipeline(self) -> None:
        from grc_policy_server.services.ingestion.ontology.emc_ontology import (
            EMCTestClassifier,
            NormalizedFactExtractor,
        )
        from grc_policy_server.services.ingestion.ontology.safety_ontology import (
            SafetyTestClassifier,
            SafetyFactExtractor,
        )
        from grc_policy_server.services.ingestion.ontology.environment_ontology import (
            EnvTestClassifier,
            EnvFactExtractor,
        )
        from grc_policy_server.services.ingestion.ontology.column_mapper import map_header
        from grc_policy_server.services.ingestion.row_key_extractor import RowKeyExtractor

        self._classifier = EMCTestClassifier()
        self._fact_extractor = NormalizedFactExtractor()
        self._safety_classifier = SafetyTestClassifier()
        self._safety_fact_extractor = SafetyFactExtractor()
        self._env_classifier = EnvTestClassifier()
        self._env_fact_extractor = EnvFactExtractor()
        self._map_header = map_header
        self._row_key_extractor = RowKeyExtractor()

    def _propagate_emc_types(self, table_nodes: list[dict]) -> None:
        """Forward-propagate EMC test type from named tables to their continuations.

        When a table caption contains "(fortgesetzt)" or "continued" and has no
        EMC keywords, inherit the type from the most recent classified table.
        Mutates the node metadata in-place.
        """
        _continuation_re = re.compile(
            r"\(\s*(?:fortgesetzt|continued|suite|cont\.?)\s*\)", re.IGNORECASE
        )
        last_emc_type: str | None = None
        last_headers: list[str] = []

        for node in table_nodes:
            meta = node.get("metadata") or {}
            ts = meta.get("table_structure") or {}
            cells = ts.get("cells") or []
            heading_path = node.get("heading_path") or []
            caption = heading_path[0] if heading_path else ""
            headers = [str(c.get("text") or "") for c in cells if c.get("is_header")]

            # Try to classify this table
            emc_type = self._classifier.classify_table(caption, headers)

            if emc_type.value != "unknown":
                last_emc_type = emc_type.value
                last_headers = headers
            elif last_emc_type and _continuation_re.search(caption):
                # Continuation fragment: propagate type from predecessor
                meta["_propagated_emc_type"] = last_emc_type
                meta["_propagated_headers"] = last_headers
                node["metadata"] = meta

    def evaluate_document(self, document_id: str) -> DocumentAccuracyMetrics:
        doc_dir = self.upload_root / document_id
        nodes_file = doc_dir / "canonical_nodes.json"

        metrics = DocumentAccuracyMetrics(document_id=document_id, filename=document_id)

        if not nodes_file.exists():
            metrics.errors.append(f"canonical_nodes.json not found")
            return metrics

        try:
            raw = json.loads(nodes_file.read_text(encoding="utf-8"))
            # Read filename from canonical_nodes.json top-level field (authoritative source).
            # metadata.json is not written as a separate file; this is the only reliable path.
            if isinstance(raw, dict):
                top_filename = raw.get("filename") or raw.get("original_filename")
                if top_filename:
                    metrics.filename = top_filename
            nodes = raw if isinstance(raw, list) else (raw.get("nodes") or [])
        except Exception as e:
            metrics.errors.append(f"load error: {e}")
            return metrics

        # Resolve document family profile now that the real filename is known
        try:
            from grc_policy_server.services.ingestion.document_family_profile import (
                get_profile_for_document,
            )
            _profile = get_profile_for_document(filename=metrics.filename)
            metrics.document_family = _profile.family_id if _profile else "unknown"
        except Exception:
            metrics.document_family = "unknown"

        table_nodes = [n for n in nodes if str(n.get("node_type") or "").lower() == "table"]
        metrics.total_tables = len(table_nodes)

        # Pre-pass: propagate EMC type from named tables to "(fortgesetzt)" continuations
        self._propagate_emc_types(table_nodes)

        all_data_cells = 0
        all_cells_with_facts = 0
        all_rows = 0
        all_rows_with_keys = 0
        all_mapped_headers = 0
        all_headers = 0

        for idx, node in enumerate(table_nodes):
            try:
                node["_doc_family_hint"] = metrics.document_family
                tm = self._evaluate_table(idx, node)
                metrics.table_metrics.append(tm)

                if tm.emc_test_type != "unknown":
                    metrics.tables_classified += 1
                metrics.emc_type_distribution[tm.emc_test_type] = (
                    metrics.emc_type_distribution.get(tm.emc_test_type, 0) + 1
                )
                # Track which backend was used for each table's canonical extract
                table_source = str((node.get("metadata") or {}).get("table_source") or "docling")
                metrics.table_source_distribution[table_source] = (
                    metrics.table_source_distribution.get(table_source, 0) + 1
                )

                all_data_cells += tm.data_cells_total
                all_cells_with_facts += tm.data_cells_with_facts
                all_rows += tm.num_rows
                all_rows_with_keys += tm.rows_with_key
                all_mapped_headers += int(tm.column_mapped_pct * tm.num_cols)
                all_headers += tm.num_cols

                for fact_type, count in tm.fact_type_counts.items():
                    metrics.total_normalized_facts += count

            except Exception as e:
                metrics.errors.append(f"table {idx} error: {e}")

        metrics.overall_fact_coverage = all_cells_with_facts / max(all_data_cells, 1)
        metrics.overall_row_key_coverage = all_rows_with_keys / max(all_rows, 1)
        metrics.overall_column_mapped_pct = all_mapped_headers / max(all_headers, 1)
        return metrics

    def compare_snapshots(self, document_id: str) -> AccuracyDriftReport:
        """Read all accuracy snapshots and compute drift for *document_id*.

        Snapshots are stored in <upload_root>/_accuracy_snapshots/*.json by
        refresh_accuracy_report. Returns a drift report with per-snapshot trend data
        and delta metrics between the two most recent snapshots.
        """
        snapshots_dir = self.upload_root / "_accuracy_snapshots"
        snapshot_files = sorted(snapshots_dir.glob("*.json")) if snapshots_dir.exists() else []

        doc_snapshots: list[dict] = []
        for sf in snapshot_files:
            try:
                raw = json.loads(sf.read_text(encoding="utf-8"))
                ts = raw.get("timestamp") or sf.stem
                for doc in raw.get("results") or []:
                    if doc.get("document_id") == document_id:
                        doc_snapshots.append({
                            "timestamp": ts,
                            "overall_fact_coverage": float(doc.get("overall_fact_coverage") or 0),
                            "overall_row_key_coverage": float(doc.get("overall_row_key_coverage") or 0),
                            "overall_column_mapped_pct": float(doc.get("overall_column_mapped_pct") or 0),
                            "total_tables": int(doc.get("total_tables") or 0),
                            "tables_classified": int(doc.get("tables_classified") or 0),
                            "total_normalized_facts": int(doc.get("total_normalized_facts") or 0),
                        })
                        break
            except Exception:
                continue

        if len(doc_snapshots) >= 2:
            prev = doc_snapshots[-2]
            latest = doc_snapshots[-1]
            delta_fact = latest["overall_fact_coverage"] - prev["overall_fact_coverage"]
            delta_key = latest["overall_row_key_coverage"] - prev["overall_row_key_coverage"]
            delta_col = latest["overall_column_mapped_pct"] - prev["overall_column_mapped_pct"]
            avg_delta = (delta_fact + delta_key + delta_col) / 3
            if avg_delta > 0.02:
                trend: Literal["improving", "stable", "degrading"] = "improving"
            elif avg_delta < -0.02:
                trend = "degrading"
            else:
                trend = "stable"
        else:
            delta_fact = delta_key = delta_col = 0.0
            trend = "stable"

        return AccuracyDriftReport(
            document_id=document_id,
            snapshots=doc_snapshots,
            delta_fact_coverage=delta_fact,
            delta_row_key_coverage=delta_key,
            delta_column_mapped_pct=delta_col,
            trend=trend,
        )

    def _evaluate_table(self, idx: int, node: dict[str, Any]) -> TableAccuracyMetrics:
        from grc_policy_server.services.documents.canonical_table_model import TableCell, TableColumn, TableRow, CanonicalTable

        meta = node.get("metadata") or {}
        ts = meta.get("table_structure") or {}
        cells_raw = ts.get("cells") or []
        num_rows = int(ts.get("num_rows") or 0)
        num_cols = int(ts.get("num_cols") or 0)
        heading_path = node.get("heading_path") or []
        caption = heading_path[0] if heading_path else ""

        # Extract headers using the same normalisation as the ingestion chunkers so
        # that multi-row (grouped) headers are collapsed to one entry per column and
        # the resulting header list is indexed 0…num_cols-1, matching data cell col
        # positions exactly.
        from grc_policy_server.services.ingestion.table_normalization import extract_headers_from_cells
        headers, header_depth = extract_headers_from_cells(cells_raw, num_cols)

        # Bug 1: When docling marks no cells as headers (common in TL 81000, DNV),
        # ALL cells appear as data cells. Detect this and skip the first header_depth
        # rows from data cell lists so header text isn't scored as fact-bearing data.
        no_header_flags = cells_raw and not any(c.get("is_header") for c in cells_raw)

        # EMC classification (use propagated type for continuation fragments)
        propagated = meta.get("_propagated_emc_type")
        if propagated:
            from grc_policy_server.services.ingestion.ontology.emc_ontology import EMCTestType
            emc_type = EMCTestType(propagated)
        else:
            emc_type = self._classifier.classify_table(caption, headers)

        # Bug 2: Route to the correct domain extractor based on caption/headers.
        # Routing priority depends on document family:
        #   - EMC families (TL 81000, DNV): EMC > Safety > Env
        #   - Environmental families (DIN EN 60068): Env > Safety > EMC
        #   - Unknown families: EMC > Safety > Env (conservative default)
        # This prevents automotive voltage tables from being routed to Safety and
        # environmental test tables from being swamped by EMC over-classification.
        safety_type = self._safety_classifier.classify_table(caption, headers)
        env_type = self._env_classifier.classify_table(caption, headers)
        _env_primary_families = {"din_en_60068"}
        doc_family = node.get("_doc_family_hint", "")  # injected below; fallback to ""

        if doc_family in _env_primary_families:
            # Env-primary: prefer Env, then Safety, then EMC
            if env_type.value != "unknown":
                active_extractor = self._env_fact_extractor
                detected_domain = f"env:{env_type.value}"
            elif safety_type.value != "unknown":
                active_extractor = self._safety_fact_extractor
                detected_domain = f"safety:{safety_type.value}"
            else:
                active_extractor = self._fact_extractor
                detected_domain = emc_type.value
        else:
            # EMC-primary (TL 81000, DNV, unknown): EMC > Safety > Env
            if emc_type.value != "unknown":
                active_extractor = self._fact_extractor
                detected_domain = emc_type.value
            elif safety_type.value != "unknown":
                active_extractor = self._safety_fact_extractor
                detected_domain = f"safety:{safety_type.value}"
            elif env_type.value != "unknown":
                active_extractor = self._env_fact_extractor
                detected_domain = f"env:{env_type.value}"
            else:
                active_extractor = self._fact_extractor
                detected_domain = "unknown"

        # Column mapping — headers[i] corresponds to actual column position i
        from grc_policy_server.services.ingestion.ontology.column_mapper import ENTITY_TYPE_DEFAULT_UNIT
        from grc_policy_server.services.ingestion.ontology.emc_ontology import OntologyEntityType

        entity_types: list[str] = []
        col_entity_map: dict[int, OntologyEntityType] = {}
        for col_pos, h_text in enumerate(headers):
            entity = self._map_header(h_text)
            entity_types.append(entity.value if entity else "")
            if entity is not None:
                col_entity_map[col_pos] = entity
        mapped_count = sum(1 for e in entity_types if e)
        col_mapped_pct = mapped_count / max(len(headers), 1) if headers else 0.0

        # NormalizedFact extraction on data cells (non-header).
        # When no is_header flags are set, skip the first header_depth rows by position.
        def _is_data_cell(c: dict) -> bool:
            if c.get("is_header"):
                return False
            if no_header_flags and int(c.get("row", 0)) < header_depth:
                return False
            return True

        data_cells = [c for c in cells_raw if _is_data_cell(c)]
        cells_with_facts = 0
        fact_type_counts: dict[str, int] = {}

        # Build col_index → header mapping for column name lookup (actual position → header)
        col_to_header = {col_pos: h for col_pos, h in enumerate(headers)}

        for c in data_cells:
            text = str(c.get("text") or "").strip()
            col_idx = c.get("col", 0)
            col_name = col_to_header.get(col_idx, "")
            facts = active_extractor.extract_from_cell(text, column_name=col_name)

            # Column-unit inheritance: bare numeric + typed column → synthesise a fact.
            # For EMC: uses existing ENTITY_TYPE_DEFAULT_UNIT map.
            # For Env: NUMERIC_LIMIT columns (temperature, humidity, duration) synthesise
            # a generic numeric_limit fact so bare values like "90" or "-10" get counted.
            if not facts:
                col_entity = col_entity_map.get(col_idx)
                if col_entity is not None:
                    if active_extractor is self._fact_extractor:
                        inherited_unit = ENTITY_TYPE_DEFAULT_UNIT.get(col_entity)
                        if inherited_unit:
                            fact_type_for_entity = {
                                OntologyEntityType.EMISSION_LIMIT: "emission_limit",
                                OntologyEntityType.FIELD_STRENGTH: "field_strength",
                                OntologyEntityType.FREQUENCY_RANGE: "frequency_range",
                            }.get(col_entity)
                            if fact_type_for_entity:
                                facts = self._fact_extractor.extract_bare_numeric_with_unit(
                                    text, inherited_unit, fact_type_for_entity
                                )
                    elif (
                        active_extractor in (self._env_fact_extractor, self._safety_fact_extractor)
                        and col_entity == OntologyEntityType.NUMERIC_LIMIT
                        and re.match(r"^[+-]?\d[\d.,\s]*$", text.strip())
                    ):
                        # Bare number in a NUMERIC_LIMIT column (temperature, humidity, duration)
                        facts = self._fact_extractor.extract_bare_numeric_with_unit(
                            text, "", "numeric_limit"
                        )

            if facts:
                cells_with_facts += 1
                for f in facts:
                    fact_type_counts[f.fact_type] = fact_type_counts.get(f.fact_type, 0) + 1

        fact_cov = cells_with_facts / max(len(data_cells), 1)

        # Row key extraction — build minimal CanonicalTable.
        # Apply the same no_header_flags guard to exclude header rows from row keys.
        cols = [TableColumn(i, h, h.lower()) for i, h in enumerate(headers)]
        rows_by_idx: dict[int, list[dict]] = {}
        for c in cells_raw:
            if c.get("is_header"):
                continue
            if no_header_flags and int(c.get("row", 0)) < header_depth:
                continue
            r = c.get("row", 0)
            rows_by_idx.setdefault(r, []).append(c)

        canonical_rows = []
        for r_idx in sorted(rows_by_idx):
            row_cells = [
                TableCell(
                    row=r_idx,
                    col=c.get("col", 0),
                    text=str(c.get("text") or ""),
                )
                for c in rows_by_idx[r_idx]
            ]
            canonical_rows.append(TableRow(row_number=r_idx, cells=row_cells))

        table = CanonicalTable(
            table_uid=f"acc_{idx}",
            caption_original=caption,
            caption_normalized=caption.lower(),
            section_path=heading_path,
            pages=[int(node.get("page_from") or 0)],
            columns=cols,
            rows=canonical_rows,
        )

        row_keys = self._row_key_extractor.extract_row_keys(table)
        # extract_row_keys returns dict[int, str] — check string truthiness
        rows_with_key = sum(1 for k in row_keys.values() if k)
        row_key_cov = rows_with_key / max(len(canonical_rows), 1) if canonical_rows else 0.0

        sample_keys = [v for v in list(row_keys.values())[:3] if v]

        return TableAccuracyMetrics(
            table_index=idx,
            caption=caption[:80],
            emc_test_type=detected_domain,
            num_rows=len(canonical_rows),
            num_cols=num_cols,
            headers=headers,
            header_entity_types=entity_types,
            column_mapped_pct=col_mapped_pct,
            data_cells_total=len(data_cells),
            data_cells_with_facts=cells_with_facts,
            fact_bearing_cells_pct=fact_cov,
            rows_with_key=rows_with_key,
            row_key_coverage_pct=row_key_cov,
            fact_type_counts=fact_type_counts,
            sample_row_keys=sample_keys,
        )

    def evaluate_all(self) -> list[DocumentAccuracyMetrics]:
        if not self.upload_root.exists():
            return []
        results = []
        for item in sorted(self.upload_root.iterdir()):
            if not item.is_dir() or item.name.startswith("_"):
                continue
            if not (item / "canonical_nodes.json").exists():
                continue
            logger.info("Accuracy evaluation: %s", item.name)
            results.append(self.evaluate_document(item.name))
        return results

    def print_report(self, metrics: list[DocumentAccuracyMetrics]) -> None:
        for dm in metrics:
            print()
            print("=" * 110)
            print(f"ACCURACY REPORT  —  {dm.filename}  ({dm.document_id[:8]}...)  [family: {dm.document_family}]")
            print("=" * 110)
            print(f"  Tables total       : {dm.total_tables}")
            classified_pct = dm.tables_classified / max(dm.total_tables, 1)
            print(f"  EMC classified     : {dm.tables_classified}/{dm.total_tables}  ({classified_pct:.0%})")
            print(f"  EMC distribution   : {dict(sorted(dm.emc_type_distribution.items()))}")
            print(f"  Fact coverage      : {dm.overall_fact_coverage:.1%}  (data cells with ≥1 NormalizedFact)")
            print(f"  Row key coverage   : {dm.overall_row_key_coverage:.1%}  (rows with non-empty key)")
            print(f"  Column mapping     : {dm.overall_column_mapped_pct:.1%}  (headers mapped to entity type)")
            print(f"  Total Norm. facts  : {dm.total_normalized_facts}")
            if dm.errors:
                for e in dm.errors:
                    print(f"  ! ERROR: {e}")

            print()
            print(f"  {'#':<3} {'Caption':<55} {'EMC Type':<22} {'Facts':>6} {'RowKeys':>8} {'ColMap':>7}")
            print(f"  {'-'*3} {'-'*55} {'-'*22} {'-'*6} {'-'*8} {'-'*7}")
            for tm in dm.table_metrics:
                print(
                    f"  {tm.table_index:<3} {tm.caption[:55]:<55} {tm.emc_test_type:<22} "
                    f"{tm.fact_bearing_cells_pct:>5.0%}  {tm.row_key_coverage_pct:>6.0%}  {tm.column_mapped_pct:>6.0%}"
                )
                # Show header → entity mapping
                header_map = [
                    f"{h[:20]}→{e or '?'}"
                    for h, e in zip(tm.headers, tm.header_entity_types)
                ]
                print(f"       headers  : {' | '.join(header_map)}")
                if tm.fact_type_counts:
                    print(f"       fact types: {tm.fact_type_counts}")
                if tm.sample_row_keys:
                    print(f"       row keys  : {tm.sample_row_keys}")
            print()

        # Aggregate summary
        if len(metrics) > 1:
            total_t = sum(m.total_tables for m in metrics)
            total_cls = sum(m.tables_classified for m in metrics)
            docs_with_tables = [m for m in metrics if m.total_tables > 0]
            n_docs = max(len(docs_with_tables), 1)
            avg_fact = sum(m.overall_fact_coverage for m in docs_with_tables) / n_docs
            avg_rk = sum(m.overall_row_key_coverage for m in docs_with_tables) / n_docs
            avg_cm = sum(m.overall_column_mapped_pct for m in docs_with_tables) / n_docs
            total_nf = sum(m.total_normalized_facts for m in metrics)
            print("=" * 110)
            print(f"AGGREGATE  tables={total_t}  classified={total_cls}/{total_t} ({total_cls/max(total_t,1):.0%})"
                  f"  fact_cov={avg_fact:.1%}  row_key_cov={avg_rk:.1%}  col_map={avg_cm:.1%}  facts={total_nf}")
            print("=" * 110)
