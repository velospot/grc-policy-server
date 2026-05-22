1. Product goal

Build a platform that can:

ingest multilingual compliance and regulatory documents across versions
understand rich structure: chapters, clauses, tables, formulas, figures, abbreviations, annexes
compare two versions or two related standards
generate two levels of outputs:
Audit mode: exhaustive, clause-level, citation-backed additions/deletions/modifications
General mode: only significant or high-impact changes with interpretation
support a chat interface for follow-up questions grounded in the uploaded corpus

The platform should also remain adaptable as requirements change. That means the architecture must be schema-flexible, workflow-configurable, and model-agnostic.

2. Product architecture at a glance

Think in six layers:

Document ingestion layer
Document intelligence / parsing layer
Semantic normalization layer
Comparison and change-detection engine
Reasoning and explanation layer
Experience layer for reports, chat, and governance workflows

A clean separation between these layers is what makes the system evolvable.

3. Core domain model

Before choosing tools or models, define a stable internal representation. This is the most important architectural choice.

Key entities
Document
id
title
language
domain
source type
publication date
revision number
jurisdiction
standard family
upload metadata
DocumentVersion
version id
effective date
status: draft / active / superseded
checksum
parser version used
DocumentNode
node id
parent id
node type: chapter / section / subsection / paragraph / bullet / table / figure / formula / annex / glossary
numbering path
raw text
normalized text
page range
bounding box references
language
citation anchor
TableObject
table id
rows/columns/cells
header inference
merged-cell semantics
extracted captions
source page refs
FigureObject
figure id
caption
OCR/vision summary
linked page refs
ChangeRecord
change id
source node
target node
change type: added / removed / modified / moved / split / merged
significance score
semantic impact score
rationale
citations
ControlMapping
regulation clause → internal control / risk / obligation / requirement
ConversationContext
chat session id
user role
accessible documents
retrieved evidence
system reasoning metadata

This gives you an internal graph that can survive changing requirements much better than raw text chunks.

4. Ingestion pipeline

Users upload PDFs, DOCX, scanned documents, spreadsheets, image-based standards, or mixed-format revisions.

Pipeline stages
A. File intake
detect format
virus scan
metadata extraction
checksum/version identification
language detection
duplicate detection
B. Parsing orchestration

Use multiple parsers depending on file type and quality:

native PDF parser for embedded text
OCR for scans
layout-aware parser for structure
table extraction engine
vision model for figures/charts/diagrams
formula extractor for equations
C. Structural reconstruction

Convert parsed output into the unified DocumentNode tree:

chapter hierarchy
section numbering
paragraphs and bullets
tables with row/column semantics
formulas as structured expressions
abbreviations/glossary mapping
figure captions and references
D. Normalization
normalize numbering styles
resolve multilingual heading patterns
standardize whitespace, punctuation, Unicode
identify abbreviations and expand them in semantic view
preserve original text for citation fidelity
E. Version registration

If the document is a revision of an existing standard:

link to prior version
classify as new revision or different family
trigger comparison jobs
5. Why parsing is the hardest part

Your initial requirements include tables, images, formulas, abbreviations, multilingual content, and subsections. That means plain text extraction is not enough.

The platform must support multi-representation ingestion:

Text representation for retrieval and generation
Structural representation for clause comparison
Layout representation for citations and highlighting
Vision representation for charts/figures
Tabular representation for matrix comparisons
Mathematical representation for formulas/equations

So each document node should store:

original content
normalized content
embeddings
layout coordinates
semantic tags
domain tags

That is the basis for trustworthy citations and diffing.

6. Comparison engine

This should not be a naive textual diff. Regulatory and compliance documents often change by:

renumbering sections
moving clauses
splitting one clause into many
merging several clauses into one
rewording while preserving intent
changing thresholds inside tables or formulas
changing scope definitions
updating obligations in notes or annexes
Comparison pipeline
Stage 1: Candidate alignment

Find likely matches between source and target nodes using:

numbering similarity
heading similarity
semantic embedding similarity
layout proximity
parent-child structural context

This produces candidate pairs.

Stage 2: Structural alignment

Detect:

moved clauses
split/merge patterns
deleted/added nodes
reordered bullets
table row/column changes
Stage 3: Content-level change detection

For aligned nodes:

lexical diff
semantic diff
numeric diff
formula diff
glossary/abbreviation diff
table cell diff
Stage 4: Impact analysis

Classify changes:

editorial
definitional
threshold change
scope change
control obligation change
evidence requirement change
testing procedure change
safety/risk change
Stage 5: Explanation generation

Use the LLM only after the diff engine has produced grounded evidence. The LLM should explain:

what changed
why it matters
who is affected
whether it introduces new control obligations
whether testing or documentation must change

This keeps the system precise.

7. Audit mode vs General mode

This should be a policy-driven reporting layer, not two separate engines.

Audit mode

Exhaustive, evidence-heavy, clause-level.

Output includes:

every addition, deletion, modification
old text vs new text
semantic explanation
classification of change type
citations to exact clauses/pages/tables
confidence level
unresolved ambiguities
possible downstream control impact

Good for:

auditors
compliance officers
legal reviewers
certification teams
General mode

Business-facing summary of meaningful changes only.

Output includes:

top impactful changes
grouped by theme
summary of effect on compliance obligations
reduced noise
citations to major changed sections
optional visual highlighting

Good for:

engineering managers
product owners
plant managers
program leads
Important design choice

Both modes should consume the same ChangeRecord graph, but apply different:

thresholds
grouping rules
explanation templates
evidence verbosity

That keeps outputs consistent.
