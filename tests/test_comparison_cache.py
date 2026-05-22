from pathlib import Path

from grc_policy_server.models.schemas import ComparisonResult
from grc_policy_server.services.comparison.comparison_cache import ComparisonCacheStore


def test_comparison_cache_round_trip(tmp_path: Path):
    store = ComparisonCacheStore(upload_root=tmp_path)
    result = ComparisonResult(
        summary="summary",
        keyDifferences=[],
        actionPlan=[],
        followUpQuestions=[],
    )

    key = store.save_for_pair(doc1_id="doc-a", doc2_id="doc-b", result=result)
    loaded = store.load_for_pair(doc1_id="doc-a", doc2_id="doc-b")

    assert loaded is not None
    assert loaded.summary == "summary"

    cached_job_id = store.cached_job_id_for_pair(doc1_id="doc-a", doc2_id="doc-b")
    assert cached_job_id == f"cached-{key}"
    loaded_from_job = store.load_for_cached_job(job_id=cached_job_id)
    assert loaded_from_job is not None
    assert loaded_from_job.summary == "summary"
