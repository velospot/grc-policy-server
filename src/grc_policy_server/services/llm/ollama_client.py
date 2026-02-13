import httpx

OLLAMA_URL = "http://ollama:11434"


class OllamaClient:
    async def embed(self, text: str) -> list[float]:
        async with httpx.AsyncClient(timeout=60) as client:
            res = await client.post(
                f"{OLLAMA_URL}/api/embeddings",
                json={"model": "nomic-embed-text", "prompt": text},
            )
            return res.json()["embedding"]

    async def summarize_diff(self, old: str, new: str) -> str:
        prompt = f"""
        Compare the following policy clauses and describe compliance impact.

        OLD:
        {old}

        NEW:
        {new}
        """
        async with httpx.AsyncClient(timeout=120) as client:
            res = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={"model": "llama3.1", "prompt": prompt},
            )
            return res.json()["response"]
