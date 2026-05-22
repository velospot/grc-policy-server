Phase 1: stabilize the current architecture
Goal

Stop loss before summarization.

Changes
keep Docling
add canonical node schema in PostgreSQL
store raw docling JSON + normalized node tree
separate comparison nodes from retrieval chunks
keep Weaviate only for retrieval/chat
compare canonical nodes, not chunks
create explicit change records
send structured change records to LLM
Immediate likely outcome

Much better traceability and improved summary accuracy.
