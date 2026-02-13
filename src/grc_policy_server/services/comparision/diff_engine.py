from grc_policy_server.services.llm.ollama_client import OllamaClient


class DiffEngine:
    def __init__(self):
        self.llm = OllamaClient()

    async def compare(self, old_chunk, new_chunk):
        summary = await self.llm.summarize_diff(old_chunk["text"], new_chunk["text"])
        return {"old": old_chunk["text"], "new": new_chunk["text"], "summary": summary}
