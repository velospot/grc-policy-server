# Definition of Done

A feature is done only when all applicable items are true.

## General

```text
[ ] Code compiles/runs locally
[ ] Unit tests added or updated
[ ] Integration tests added when crossing component boundaries
[ ] Documentation updated
[ ] No external runtime API added
[ ] Offline mode preserved
[ ] Logs do not leak full confidential document content unnecessarily
```

## Extraction feature

```text
[ ] Original PDF preserved
[ ] SHA-256 stored
[ ] Raw extractor output stored
[ ] CIR object created
[ ] Page numbers preserved
[ ] Bounding boxes preserved when available
[ ] Confidence stored
[ ] Low-confidence path tested
```

## Table feature

```text
[ ] Table caption stored
[ ] Columns stored
[ ] Rows stored
[ ] Cells stored
[ ] Multi-page table logic tested
[ ] Footnotes handled or explicitly flagged
[ ] Table row citations created
```

## Requirement feature

```text
[ ] Source object linked
[ ] Normative level stored
[ ] Language stored
[ ] Raw text preserved
[ ] Normalized text/facts stored
[ ] Citation exists
```

## Comparison feature

```text
[ ] Same-language validation enforced
[ ] Alignment confidence stored
[ ] Added/removed/modified cases covered
[ ] Numeric/unit changes tested
[ ] Table row changes tested
[ ] Ambiguous cases flagged for review
```

## LLM feature

```text
[ ] Local provider only
[ ] Prompt version stored
[ ] Evidence pack used
[ ] JSON schema validation enforced
[ ] Citations validated
[ ] Output language checked
[ ] Unsupported claims rejected or flagged
```

## UI feature

```text
[ ] User can see job status
[ ] User can inspect citation
[ ] PDF highlight works
[ ] Error states are clear
[ ] Review state is auditable
```
