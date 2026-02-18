from grc_policy_server.services.comparision.real_diff_engine import RealDiffEngine
from grc_policy_server.services.llm.base import BaseLLM


class DiffService:
    def __init__(self, llm: BaseLLM):
        self.llm = llm

    async def compare_and_summarize(self, doc1, doc2):

        diffs = RealDiffEngine().compare(doc1.content, doc2.content)

        key_differences = [d.content for d in diffs if d.type in {"added", "removed"}]

        summary_text = await self.llm.summarize_changes(
            doc1.id,
            doc2.id,
            key_differences,
        )

        return {
            "diffs": diffs,
            "summary": summary_text,
        }
