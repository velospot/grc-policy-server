# 24 - Automotive EMC Domain Ontology

## Purpose

This document defines the first ontology for automotive EMC compliance comparison.

The ontology helps the auditor normalize technical facts, align table rows, classify changes, and explain impact with domain-specific precision.

Core principle:

```text
Use the ontology to structure evidence. Do not use it to invent facts that are not present in the source documents.
```

---

## 1. Scope

The initial ontology covers common automotive electromagnetic compatibility document concepts:

```text
- conducted emissions
- radiated emissions
- conducted immunity
- radiated immunity
- bulk current injection
- electrostatic discharge
- electrical transients
- supply voltage disturbances
- frequency ranges
- test levels
- limits
- detector types
- modulation
- dwell time
- acceptance criteria
- product applicability
- ports, harnesses, and coupling methods
- references to test methods and standards
```

The ontology should be extensible per OEM, lab, or regulatory document family.

---

## 2. Entity types

Recommended entity types:

```text
Requirement
TestMethod
TestPhenomenon
EmissionLimit
ImmunityLevel
FrequencyRange
VoltageRange
CurrentLimit
FieldStrength
PulseLevel
ESDLevel
AcceptanceCriterion
DetectorType
Modulation
DwellTime
CouplingMethod
Port
Harness
DeviceUnderTest
OperatingMode
VehicleCategory
ComponentCategory
Exception
ApplicabilityScope
Footnote
ReferenceStandard
NormativeTerm
EvidenceDocument
```

---

## 3. Relationship types

```text
requires_test_method
has_test_phenomenon
has_frequency_range
has_limit
has_test_level
has_unit
has_detector
has_modulation
has_acceptance_criterion
applies_to_component
applies_to_vehicle_category
applies_to_port
has_exception
has_footnote
references_standard
supersedes_requirement
is_evidence_for
invalidates_evidence
```

Example fact graph:

```json
{
  "requirementId": "REQ-000245",
  "relationships": [
    {"type": "has_test_phenomenon", "target": "radiated_immunity"},
    {"type": "has_frequency_range", "target": "200-400 MHz"},
    {"type": "has_test_level", "target": "30 V/m"},
    {"type": "has_modulation", "target": "AM 80%"},
    {"type": "has_acceptance_criterion", "target": "Class A"}
  ]
}
```

---

## 4. Normalized quantity model

```json
{
  "factId": "FACT-000001",
  "type": "field_strength",
  "rawText": "30 V/m",
  "value": 30.0,
  "unit": "V/m",
  "normalizedUnit": "V/m",
  "dimension": "electric_field_strength",
  "sourceObjectId": "cell-v1-0042-12-03",
  "confidence": 0.96
}
```

Frequency range model:

```json
{
  "type": "frequency_range",
  "rawText": "200 MHz to 400 MHz",
  "lowerHz": 200000000,
  "upperHz": 400000000,
  "lowerInclusive": true,
  "upperInclusive": true
}
```

---

## 5. Unit normalization

Recommended canonical units:

| Quantity | Canonical unit | Common raw forms |
|---|---|---|
| frequency | Hz | Hz, kHz, MHz, GHz |
| field strength | V/m | V/m, kV/m |
| voltage | V | V, mV, kV |
| current | A | A, mA, uA |
| conducted emission voltage | dBuV | dBuV, dB(uV) |
| conducted emission current | dBuA | dBuA, dB(uA) |
| power | W | W, mW, dBm |
| time | s | ns, us, ms, s, min |
| resistance/impedance | Ohm | Ohm, kohm |
| capacitance | F | pF, nF, uF |
| temperature | degC | C, degC |

Rules:

```text
- Preserve the raw text.
- Store normalized numeric values.
- Store unit conversion provenance.
- Flag ambiguous units for review.
- Do not convert logarithmic units unless conversion is explicitly supported and valid.
```

---

## 6. EMC phenomenon taxonomy

```text
emissions
  conducted_emissions
  radiated_emissions
  transient_emissions

immunity
  radiated_immunity
  conducted_immunity
  bulk_current_injection
  electrostatic_discharge
  electrical_transients
  supply_voltage_variation
```

Synonym examples:

```yaml
radiated_immunity:
  en: [radiated immunity, RF immunity, electromagnetic field immunity]
  de: [gestrahlte Stoerfestigkeit, HF-Stoerfestigkeit]
  fr: [immunite rayonnee, immunite RF]
conducted_emissions:
  en: [conducted emissions, conducted disturbance voltage]
  de: [leitungsgefuehrte Stoeraussendung, Stoerspannung]
  fr: [emissions conduites, tension perturbatrice conduite]
```

Synonyms should be profile-specific when OEM terminology differs.

---

## 7. Normative term dictionary

The ontology should include language-specific normative strength.

```yaml
normative_terms:
  en:
    mandatory: [shall, must, is required to, are required to]
    recommended: [should, is recommended]
    permitted: [may, is permitted]
    prohibited: [shall not, must not, is prohibited]
  de:
    mandatory: [muss, muessen, ist erforderlich, sind erforderlich]
    recommended: [soll, sollte, empfohlen]
    permitted: [darf, duerfen, kann]
    prohibited: [darf nicht, duerfen nicht, ist verboten]
  fr:
    mandatory: [doit, doivent, est obligatoire, sont obligatoires]
    recommended: [devrait, il est recommande]
    permitted: [peut, peuvent, est autorise]
    prohibited: [ne doit pas, ne doivent pas, est interdit]
```

Normative changes map to severity policy rules:

```text
recommended -> mandatory = high, OBLIGATION_STRENGTHENED
mandatory -> recommended = high, OBLIGATION_WEAKENED
permitted -> prohibited = high, PROHIBITION_CHANGED
```

---

## 8. Acceptance criteria ontology

Acceptance criteria often appear as classes, functional status, or pass/fail language.

Common normalized fields:

```text
acceptance_class
functional_status
performance_degradation_allowed
reset_allowed
communication_loss_allowed
recovery_required
pass_fail_condition
```

Example:

```json
{
  "type": "acceptance_criterion",
  "rawText": "Class A",
  "normalizedLabel": "class_a",
  "documentDefinedMeaning": "No degradation of performance allowed during and after exposure.",
  "definitionCitationId": "EV-v2-0012"
}
```

Do not assume that `Class A`, `Class B`, or similar labels mean the same thing across all document families. Resolve definitions from the document when available.

---

## 9. Test method and reference standard model

```json
{
  "type": "reference_standard",
  "rawText": "ISO 11452-2",
  "standardFamily": "ISO 11452",
  "part": "2",
  "edition": null,
  "section": null,
  "confidence": 0.93
}
```

Rules:

```text
- Preserve raw reference text exactly.
- Parse family, part, edition, clause, and year when present.
- Treat changed standard family, part, year, or clause as potentially high impact.
- Treat formatting-only reference changes as low only when target is equivalent.
```

Common automotive EMC reference families may include OEM specifications and industry standards. Pin exact editions in project metadata or the document text; do not infer editions from model knowledge.

---

## 10. Table row key construction

For EMC tables, row keys should be semantic.

Radiated immunity row key example:

```text
phenomenon | frequency_range | modulation | component_or_port | acceptance_criterion
```

Conducted emissions row key example:

```text
phenomenon | port | frequency_range | detector | limit_class
```

ESD row key example:

```text
phenomenon | discharge_type | polarity | voltage_level | location | acceptance_criterion
```

Transient immunity row key example:

```text
phenomenon | pulse_type | supply_voltage | coupling_path | severity_level | acceptance_criterion
```

Never rely on row index alone unless no semantic key can be extracted and review is required.

---

## 11. Ontology-backed change facts

Example numeric change fact:

```json
{
  "diffFactId": "DF-000017",
  "factType": "test_level",
  "ontologyEntity": "FieldStrength",
  "old": {"value": 30, "unit": "V/m"},
  "new": {"value": 60, "unit": "V/m"},
  "comparison": "increased",
  "severityHints": ["TEST_LEVEL_CHANGED", "EVIDENCE_MAY_BE_INVALIDATED"],
  "citations": {
    "v1": ["EV-v1-0042-row-12-cell-03"],
    "v2": ["EV-v2-0047-row-12-cell-03"]
  }
}
```

Example scope change fact:

```json
{
  "diffFactId": "DF-000018",
  "factType": "applicability_scope",
  "old": "passenger vehicles",
  "new": "all vehicles",
  "comparison": "broadened",
  "severityHints": ["SCOPE_BROADENED"]
}
```

---

## 12. Severity mapping from ontology entities

Recommended defaults:

| Entity changed | Default severity | Reason code |
|---|---|---|
| NormativeTerm strength | high | OBLIGATION_STRENGTHENED or OBLIGATION_WEAKENED |
| FieldStrength | high | TEST_LEVEL_CHANGED |
| FrequencyRange | high | FREQUENCY_RANGE_CHANGED |
| EmissionLimit | high | NUMERIC_LIMIT_CHANGED |
| ImmunityLevel | high | TEST_LEVEL_CHANGED |
| AcceptanceCriterion | high | ACCEPTANCE_CRITERION_CHANGED |
| TestMethod | high | TEST_METHOD_CHANGED |
| ReferenceStandard | medium or high | REFERENCE_STANDARD_CHANGED |
| Footnote | medium or high | FOOTNOTE_CHANGED or NORMATIVE_FOOTNOTE_CHANGED |
| DetectorType | medium or high | TEST_SETUP_CHANGED |
| Modulation | medium or high | TEST_SETUP_CHANGED |
| DwellTime | medium or high | TEST_SETUP_CHANGED |
| Layout-only object | low | LAYOUT_ONLY |

Document family profiles may override these defaults.

---

## 13. Extraction patterns

Examples of patterns to extract facts:

```text
frequency range:
  150 kHz to 30 MHz
  200-400 MHz
  from 1 GHz up to 6 GHz

test level:
  30 V/m
  100 mA
  8 kV contact discharge

emission limit:
  46 dBuV
  20 dBuA

modulation:
  AM 80%
  pulse modulation
  1 kHz sine
```

Each extracted fact should include:

```text
- raw text
- normalized value
- unit
- source object ID
- citation ID
- confidence
```

---

## 14. Ontology storage format

Recommended format:

```text
ontology/
  automotive_emc/
    ontology.yaml
    units.yaml
    normative_terms.yaml
    synonyms_en.yaml
    synonyms_de.yaml
    synonyms_fr.yaml
    severity_mappings.yaml
    extraction_patterns.yaml
```

The registry should store a hash for each file and a combined ontology snapshot hash.

---

## 15. Limitations

The ontology must not overreach.

```text
- It should not infer legal meaning outside the document.
- It should not assume a standard edition when the source text omits it.
- It should not assume acceptance class definitions are universal.
- It should not classify a change as high without evidence of the relevant entity change.
- It should route ambiguous units, references, and scope changes to review.
```

---

## 16. Ontology test cases

Minimum ontology tests:

| Input | Expected extraction |
|---|---|
| `30 V/m` | FieldStrength value 30 unit V/m |
| `150 kHz to 30 MHz` | FrequencyRange lower 150000 upper 30000000 |
| `shall withstand` | NormativeTerm mandatory |
| `should withstand` | NormativeTerm recommended |
| `8 kV contact discharge` | ESDLevel value 8 unit kV discharge_type contact |
| `Class A` with local definition | AcceptanceCriterion class_a plus definition citation |
| `CISPR 25` | ReferenceStandard family parsed, edition unknown unless present |
| table row reordered | Same row key, low severity if facts equivalent |

---

## 17. Implementation recommendation

The ontology should be used in three places:

```text
1. CIR enrichment
   Extract normalized domain facts and attach them to requirements, rows, cells, and footnotes.

2. Alignment
   Use ontology facts to align rows and requirements even when wording or order changes.

3. Severity classification
   Trigger deterministic severity rules based on changed ontology facts.
```

The LLM should receive ontology facts as part of the evidence pack so it can explain changes accurately without inventing missing context.
