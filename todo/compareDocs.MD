## To Do

1. Compare should happen in multipe steps as follow

- first compare sections between two docs and if sections match, then look for semantic change in the contents.
- sometimes the docs may have a different section number or depth, yet contents may match to the maximum with little changes to section title or text. contents in the sense can be tables, paragraphs, bullet points, formulas, equations.
- compare table normalized captions/title and then contents.

2. Categorize the changeType as medium, high, low and update as a property if not exists in keyDifferences
  - medium : medium degree of change in semantic meaning. example 35-60% change in meaning 
  - high : high degree of change in semantic meaning between sections, section contents, tables, points. 
    example greater than 60% change in meaning
  - low : little change like formating, breaks, paraphrase changes,cosmetic, non-semantic changes

  
  Comparison blueprint
  6.1 Matching strategy
  
  Use layered matching:
  
  Layer 1: structural hints
  section number similarity
  heading similarity
  parent lineage similarity
  Layer 2: semantic hints
  multilingual embedding similarity
  glossary-aware term overlap
  domain concept overlap
  Layer 3: special content matching
  table schema/header similarity
  formula token similarity
  figure caption similarity
  
  This produces alignment candidates.
  
  6.2 Diff strategy
  Text nodes
  word/sentence diff
  semantic meaning change
  numeric/threshold detection
  Tables
  header changes
  row addition/removal
  cell value changes
  unit changes
  threshold changes
  Formulas
  variable changes
  operator changes
  constants changed
  expression structure changed
  Structure
  clause moved
  split or merged clause
  renumbered section
  annex promoted to main section, etc.
  6.3 Impact classification
  
  Recommended categories:
  
  editorial
  terminology/definition
  scope/applicability
  obligation/control
  evidence/documentation
  testing/verification
  threshold/limit
  risk/safety impact
  reporting/compliance submission
