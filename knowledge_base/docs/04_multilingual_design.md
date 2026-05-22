# 04 - Multilingual Design

## Supported languages

MVP languages:

- English: en
- German: de
- French: fr

The product requirement is same-language comparison. That means English v1 compares with English v2, German v1 with German v2, and French v1 with French v2.

## Key rule

Compare in the source language. Explain in the source language. Do not translate before comparing.

Translation can alter compliance meaning, especially normative terms.

## Language detection

Detect language at multiple levels:

- Document
- Page
- Section
- Paragraph/block
- Table caption
- Table row/cell when needed

Use fastText language identification for offline detection.

Document language field:

```json
{
  "dominant": "de",
  "confidence": 0.98,
  "method": "fasttext_lid_176"
}
```

## Same-language gate

Comparison validation:

```python
def validate_comparison_language(doc_a, doc_b):
    if doc_a.language.dominant != doc_b.language.dominant:
        raise ComparisonBlocked(
            reason="Documents are not in the same language",
            doc_a_language=doc_a.language.dominant,
            doc_b_language=doc_b.language.dominant,
        )
```

UI copy examples:

English:

```text
Comparison blocked. The selected documents are not in the same language.
```

German:

```text
Vergleich blockiert. Die ausgewahlten Dokumente haben nicht dieselbe Sprache.
```

French:

```text
Comparaison bloquee. Les documents selectionnes ne sont pas dans la meme langue.
```

## OCR language configuration

For scanned documents, configure OCR by detected or user-selected language:

```text
en -> Tesseract language pack eng
de -> Tesseract language pack deu
fr -> Tesseract language pack fra
```

For mixed-language documents, use page-level language or a combined OCR mode if available.

## Normative vocabulary dictionary

The deterministic requirement extractor must be language-aware.

```json
{
  "en": {
    "mandatory": ["shall", "must", "is required to", "are required to"],
    "recommended": ["should", "it is recommended"],
    "permitted": ["may", "is permitted"],
    "prohibited": ["shall not", "must not", "is prohibited"]
  },
  "de": {
    "mandatory": ["muss", "muessen", "ist erforderlich", "sind erforderlich", "hat zu"],
    "recommended": ["soll", "sollen", "sollte", "sollten", "wird empfohlen"],
    "permitted": ["darf", "duerfen", "kann", "koennen", "ist zulaessig"],
    "prohibited": ["darf nicht", "duerfen nicht", "ist unzulaessig", "ist verboten"]
  },
  "fr": {
    "mandatory": ["doit", "doivent", "est requis", "sont requis", "est obligatoire"],
    "recommended": ["devrait", "devraient", "il convient de", "est recommande"],
    "permitted": ["peut", "peuvent", "est autorise", "sont autorises"],
    "prohibited": ["ne doit pas", "ne doivent pas", "est interdit", "sont interdits"]
  }
}
```

Store both the normalized level and the original term.

```json
{
  "normative_level": "mandatory",
  "normative_term": "muss",
  "language": "de"
}
```

## Language-specific retrieval

Always filter vector and keyword search by language:

```json
{
  "where": {
    "document_id": "doc_v2",
    "language": "fr",
    "object_type": ["section", "requirement", "table_row"]
  }
}
```

This avoids accidental cross-language matches.

## Language-independent normalization

Technical facts should normalize across languages:

```text
150 kHz to 30 MHz
en: 150 kHz to 30 MHz
de: 150 kHz bis 30 MHz
fr: 150 kHz a 30 MHz
```

All become:

```json
{
  "type": "frequency_range",
  "lower_hz": 150000,
  "upper_hz": 30000000
}
```

Normalize:

- Frequency ranges: Hz, kHz, MHz, GHz.
- Field strength: V/m.
- Conducted emission units: dBuV, dBuA.
- Time: ns, us, ms, s.
- Voltage/current: V, mV, A, mA.
- Temperature: C.
- Percentages.
- Acceptance classes.
- Detector types.
- Standard references.

## Output language control

Pass the target language explicitly to the LLM.

```text
The source documents are in German.
Write the explanation only in German.
Do not translate quoted evidence.
Return valid JSON.
Every claim must cite evidence IDs.
```

## UI localization

The UI should localize labels based on comparison language or user preference.

| Concept | English | German | French |
|---|---|---|---|
| Change type | Change type | Aenderungstyp | Type de modification |
| Impact | Impact | Auswirkung | Impact |
| Evidence | Evidence | Nachweis | Preuve |
| Confidence | Confidence | Vertrauen | Confiance |
| Requires review | Requires review | Manuelle Pruefung erforderlich | Revue manuelle requise |

## Do not translate official citations

Evidence snippets must remain exactly as extracted from the source language, except for whitespace normalization needed for display.

Allowed:

```text
The summary is in English. The quote remains the original English quote.
```

Not allowed:

```text
Source quote translated into another language and cited as if it were original.
```

## Future cross-language mode

Keep cross-language comparison out of MVP. If added later, it should be explicit and marked as approximate. It requires:

- Bilingual alignment model.
- Translation memory.
- Language-specific legal/compliance term map.
- Human approval.
- Separate confidence labeling.
