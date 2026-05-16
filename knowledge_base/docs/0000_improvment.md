- use [@knowledge_base](file:///Users/navm/projects/grc-policy-server/knowledge_base/) 
- consider there GRC tool is for compliance comparison for EMC, environment and safety standards department as provided from upload documents.
- in all the source documents, some tables are not properly identified. or  falsely identified as paragraph. esp in TL docs where some Table caption number does not exist rather as alpahabet. e.g Taballe C or Taballe E

- while comparing table source is lost as section heading shows n row x  m columns, but in citated table markdown there is mismatch.
- comparison of tables is also false due to change in font or decoration of table captions., possibly docling output.
- cosmetic changes when only camelcase or Uppercase in wordings change, should be filtered out from keydifference api call for comparison response.
- sort all difference by page number asc.
- ignore personael identifiable info.
- for DNV based docs there is false high comparison when actually low or medium dfiff on tables as they exist in both e.g DNV-CG-0339_2021-08.pdf (v1.0)
Table 2 Tests with severity levels depending on intended location on board • Page 11
- do evaluation tests and improve the code for
