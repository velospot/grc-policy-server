# SQL Schema Blueprint

This is a simplified blueprint. Use Alembic migrations in implementation.

```sql
CREATE TABLE projects (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE documents (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(id),
    filename TEXT NOT NULL,
    release_label TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    storage_uri TEXT NOT NULL,
    page_count INTEGER,
    language_code TEXT,
    language_confidence DOUBLE PRECISION,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(project_id, sha256)
);

CREATE TABLE extraction_runs (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id),
    extractor_name TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    cir_version TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    raw_output_uri TEXT,
    cir_snapshot_uri TEXT,
    error_message TEXT
);

CREATE TABLE pages (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id),
    page_number INTEGER NOT NULL,
    width DOUBLE PRECISION,
    height DOUBLE PRECISION,
    language_code TEXT,
    has_native_text BOOLEAN,
    ocr_used BOOLEAN,
    render_uri TEXT,
    UNIQUE(document_id, page_number)
);

CREATE TABLE sections (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id),
    parent_id UUID REFERENCES sections(id),
    section_number TEXT,
    title TEXT,
    normalized_title TEXT,
    path_text TEXT,
    page_start INTEGER,
    page_end INTEGER,
    language_code TEXT,
    confidence DOUBLE PRECISION
);

CREATE TABLE blocks (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id),
    section_id UUID REFERENCES sections(id),
    page_number INTEGER NOT NULL,
    reading_order INTEGER,
    block_type TEXT NOT NULL,
    text TEXT,
    bbox JSONB,
    language_code TEXT,
    source_extractor TEXT,
    confidence DOUBLE PRECISION
);

CREATE TABLE tables (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id),
    section_id UUID REFERENCES sections(id),
    caption TEXT,
    normalized_caption TEXT,
    page_start INTEGER,
    page_end INTEGER,
    continued_from_previous BOOLEAN DEFAULT false,
    continued_to_next BOOLEAN DEFAULT false,
    confidence DOUBLE PRECISION
);

CREATE TABLE table_columns (
    id UUID PRIMARY KEY,
    table_id UUID NOT NULL REFERENCES tables(id),
    column_index INTEGER NOT NULL,
    raw_header TEXT,
    normalized_name TEXT,
    unit TEXT,
    header_path JSONB,
    is_key_column BOOLEAN DEFAULT false
);

CREATE TABLE table_rows (
    id UUID PRIMARY KEY,
    table_id UUID NOT NULL REFERENCES tables(id),
    row_index INTEGER NOT NULL,
    semantic_key TEXT,
    page_number INTEGER,
    bbox JSONB,
    cells JSONB NOT NULL,
    normalized_facts JSONB,
    confidence DOUBLE PRECISION
);

CREATE TABLE requirements (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id),
    section_id UUID REFERENCES sections(id),
    source_object_type TEXT NOT NULL,
    source_object_id UUID,
    language_code TEXT,
    normative_level TEXT,
    normative_term TEXT,
    raw_text TEXT,
    normalized_text TEXT,
    confidence DOUBLE PRECISION
);

CREATE TABLE citations (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id),
    source_object_type TEXT NOT NULL,
    source_object_id UUID,
    page_number INTEGER NOT NULL,
    bbox JSONB,
    display_label TEXT,
    quote TEXT,
    confidence DOUBLE PRECISION
);

CREATE TABLE index_objects (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id),
    source_object_type TEXT NOT NULL,
    source_object_id UUID NOT NULL,
    language_code TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    vector_backend_id TEXT,
    indexed_at TIMESTAMPTZ
);

CREATE TABLE comparisons (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(id),
    left_document_id UUID NOT NULL REFERENCES documents(id),
    right_document_id UUID NOT NULL REFERENCES documents(id),
    language_code TEXT NOT NULL,
    status TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);

CREATE TABLE change_items (
    id UUID PRIMARY KEY,
    comparison_id UUID NOT NULL REFERENCES comparisons(id),
    change_key TEXT NOT NULL,
    change_type TEXT NOT NULL,
    risk_level TEXT,
    section_label TEXT,
    title TEXT,
    summary TEXT,
    impact TEXT,
    confidence DOUBLE PRECISION,
    requires_human_review BOOLEAN DEFAULT false,
    review_state TEXT DEFAULT 'unreviewed',
    machine_delta JSONB,
    llm_output JSONB
);

CREATE TABLE evidence_items (
    id UUID PRIMARY KEY,
    change_item_id UUID NOT NULL REFERENCES change_items(id),
    side TEXT NOT NULL,
    citation_id UUID NOT NULL REFERENCES citations(id),
    evidence_id TEXT NOT NULL,
    quote TEXT
);

CREATE TABLE llm_runs (
    id UUID PRIMARY KEY,
    change_item_id UUID REFERENCES change_items(id),
    model_id TEXT NOT NULL,
    runtime TEXT NOT NULL,
    prompt_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    output_hash TEXT,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error_message TEXT
);

CREATE TABLE audit_events (
    id UUID PRIMARY KEY,
    project_id UUID REFERENCES projects(id),
    actor_user_id UUID,
    event_type TEXT NOT NULL,
    entity_type TEXT,
    entity_id UUID,
    payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Recommended indexes:

```sql
CREATE INDEX idx_documents_project ON documents(project_id);
CREATE INDEX idx_sections_document ON sections(document_id);
CREATE INDEX idx_blocks_document_section ON blocks(document_id, section_id);
CREATE INDEX idx_tables_document_section ON tables(document_id, section_id);
CREATE INDEX idx_table_rows_table ON table_rows(table_id);
CREATE INDEX idx_requirements_document ON requirements(document_id);
CREATE INDEX idx_citations_document_page ON citations(document_id, page_number);
CREATE INDEX idx_change_items_comparison ON change_items(comparison_id);
CREATE INDEX idx_audit_events_project_time ON audit_events(project_id, created_at);
```
