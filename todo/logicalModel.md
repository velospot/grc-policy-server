4. Logical data model
4.1 Core entities
# Document
  document_id
  tenant_id
  title
  domain
  standard_family
  source_type
  jurisdiction
  language
  publication_date
  effective_date
  revision_label
  status
# DocumentVersion
  version_id
  document_id
  version_number
  release_date
  prior_version_id
  parser_version
  extraction_confidence
  checksum
# DocumentNode
  node_id
  version_id
  parent_node_id
  node_type
  numbering_path
  title
  raw_text
  normalized_text
  canonical_text
  language
  page_from
  page_to
  citation_anchor
  bbox_refs
  embedding_ref
# TableObject
  table_id
  node_id
  caption
  schema_json
  cell_matrix_json
  header_map
  page_refs
# FigureObject
  figure_id
  node_id
  caption
  vision_summary
  page_refs
# FormulaObject
  formula_id
  node_id
  latex_repr
  mathml_repr
  normalized_formula
  symbols_json
# GlossaryTerm
  term_id
  version_id
  term
  expansion
  synonyms
  language
# ChangeRecord
  change_id
  comparison_id
  source_node_id
  target_node_id
  change_type
  structural_relation
  lexical_delta
  semantic_delta
  impact_score
  significance_band
  impact_category
  rationale
  confidence
  citations_json
# ComparisonJob
  comparison_id
  source_version_id
  target_version_id
  comparison_policy_id
  status
  created_by
  completed_at
# ReportArtifact
  report_id
  comparison_id
  report_mode
  generation_params
  output_uri
  created_at
# ChatSession
  session_id
  tenant_id
  user_id
  scope_json
  created_at
# ChatTurn
  turn_id
  session_id
  question
  retrieved_evidence_json
  answer
  citations_json
  model_info
