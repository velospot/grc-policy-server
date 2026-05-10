10. Tables, charts, images, formulas

These are not edge cases in compliance content. They are first-class content types.

Tables

Need special logic for:

threshold changes
newly added columns
row deletions
merged-cell reinterpretation
unit changes
header structure changes

Example:
A numeric tolerance moving from ±10% to ±5% is far more important than many paragraph edits.

Charts and images

Use vision models to extract:

captions
labels
referenced thresholds
flow relationships
testing sequences

Do not overtrust image summaries. Mark confidence and always cite the source region.

Formulas

Need math-aware comparison:

tokenization
variable mapping
threshold/value detection
equation-structure change detection

A small formula change can have major regulatory impact.

11. Citations and trust model

Citations are central to this product. Every insight should be traceable.

Citation model

Each answer or report item should link to:

document id
version
section number
page number
bounding box or anchor
table/figure id if relevant
Evidence levels
Direct: exact clause/table/figure citation
Derived: supported by aligned nodes and structured diff
Inferred: LLM interpretation over grounded evidence

This distinction should be visible in the UI. It will matter for auditors.

12. Evolving requirements: how to design for change

This is the architectural center of your question.

Requirements will change. Industries will expand. Reporting styles will evolve. You do not want a hardcoded monolith.

Use these design principles
A. Plugin-based ingestion

Each parser or extractor is a pluggable component:

pdf text parser
OCR parser
table parser
vision parser
formula parser
domain-specific parser

So if tomorrow you onboard aerospace, pharma, or medical device standards, you add extractors instead of rewriting the system.

B. Configurable comparison policies

Different industries care about different change signals.

Example:

automotive: testing thresholds, process validation, safety clauses
manufacturing: procedural changes, equipment requirements
EMV: limit values, waveforms, test setups

Store comparison rules in configuration:

significance thresholds
node weighting
change type priorities
domain-specific ontologies
