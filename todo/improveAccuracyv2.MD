# Improve comparison

If your comparison engine relies heavily on content retrieved back from Weaviate instead of from a structured canonical document store, that is a major source of drift.

Fix

Use Weaviate for:

chat retrieval
semantic candidate matching
“find related clause” style tasks

Do not use it as the canonical comparison substrate.

the weak points are usually these:

1. doc structure fidelity is degraded during chunking
2. section-to-section alignment is too coarse or too brittle
3. diff evidence sent to the LLM is incomplete or already lossy
4. tables / lists / notes / moved clauses are being flattened
5. citations are attached after summarization rather than driving it


 focus.

1. What is likely going wrong in your current version
1.1 Chunking is probably destroying comparison truth

Hierarchical chunking is useful for retrieval, but dangerous as the source of truth for document comparison.

Typical losses:

- one clause split across multiple chunks
- heading context separated from paragraph
- bullet lists flattened into text
- tables converted into badly ordered text
- subsection numbers lost or normalized inconsistently
- notes, exceptions, and annex references detached from parents

So by the time you compare “sections,” you are not really comparing the original regulatory unit anymore. You are comparing an approximation.

# Symptom

The summary sounds plausible but misses precise changes, invents scope, or fails to explain why a change matters.

# Fix

Do not use chunked vector-store text as the comparison backbone.
Use a document node tree as the backbone, and use chunking only for retrieval/chat.

1.2 “Section-to-section between document IDs” is too weak as alignment logic

A lot of standards revisions do not preserve clean section equivalence.

Examples:

- 5.2 becomes 5.3.1 + 5.3.2
- one clause is moved under a new chapter
- wording changes but intent remains
- tables are updated while the paragraph looks similar
- definitions move to glossary/annex
- scope shifts through one sentence in the intro or applicability section
- If comparison is based mostly on section numbering or title matching, accuracy will drop fast.

# Symptom
- false deletions/additions
- missed modifications
- clauses marked as changed when they were only moved
- summaries that overstate impact

# Fix

Introduce a proper alignment stage before diffing:
- structural similarity
- heading similarity
- semantic similarity
- parent lineage similarity
- table/formula similarity
- split/merge detection
- 1.3 You are likely passing only “key diffs” to the LLM

That phrase is a red flag.

If the system first compresses or filters raw diffs into “key diffs,” then asks the LLM to summarize, the LLM is reasoning over a partial snapshot of the evidence.

That means:

important context may already be gone
significance decisions may be made too early
the LLM cannot recover omitted evidence
# Symptom

The summary is accurate for a few changes but misses high-impact changes elsewhere.

# Fix
 
Insert a change-record layer between diffing and summarization.

The LLM should receive structured evidence like:

- source node id
- target node id
- change type
- exact before/after text
- numerical changes
- table cell changes
- confidence
- citations
- surrounding heading context
- impact category candidates

Not just “key diffs.”


Keep your current stack

You can keep:

FastAPI
Celery
Next.js
Docling
Weaviate

But change the responsibilities.

New roles
Docling: extraction source
PostgreSQL / document store: canonical structured document store
Weaviate: semantic retrieval only
Comparison engine: works on canonical nodes, not chunks
LLM: explanation only, not factual diff determination
3.2 Add a canonical document model

Right now you likely store chunks and metadata.
You need to store document nodes.

Suggested minimum node schema
{
  "node_id": "docA:v3:sec_5.2:p_3",
  "document_id": "docA",
  "version_id": "v3",
  "parent_id": "docA:v3:sec_5.2",
  "node_type": "paragraph",
  "section_label": "5.2",
  "heading_path": ["Requirements", "Safety Controls", "Operational Limits"],
  "order_index": 103,
  "raw_text": "...",
  "normalized_text": "...",
  "page_from": 17,
  "page_to": 17,
  "bbox_refs": [...],
  "language": "en",
  "source_kind": "body"
}

Also support:

list_item
table
figure
formula
note
warning
definition

This becomes your comparison truth.

3.3 Split “chunking” into two separate concepts

Right now you likely have one chunking strategy used for everything. Split it.

A. Comparison units

These are stable structural nodes:

section
subsection
paragraph
list item
table row
formula
note

Used for comparison.

B. Retrieval chunks

These are RAG-friendly chunks:

300 to 1200 tokens
parent-child merged when useful
overlap allowed
semantic grouping allowed

Used only for chat/retrieval.

This separation alone usually improves quality substantially.

3.4 Add a normalization layer after Docling

Before anything is chunked or embedded, normalize the extracted structure.

Normalize:
numbering forms (5.2, 5-2, Clause 5.2)
whitespace/punctuation
bullet hierarchy
heading levels
table headers
note/example/warning labels
glossary term formatting
page/citation anchors
language tags
Preserve:
original raw text
exact source ordering
location metadata

You want both:

raw fidelity for citation
normalized text for comparison
3.5 Add an alignment engine before diffing

This is the biggest missing capability in most v1 implementations.

For each node in source version, generate candidate matches in target version using:

Alignment signals
exact or near section number match
heading similarity
semantic embedding similarity
parent path similarity
position proximity within chapter
node type compatibility
table schema similarity
formula similarity

Then choose:

matched
added
removed
moved
split
merged
uncertain
Important

Do this before textual diff.

Text diff without alignment gives bad results.


Do significance scoring before LLM summarization

Right now the LLM may be deciding importance from partial evidence. That is risky.

Add a pre-LLM significance layer.

Rules can include:
numeric threshold changed
requirement verbs changed: “shall” / “must” / “may”
scope/applicability changed
safety/test limits changed
new reporting obligation added
exemption removed
definition changed
table value changed
mandatory annex changed

Then assign:

editorial
low
medium
high
critical

Use this both for audit and general mode.

3.8 Treat tables separately

If tables are being flattened into paragraphs, you are losing a lot.

For each table:

store:

caption
table id
headers
row keys
cell matrix
units
page refs

Then compare:

header changes
row additions/deletions
cell changes
unit changes
threshold changes

Do not leave this to generic text diff.

3.9 Improve the LLM input contract

Instead of:

“Here are some key diffs, summarize them”

use:

“Here are structured change records with exact citations, change type, confidence, and impact candidates. Produce either exhaustive audit mode or significance-filtered general mode. Do not infer unsupported changes.”

The model input should contain
document metadata
comparison objective
mode: audit/general
grouped change records by chapter
per-change citations
significance labels
unresolved/uncertain alignments

That makes summaries much more stable.

4. Where to instrument your current pipeline to locate loss

You said it is “not clear what, where something is lost.”
You need observability at each transformation boundary.

Add a debug trace per compare job.

4.1 Capture these checkpoints
Checkpoint A: raw extracted structure

Store:

docling JSON
extracted headings
block order
tables found
lists found
page anchors
Checkpoint B: normalized node tree

Store:

node count by type
hierarchy depth
section labels
node ordering
dropped/merged blocks count
Checkpoint C: retrieval/index artifacts

Store:

retrieval chunk count
chunk-to-node mapping
embedding metadata
Checkpoint D: alignment results

Store:

matched nodes
unmatched nodes
low-confidence matches
splits/merges detected
Checkpoint E: diff records

Store:

change counts by type
numeric changes
table changes
moved clauses
confidence distribution
Checkpoint F: LLM input payload

Store:

exact structured payload sent
prompt version
token counts
included change record ids
Checkpoint G: final summary

Store:

which change records were referenced
which were omitted
citation coverage

This gives you a loss map.
