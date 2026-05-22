# Improve table comparison

1. During table comparision, categorising high, low, medium should not just be on count of rows and columns, rather also based on the content.
2. Docling extraction of tables may not be sometimes different structure. E.g table caption/ table title will be part of table row or a cell in one of the compliance document and not in other compliance document during comparison. Then this is not a high impact change nor any difference in semantic meaning, when every cell content of a table in a document to be compared exists in other compliance document.
3. Also when comparing tables, formatting elements like a semicolon, hyphen, spacing etc form low impact category.

# Improve non-table comparison
1. change in figure no, table no, section no form low impact category
2. first compare sections between two docs and if sections match, then look for semantic change in the contents.
3. sometimes the docs may have a different section number or depth, yet contents may match to the maximum with little changes to section title or text. contents in the sense can be tables, paragraphs, bullet points, formulas, equations.
