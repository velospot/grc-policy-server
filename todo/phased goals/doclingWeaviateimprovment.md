 build a canonical node tree from Docling output

For each document, persist:

sections
subsections
paragraphs
list items
notes
tables
figures
formulas if possible

This is separate from chunk storage.
build a canonical node tree from Docling output

For each document, persist:

sections
subsections
paragraphs
list items
notes
tables
figures
formulas if possible

This is separate from chunk storage.

Step 3: store tables twice
once as structured rows/cells for comparison
once as markdown for retrieval
Step 4: compare at node level, not chunk level

Do:

section-to-section candidate alignment
paragraph/list/table node alignment inside matched sections
independent table diff
Step 5: generate structured change records

Before the LLM, create:

added/removed/modified/moved records
source and target citations
numeric changes
table cell changes
confidence
Step 6: feed the LLM only structured change records

Not raw chunk diffs and not retrieved markdown blobs.

A concrete representation split

For each document version, store this:

In Postgres
document_nodes
node_id
document_id
version_id
parent_id
node_type
section_label
heading_path
order_index
raw_text
normalized_text
page_from
page_to
document_tables
table_id
node_id
caption
page_from
page_to
markdown_render
table_json
comparison_alignments
source_node_id
target_node_id
match_score
match_reason
alignment_type
change_records
source_node_id
target_node_id
change_type
diff_json
significance
citations_json
In Weaviate
retrieval chunks
semantic embeddings
normalized text objects
markdown table text objects
maybe node-level embeddings too

That gives you the right separation.

One important warning about normalization

Since you said you save normalized text, review your normalization rules carefully.

Do not normalize away:

section numbers
bullet numbering
modal verbs: shall, must, may, should
negation
units
decimal precision
mathematical symbols
note/warning labels
annex references
exceptions

For comparison, you want:

light normalization for alignment
raw or near-raw text for legal meaning and citations

If normalization is too aggressive, you may be losing the exact meaning before diffing even starts.

The likely immediate fix with highest ROI

If you want the single most effective change from where you are now:

Introduce a canonical node-and-table store outside Weaviate, and make the comparison engine operate only on that structured store.

Keep Weaviate for retrieval and chat.

That one change usually clarifies where the loss happens and sharply improves accuracy.

My direct diagnosis

Saving normalized text and markdown tables in Weaviate is not wrong.
It is just not sufficient for a compliance comparison system because:

Weaviate storage format is retrieval-oriented
markdown tables are presentation-oriented
comparison needs structure-oriented evidence

So the loss is probably not that your system “does not have the information.”
It is that the information is being flattened too early and then compared too late.
