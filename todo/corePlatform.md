3.2 Core platform services
1. Identity and access service

Responsibilities:

SSO
RBAC
tenant isolation
data permissions by document, workspace, domain, region
2. Document ingestion service

Responsibilities:

upload handling
metadata extraction
version registration
checksum and duplicate detection
job orchestration kickoff
3. Document intelligence service

Responsibilities:

PDF/DOCX/image parsing
OCR
layout extraction
table extraction
figure/chart extraction
formula extraction
abbreviation/glossary detection
structural reconstruction into nodes
4. Document normalization service

Responsibilities:

language detection
normalization of section numbering
multilingual alignment
text cleanup
canonical semantic representation
clause identity assignment
5. Document repository service

Responsibilities:

maintain document tree
manage citations
store parsed artifacts
expose query APIs for structure-aware retrieval
6. Comparison engine

Responsibilities:

candidate matching
structural alignment
text diff
semantic diff
table diff
numeric/formula diff
split/merge/move detection
impact classification
7. Significance scoring service

Responsibilities:

decide whether a change is editorial, moderate, or high impact
domain-specific weighting
explainability rules
8. Reasoning service

Responsibilities:

produce grounded explanations
convert evidence into audit-mode and general-mode narratives
generate impact summaries
answer chat questions using retrieved evidence
9. Retrieval service

Responsibilities:

hybrid retrieval
structural search
semantic search
filtering by version, domain, language, section, change type
10. Reporting service

Responsibilities:

render audit reports
render executive/general summaries
export PDF, DOCX, XLSX, JSON
11. Workflow and configuration service

Responsibilities:

report templates
domain ontologies
significance policies
review/approval workflow
notification rules
parser/model configuration
12. Audit and observability service

Responsibilities:

user action logging
model/prompt versioning
evidence traceability
comparison reproducibility
latency/errors/cost tracking


Offline comparison pipeline (optimized)
Step-by-step
Step 1: Parse document
OCR (if needed)
structure extraction
table detection
figure extraction
formula extraction
Step 2: Normalize
unify structure
expand abbreviations
multilingual alignment
embeddings generation
Step 3: Align versions
structural + semantic matching
Step 4: Diff
text diff
table diff
formula diff
structure diff
Step 5: Impact scoring
rule-based + ML-assisted
Step 6: Explanation (LLM)
strictly grounded
uses:
aligned nodes
change records
glossary
