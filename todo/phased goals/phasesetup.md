Suggested storage split
PostgreSQL

Store:

  documents
  versions
  nodes
  tables
  figures metadata
  comparison jobs
  alignment candidates
  change records
  summaries
  citations
  Weaviate

Store:

  retrieval chunks
  node embeddings
  semantic search objects
  chat retrieval artifacts
  Object storage

Store:

  original files
  docling JSON
  rendered compare outputs
  debug snapshots
7. Minimal revised pipeline for your current implementation

This is the version I would build next from what you already have.

Upload flow

upload -> celery -> docling extraction -> normalization -> canonical node tree -> save nodes in postgres -> generate retrieval chunks -> embed -> save in weaviate

Compare flow

compare request -> celery -> load canonical nodes from postgres -> alignment engine -> node-level diffs -> table/formula/numeric diffs -> change record generation -> significance scoring -> LLM summarization -> persist audit/general reports
