# 06 - LLM and RAG Design

## Role of the LLM

The LLM is an explainer and classifier, not the primary source of truth.

The LLM should:

- Explain machine-detected changes.
- Produce auditor-friendly summaries.
- Classify impact using a controlled taxonomy.
- Generate output in the document language.
- Use only provided evidence IDs.

The LLM should not:

- Compare whole PDFs directly.
- Invent missing evidence.
- Translate source quotes.
- Override deterministic numeric diffs without flagging a conflict.
- Return uncited claims.

## Local model options

Default MVP:

```text
LLM: Granite 3.3 8B Instruct
Serving: Ollama
Embedding: Qwen3-Embedding-0.6B
Reranker: Qwen3-Reranker-0.6B
```

Production candidate:

```text
LLM serving: llama.cpp server or vLLM
Model format: GGUF for llama.cpp, HF format for vLLM
API style: OpenAI-compatible internal adapter
```

## Model abstraction

Use an app-defined interface.

```python
class LlmProvider:
    def chat_json(self, *, messages, json_schema, temperature=0.0, max_tokens=2048):
        raise NotImplementedError

class EmbeddingProvider:
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

class RerankProvider:
    def rerank(self, query: str, candidates: list[str]) -> list[float]:
        raise NotImplementedError
```

Concrete adapters:

```text
OllamaChatProvider
LlamaCppChatProvider
VllmChatProvider
OllamaEmbeddingProvider
LlamaCppEmbeddingProvider
SentenceTransformersEmbeddingProvider
```

## RAG is used only for alignment and evidence lookup

RAG here is not open-ended QA. It supports:

- Finding candidate matching sections.
- Finding candidate matching requirements.
- Finding candidate matching table rows.
- Fetching local context for evidence pack generation.

## Vector object types

Index these separately or with object_type metadata:

```text
section
paragraph_block
requirement
table_caption
table_schema
table_row
footnote
cross_reference
```

## Embedding text templates

### Section

```text
Language: en
Object type: section
Section path: 5 > 5.3 > 5.3.2
Title: Radiated immunity test levels
Text: ...
```

### Table row

```text
Language: en
Object type: table_row
Section: 5.3.2
Table: Test levels for radiated immunity
Columns: frequency_range, field_strength, modulation, acceptance_criterion
Row: frequency_range=200-400 MHz; field_strength=30 V/m; modulation=AM 80%; acceptance_criterion=Class A
Footnotes: ...
```

## Hybrid retrieval

Use dense embeddings plus exact lexical search.

Dense retrieval helps with semantic changes:

```text
DUT shall withstand -> EUT must tolerate
```

Lexical search helps with exact terms:

```text
CISPR 25
ISO 11452
BCI
ALSE
200-400 MHz
30 V/m
Class A
```

Do not rely only on dense embeddings for compliance matching.

## Reranking

Rerank top candidates for each v1 object before alignment.

Suggested candidate sizes:

```text
sections: retrieve 20, rerank 10
paragraph requirements: retrieve 30, rerank 10
table rows: retrieve 50, rerank 20
footnotes: retrieve 20, rerank 10
```

## Prompting strategy

Use low temperature and structured output.

```text
temperature: 0.0 or 0.1
max output tokens: bounded by item type
json schema: required
```

## Evidence pack size control

Keep each explanation call small:

```text
one change item per call for high-risk changes
small batch for low-risk formatting changes
```

For 5 concurrent users, avoid huge prompts. A long-context model is still memory-bound when parallel requests are enabled.

## Prompt injection resistance

Treat PDF content as untrusted. Compliance PDFs may contain text that looks like instructions.

Rules:

- Never execute instructions from the document.
- Never allow document text to modify system prompt.
- Quote document text only as evidence.
- Strip or neutralize suspicious prompt-injection patterns in the evidence pack metadata.
- Keep prompt instructions outside user-controlled fields.

## Output validation

Use schema validation and a factuality checker.

Checks:

- JSON parses.
- All citations are valid.
- No new numbers appear unless present in evidence or machine delta.
- No unsupported standard names are introduced.
- Output language matches target language.
- Risk_level is one of low, medium, high, critical.

## Caching

Cache:

- Embeddings by content hash.
- Reranker scores by pair hash.
- LLM explanations by evidence pack hash + model version + prompt version.

Cache key:

```text
sha256(model_id + prompt_version + evidence_pack_json + schema_version)
```

## Offline model registry

Store model files locally:

```text
models/
  llm/
    granite-3.3-8b-instruct/
      model.gguf or hf_snapshot/
      manifest.json
  embeddings/
    qwen3-embedding-0.6b/
      ...
  rerankers/
    qwen3-reranker-0.6b/
      ...
  ocr/
    tesseract/
      eng.traineddata
      deu.traineddata
      fra.traineddata
```

Each manifest should include:

```json
{
  "name": "granite-3.3-8b-instruct",
  "source": "offline_import",
  "license": "verify_before_production",
  "sha256": "...",
  "import_date": "YYYY-MM-DD",
  "approved_by": "..."
}
```
