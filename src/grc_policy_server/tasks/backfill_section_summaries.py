from __future__ import annotations

import logging
from pathlib import Path

from grc_policy_server.core.celery_app import celery_app
from grc_policy_server.core.config import settings
from grc_policy_server.services.graph.graph_neo4j_client import Neo4jClient, Neo4jSettings
from grc_policy_server.services.ingestion.section_summary_backfill import (
    SectionSummaryBackfillService,
)
from grc_policy_server.services.vector.weaviate_client import WeaviateClient

logger = logging.getLogger(__name__)


@celery_app.task(name="grc_policy_server.tasks.backfill_section_summaries")
def backfill_section_summaries() -> dict[str, int]:
    weaviate: WeaviateClient | None = None
    try:
        weaviate = WeaviateClient()
    except Exception:
        logger.warning("Weaviate unavailable — backfill will skip vector upsert")
    neo4j: Neo4jClient | None = None
    if settings.neo4j_enabled:
        neo4j = Neo4jClient(
            Neo4jSettings(
                uri=settings.neo4j_uri,
                user=settings.neo4j_user,
                password=settings.neo4j_password,
                database=settings.neo4j_database,
            )
        )

    try:
        service = SectionSummaryBackfillService(
            upload_root=Path(settings.upload_root),
            weaviate=weaviate,
            neo4j=neo4j,
        )
        result = service.backfill_all()
        return {
            "documents_seen": result.documents_seen,
            "documents_updated": result.documents_updated,
            "documents_skipped": result.documents_skipped,
            "section_nodes_updated": result.section_nodes_updated,
            "vector_records_upserted": result.vector_records_upserted,
        }
    finally:
        try:
            if weaviate is not None:
                weaviate.close()
        except Exception:
            logger.exception("failed to close Weaviate client in backfill task")
        try:
            if neo4j is not None:
                neo4j.close()
        except Exception:
            logger.exception("failed to close Neo4j client in backfill task")
