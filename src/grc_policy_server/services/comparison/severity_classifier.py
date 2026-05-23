"""
Severity classification service — rule-engine-based classifier for change records.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DESIGN
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ClassificationContext   All signals a rule may inspect (frozen dataclass).
  ClassificationResult    Verdict: severity ∈ {low, medium, high} + reason tags.
  ClassifierRule          Protocol — one rule = one class, one responsibility.
  RuleEngine              Evaluates rules in priority order, stops at first match.
  SeverityClassifier      Public service — owns a pre-wired RuleEngine instance.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SEVERITY LEVELS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Exactly three values are ever produced (no "critical"):

  ┌──────────┬──────────────────────────────────────────────────────────────┐
  │ Severity │ Meaning                                                      │
  ├──────────┼──────────────────────────────────────────────────────────────┤
  │ high     │ Content added/removed, or > 70 % semantic divergence         │
  │ medium   │ Obligation changed, moved/split/merged, moderate drift       │
  │ low      │ Cosmetic, formatting, or reference-number-only differences   │
  └──────────┴──────────────────────────────────────────────────────────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULE TABLE  (evaluated top-to-bottom; first match wins)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  #   Rule class                 Condition                         → Severity
  ─── ─────────────────────────  ────────────────────────────────  ──────────
  1   ReferenceNumberOnlyRule    reference_number_only_change=True → low
  2   FormattingOnlyRule         formatting_only_change=True       → low
  3   CosmeticOnlyRule           MODIFIED + cosmetic only          → low
                                 (no obligation or content signal)
  4   AddedRemovedRule           change_type ∈ {ADDED, REMOVED}   → high
  5   ObligationChangeRule       verb change or meaning weakened/  → medium
                                 strengthened/inverted
  6   HighDistanceRule           distance > 0.75                   → high
  7   MovedRule                  alignment_type == "moved"         → medium
                                 (always; moved content is always
                                 reviewer-relevant regardless of
                                 whether the wording changed)
  8   SplitMergeRule             alignment_type ∈ {split, merged}  → low
                                 when no semantic signal AND        (else medium)
                                 distance ≤ 0.35
  9   ContentSignalRule          has_content_signal OR             → medium
                                 distance > 0.35
  10  DefaultRule                catch-all                         → low

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SIGNAL DEFINITIONS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  has_obligation_signal  requirement_verb_change is not None
                         OR meaning_change ∈ {weakened, strengthened, inverted}

  has_content_signal     meaning_change not in {unchanged, ""}
                         OR numeric_changes is non-empty
                         OR table_changes is non-empty

  distance               float in [0, 1]: 0 = identical, 1 = completely different
                         (distance = 1 − cosine_similarity)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EXTENSIBILITY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  To add a new rule:
    1. Create a class with an `evaluate(ctx) -> ClassificationResult | None` method.
    2. Insert it at the correct priority position in `_DEFAULT_ENGINE`.
    3. Add a row to the rule table above.

  To override rules in tests or domain pipelines:
    classifier = SeverityClassifier(engine=RuleEngine([MyRule(), DefaultRule()]))
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Protocol, runtime_checkable

ChangeType = Literal["ADDED", "REMOVED", "MODIFIED"]
Severity = Literal["low", "medium", "high"]


class SeverityReasonCode(str, Enum):
    """Structured reason codes for severity classification — machine-readable audit trail."""

    OBLIGATION_STRENGTHENED = "OBLIGATION_STRENGTHENED"
    OBLIGATION_WEAKENED = "OBLIGATION_WEAKENED"
    TEST_LEVEL_CHANGED = "TEST_LEVEL_CHANGED"
    NUMERIC_LIMIT_CHANGED = "NUMERIC_LIMIT_CHANGED"
    FREQUENCY_RANGE_CHANGED = "FREQUENCY_RANGE_CHANGED"
    ACCEPTANCE_CRITERION_CHANGED = "ACCEPTANCE_CRITERION_CHANGED"
    TEST_METHOD_CHANGED = "TEST_METHOD_CHANGED"
    SCOPE_BROADENED = "SCOPE_BROADENED"
    SCOPE_NARROWED = "SCOPE_NARROWED"
    NORMATIVE_FOOTNOTE_CHANGED = "NORMATIVE_FOOTNOTE_CHANGED"
    EVIDENCE_MAY_BE_INVALIDATED = "EVIDENCE_MAY_BE_INVALIDATED"
    CROSS_REFERENCE_CHANGED = "CROSS_REFERENCE_CHANGED"
    PRESENTATION_ONLY = "PRESENTATION_ONLY"


class SemanticImpact(str, Enum):
    """Impact category for a change — more granular than severity."""

    NONE = "none"
    EDITORIAL = "editorial"
    STRUCTURAL = "structural"
    SEMANTIC = "semantic"
    TECHNICAL = "technical"
    NORMATIVE = "normative"
    SCOPE = "scope"


class AuditDisposition(str, Enum):
    """Routing decision for downstream audit/review workflows."""

    AUTO_CLASSIFIED = "auto_classified"
    REQUIRES_HUMAN_REVIEW = "requires_human_review"
    ESCALATED = "escalated"


# ──────────────────────────────────────────────────────────────────────────────
# Context & Result
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ClassificationContext:
    """All signals available to every classifier rule (immutable).

    Fields
    ------
    change_type                 ADDED | REMOVED | MODIFIED
    alignment_type              How the matcher paired the nodes:
                                  exact / heading / semantic / moved / split / merged
    node_type                   paragraph | clause | table | list_item | heading | …
    distance                    Semantic distance in [0, 1].  None for ADDED/REMOVED.
    meaning_change              unchanged | changed | weakened | strengthened |
                                inverted | modified | added | removed
    numeric_changes             List of {type, old, new} dicts from detect_numeric_changes().
    requirement_verb_change     {old, new, direction} dict or None.
    table_changes               ChangeDetail payloads for table-type nodes.
    cosmetic_change             True when texts differ only in casing/unicode punctuation.
    reference_number_only_change True when the only numeric differences are cross-reference
                                  numbers (Figure N, Table N, Section N).
    formatting_only_change      True when texts differ only in newlines, hyphens, semicolons.
    """

    change_type: ChangeType
    alignment_type: str
    node_type: str
    distance: float | None
    meaning_change: str
    numeric_changes: list[dict[str, Any]]
    requirement_verb_change: dict[str, str] | None
    table_changes: list[dict[str, Any]]
    cosmetic_change: bool = False
    reference_number_only_change: bool = False
    formatting_only_change: bool = False
    structural_label_change: bool = False
    # Ontology-backed enrichment fields (populated by Phase C ontology module)
    normalized_facts: list[dict[str, Any]] = field(default_factory=list)
    ontology_entity_type: str = (
        ""  # e.g. "FieldStrength", "FrequencyRange", "EmissionLimit"
    )
    test_procedure_change: bool = False
    test_setup_change: bool = False
    # Domain expert routing: "EMC" | "Safety" | "Environment" | ""
    testing_department: str = ""
    # Section role context — "normative" | "informative" | "annex" | "appendix" | "note"
    section_role: str = "normative"
    # Obligation strength of the node text (-1 = unknown, 0=may … 5=shall)
    obligation_strength: int = -1


@dataclass(frozen=True)
class ClassificationResult:
    """Verdict produced by the rule engine.

    severity              One of: low | medium | high  (never "critical").
    reasons               Informational tags — useful for debugging and trace payloads.
    impact                Capitalised severity string for the API response `impact` field.
    semantic_impact       Fine-grained impact category (none/editorial/structural/…).
    severity_reason_codes Structured reason codes for audit-grade reports.
    severity_confidence   Classifier confidence in [0, 1].
    audit_disposition     Routing hint for review workflows.
    """

    severity: Severity
    reasons: list[str] = field(default_factory=list)
    semantic_impact: SemanticImpact = SemanticImpact.NONE
    severity_reason_codes: list[SeverityReasonCode] = field(default_factory=list)
    severity_confidence: float = 1.0
    audit_disposition: AuditDisposition = AuditDisposition.AUTO_CLASSIFIED

    @property
    def impact(self) -> str:
        return self.severity.capitalize()


# ──────────────────────────────────────────────────────────────────────────────
# Rule protocol
# ──────────────────────────────────────────────────────────────────────────────


@runtime_checkable
class ClassifierRule(Protocol):
    """Contract for a single classification rule.

    `evaluate` returns a `ClassificationResult` when this rule fires, or `None`
    to defer to the next rule in the engine.
    """

    def evaluate(self, ctx: ClassificationContext) -> ClassificationResult | None: ...


# ──────────────────────────────────────────────────────────────────────────────
# Shared helpers (private)
# ──────────────────────────────────────────────────────────────────────────────


def _collect_reasons(ctx: ClassificationContext) -> list[str]:
    """Build the informational reason-tag list.  Never used to gate severity."""
    reasons: list[str] = []
    if ctx.change_type in {"ADDED", "REMOVED"}:
        reasons.append(ctx.change_type.lower())
    if ctx.alignment_type in {"moved", "split", "merged"}:
        reasons.append(ctx.alignment_type)
    if ctx.cosmetic_change:
        reasons.append("cosmetic_change")
    if ctx.numeric_changes:
        reasons.append("numeric_change")
    if ctx.requirement_verb_change:
        reasons.append(f"requirement_verb_{ctx.requirement_verb_change['direction']}")
    if ctx.table_changes:
        reasons.append("table_change")
    if ctx.meaning_change not in {"unchanged", ""}:
        reasons.append(f"meaning_{ctx.meaning_change}")
    return reasons


def _has_obligation_signal(ctx: ClassificationContext) -> bool:
    """True when the change involves a shift in normative strength."""
    return bool(ctx.requirement_verb_change) or ctx.meaning_change in {
        "weakened",
        "strengthened",
        "inverted",
    }


def _has_content_signal(ctx: ClassificationContext) -> bool:
    """True when there is a detectable semantic or structural content change."""
    return (
        ctx.meaning_change not in {"unchanged", ""}
        or bool(ctx.numeric_changes)
        or bool(ctx.table_changes)
    )


# ──────────────────────────────────────────────────────────────────────────────
# Rule implementations  (priority: top → bottom, matching rule table in module doc)
# ──────────────────────────────────────────────────────────────────────────────


class StructuralRenumberingRule:
    """Rule 0.5 — Section renumbered with identical content → LOW.

    Fires on MODIFIED section/heading nodes where only the leading section
    number changed and the remaining text is essentially identical (distance <
    0.25 and meaning_change in {unchanged, ""}). This prevents purely
    administrative renumbering from appearing as a content change.
    """

    def evaluate(self, ctx: ClassificationContext) -> ClassificationResult | None:
        if (
            ctx.node_type in {"section", "heading"}
            and ctx.change_type == "MODIFIED"
            and ctx.structural_label_change
            and (ctx.distance is None or ctx.distance < 0.25)
            and ctx.meaning_change in {"unchanged", ""}
        ):
            return ClassificationResult(
                severity="low",
                reasons=["structural_renumbering"],
                semantic_impact=SemanticImpact.STRUCTURAL,
                audit_disposition=AuditDisposition.AUTO_CLASSIFIED,
            )
        return None


class StructuralLabelRule:
    """Rule 0 — Structural label change (section/table/figure/chapter number) → LOW.

    Fires on MODIFIED nodes when the only difference is in the structural label
    prefix (e.g. "3.1 Introduction" → "4.1 Introduction", "Table 5: Limits" →
    "Table 7: Limits", "Chapter 3:" → "Chapter 4:").  The semantic content after
    stripping those prefixes must be identical.

    This keeps pure renumbering out of the MEDIUM/HIGH bucket.
    """

    def evaluate(self, ctx: ClassificationContext) -> ClassificationResult | None:
        if ctx.structural_label_change and ctx.change_type == "MODIFIED":
            return ClassificationResult(
                severity="low", reasons=["structural_label_change"]
            )
        return None


class ReferenceNumberOnlyRule:
    """Rule 1 — Reference-number-only change → LOW.

    Fires when the only numeric differences between the two texts are changes to
    cross-reference labels: "Figure 3" → "Figure 5", "Section 3.1" → "Section 4.2",
    "Table A.1" → "Table B.2", etc.  These are structural reordering artefacts
    produced by document renumbering and carry no semantic weight.
    """

    def evaluate(self, ctx: ClassificationContext) -> ClassificationResult | None:
        if ctx.reference_number_only_change:
            return ClassificationResult(
                severity="low", reasons=["reference_number_change"]
            )
        return None


class FormattingOnlyRule:
    """Rule 2 — Formatting-only change → LOW.

    Fires when the two texts are identical after stripping formatting characters:
    newlines, carriage returns, hyphens/dashes (- – —), and semicolons.  Extra
    whitespace is also collapsed.  No semantic meaning is lost or gained.

    Examples that fire this rule:
      "The system must;\n- log all access."  vs  "The system must log all access."
      "N/A"  vs  "N A"  (hyphen vs space)
    """

    def evaluate(self, ctx: ClassificationContext) -> ClassificationResult | None:
        if ctx.formatting_only_change:
            return ClassificationResult(
                severity="low", reasons=["formatting_only_change"]
            )
        return None


class CosmeticOnlyRule:
    """Rule 3 — Cosmetic-only MODIFIED → LOW.

    Fires on MODIFIED nodes where the texts are identical after normalising:
      - case folding
      - unicode punctuation (curly quotes → straight, em-dash → hyphen, etc.)
      - stripping all non-alphanumeric characters

    Only fires when there is no obligation signal and no content signal; if the
    cosmetic change accompanies a real semantic change the rule passes.
    """

    def evaluate(self, ctx: ClassificationContext) -> ClassificationResult | None:
        if ctx.change_type != "MODIFIED" or not ctx.cosmetic_change:
            return None
        if not _has_obligation_signal(ctx) and not _has_content_signal(ctx):
            return ClassificationResult(severity="low", reasons=_collect_reasons(ctx))
        return None


class AddedRemovedRule:
    """Rule 4 — Added or removed content → context-tiered severity.

    Tier 1: Node has a normative obligation verb (shall/must) → HIGH
    Tier 2: Node is in an informative/annex/note section role → MEDIUM
    Tier 3: No obligation signal at all → MEDIUM with human review flag
    Default: HIGH for normative content with any obligation signal
    """

    _INFORMATIVE_ROLES = frozenset({"informative", "annex", "appendix", "note", "footnote"})

    def evaluate(self, ctx: ClassificationContext) -> ClassificationResult | None:
        if ctx.change_type not in {"ADDED", "REMOVED"}:
            return None

        reasons = _collect_reasons(ctx)

        # Tier 1: Strong normative obligation (shall=5) present → HIGH even in informative.
        # bool(-1)=True but -1 >= 5 is False → no-op for unknown.
        # bool(0)=False → short-circuit, no-op for "may".
        if ctx.obligation_strength and ctx.obligation_strength >= 5:
            return ClassificationResult(
                severity="high",
                reasons=reasons,
                semantic_impact=SemanticImpact.NORMATIVE,
                audit_disposition=AuditDisposition.ESCALATED,
            )

        # Tier 2: Content in informative / annex / note section → MEDIUM
        if ctx.section_role in self._INFORMATIVE_ROLES:
            return ClassificationResult(
                severity="medium",
                reasons=reasons + ["informative_section"],
                semantic_impact=SemanticImpact.EDITORIAL,
                audit_disposition=AuditDisposition.REQUIRES_HUMAN_REVIEW,
            )

        # Tier 3: "May" was explicitly detected (obligation_strength == 0) → MEDIUM.
        # bool(0)=False → fires when exactly 0.
        # bool(-1)=True → does NOT fire for unknown/unset (-1 is truthy).
        if not bool(ctx.obligation_strength):
            return ClassificationResult(
                severity="medium",
                reasons=reasons + ["weak_obligation_only"],
                audit_disposition=AuditDisposition.REQUIRES_HUMAN_REVIEW,
            )

        # Default: any other obligation level or unknown → HIGH
        return ClassificationResult(
            severity="high",
            reasons=reasons,
            audit_disposition=AuditDisposition.ESCALATED,
        )


class ObligationChangeRule:
    """Rule 5 — Obligation / normative-verb change → MEDIUM.

    Fires when:
      • A requirement verb changed: shall/must/should/may in any direction
        (strengthened, weakened, or laterally changed).
      • meaning_change is weakened, strengthened, or inverted.

    Obligation changes are compliance-relevant and must be reviewed, but they
    are not automatically HIGH — the reviewer assesses whether the intent
    changed or merely the wording.
    """

    def evaluate(self, ctx: ClassificationContext) -> ClassificationResult | None:
        if _has_obligation_signal(ctx):
            return ClassificationResult(
                severity="medium", reasons=_collect_reasons(ctx)
            )
        return None


class HighDistanceRule:
    """Rule 6 — High semantic distance → HIGH.

    Fires when distance > 0.75, meaning less than 40 % of the semantic content
    is shared between the two node versions.  At this divergence level the node
    is effectively a replacement, not an edit.

    Threshold: distance > 0.75  (equivalent to cosine_similarity < 0.40).
    """

    def evaluate(self, ctx: ClassificationContext) -> ClassificationResult | None:
        if ctx.distance is not None and ctx.distance > 0.75:
            return ClassificationResult(severity="high", reasons=_collect_reasons(ctx))
        return None


class MovedRule:
    """Rule 7 — Moved node or section → MEDIUM (always).

    Fires whenever alignment_type == "moved", regardless of content similarity
    or semantic distance.

    Rationale: a moved node always requires a reviewer to confirm that the new
    location is contextually appropriate.  A clause that is semantically
    identical but placed under a different section may still carry different
    compliance implications (e.g. a requirement moved from "mandatory" to
    "informative" section).  Therefore moved content is never classified as LOW.

    Note: split and merged alignments are handled separately by SplitMergeRule.
    """

    def evaluate(self, ctx: ClassificationContext) -> ClassificationResult | None:
        if ctx.alignment_type == "moved":
            return ClassificationResult(
                severity="medium", reasons=_collect_reasons(ctx)
            )
        return None


class SplitMergeRule:
    """Rule 8 — Split or merged nodes → LOW when repositioning only, else MEDIUM.

    Fires when alignment_type ∈ {split, merged}.

    LOW  when: no obligation signal, no content signal, and distance ≤ 0.35.
         This covers pure structural reformatting (one paragraph split into two
         with identical wording, or two bullets merged into one).

    MEDIUM otherwise: the structural change accompanies detectable content
         differences and must be reviewed.
    """

    def evaluate(self, ctx: ClassificationContext) -> ClassificationResult | None:
        if ctx.alignment_type not in {"split", "merged"}:
            return None
        has_semantic = _has_obligation_signal(ctx) or _has_content_signal(ctx)
        reasons = _collect_reasons(ctx)
        if not has_semantic and (ctx.distance is None or ctx.distance <= 0.35):
            return ClassificationResult(severity="low", reasons=reasons)
        return ClassificationResult(severity="medium", reasons=reasons)


class ContentSignalRule:
    """Rule 9 — Moderate content drift or semantic signals → MEDIUM.

    Fires when any of:
      • has_content_signal is True (meaning changed, numeric values differ,
        or table cell content changed).
      • distance > 0.35 (more than 35 % semantic divergence).

    At this level the change is meaningful enough to warrant review but does
    not meet the > 60 % threshold for HIGH.
    """

    def evaluate(self, ctx: ClassificationContext) -> ClassificationResult | None:
        if _has_content_signal(ctx):
            return ClassificationResult(
                severity="medium", reasons=_collect_reasons(ctx)
            )
        if ctx.distance is not None and ctx.distance > 0.35:
            return ClassificationResult(
                severity="medium", reasons=_collect_reasons(ctx)
            )
        return None


_DOMAIN_ENTITY_MAP: dict[str, tuple[SeverityReasonCode, SemanticImpact]] = {
    "FieldStrength": (SeverityReasonCode.TEST_LEVEL_CHANGED, SemanticImpact.TECHNICAL),
    "FrequencyRange": (
        SeverityReasonCode.FREQUENCY_RANGE_CHANGED,
        SemanticImpact.TECHNICAL,
    ),
    "EmissionLimit": (
        SeverityReasonCode.NUMERIC_LIMIT_CHANGED,
        SemanticImpact.TECHNICAL,
    ),
    "ImmunityLevel": (SeverityReasonCode.TEST_LEVEL_CHANGED, SemanticImpact.TECHNICAL),
    "AcceptanceCriterion": (
        SeverityReasonCode.ACCEPTANCE_CRITERION_CHANGED,
        SemanticImpact.NORMATIVE,
    ),
    "TestMethod": (SeverityReasonCode.TEST_METHOD_CHANGED, SemanticImpact.TECHNICAL),
    "NormativeTerm": (
        SeverityReasonCode.OBLIGATION_STRENGTHENED,
        SemanticImpact.NORMATIVE,
    ),
}


class DomainEntityRule:
    """Rule 9.1 — Ontology entity changed → HIGH with structured reason code.

    Fires when `ctx.ontology_entity_type` identifies a known EMC domain entity
    (FieldStrength, FrequencyRange, EmissionLimit, etc.).  These changes are
    deterministically high severity because the underlying test parameter changed.

    Inert until Phase C populates `ontology_entity_type` — no effect on existing
    behaviour when the field is empty.
    """

    def evaluate(self, ctx: ClassificationContext) -> ClassificationResult | None:
        if not ctx.ontology_entity_type:
            return None
        entry = _DOMAIN_ENTITY_MAP.get(ctx.ontology_entity_type)
        if entry is None:
            return None
        reason_code, impact = entry
        return ClassificationResult(
            severity="high",
            reasons=_collect_reasons(ctx) + [reason_code.value],
            semantic_impact=impact,
            severity_reason_codes=[
                reason_code,
                SeverityReasonCode.EVIDENCE_MAY_BE_INVALIDATED,
            ],
            severity_confidence=0.95,
            audit_disposition=AuditDisposition.ESCALATED,
        )


class NormativeObligationEscalationRule:
    """Rule 9.2 — Obligation weakened → MEDIUM with audit routing.

    Fires when a requirement verb change has direction "weakened", enriching the
    result with `OBLIGATION_WEAKENED` reason code and `REQUIRES_HUMAN_REVIEW`
    disposition.  Severity stays MEDIUM (obligation changes are review-relevant
    but not automatically high without knowing domain context).
    """

    def evaluate(self, ctx: ClassificationContext) -> ClassificationResult | None:
        if (
            ctx.requirement_verb_change
            and ctx.requirement_verb_change.get("direction") == "weakened"
        ):
            return ClassificationResult(
                severity="medium",
                reasons=_collect_reasons(ctx),
                semantic_impact=SemanticImpact.NORMATIVE,
                severity_reason_codes=[SeverityReasonCode.OBLIGATION_WEAKENED],
                severity_confidence=0.90,
                audit_disposition=AuditDisposition.REQUIRES_HUMAN_REVIEW,
            )
        return None


class ObligationStrengthCodeRule:
    """Rule 9.3 — Obligation strengthened → MEDIUM with structured reason code.

    Fires when a requirement verb change direction is "strengthened", adding the
    `OBLIGATION_STRENGTHENED` reason code for audit traceability.
    """

    def evaluate(self, ctx: ClassificationContext) -> ClassificationResult | None:
        if (
            ctx.requirement_verb_change
            and ctx.requirement_verb_change.get("direction") == "strengthened"
        ):
            return ClassificationResult(
                severity="medium",
                reasons=_collect_reasons(ctx),
                semantic_impact=SemanticImpact.NORMATIVE,
                severity_reason_codes=[SeverityReasonCode.OBLIGATION_STRENGTHENED],
                severity_confidence=0.90,
                audit_disposition=AuditDisposition.AUTO_CLASSIFIED,
            )
        return None


class PresentationOnlyRule:
    """Rule 9.4 — Purely cosmetic + formatting change → LOW with PRESENTATION_ONLY code.

    Fires only when *both* `cosmetic_change` and `formatting_only_change` are set,
    meaning the two texts differ only in presentation (case, whitespace, hyphens,
    unicode punctuation).  Adds `PRESENTATION_ONLY` to reason codes.
    """

    def evaluate(self, ctx: ClassificationContext) -> ClassificationResult | None:
        if ctx.cosmetic_change and ctx.formatting_only_change:
            return ClassificationResult(
                severity="low",
                reasons=_collect_reasons(ctx),
                semantic_impact=SemanticImpact.EDITORIAL,
                severity_reason_codes=[SeverityReasonCode.PRESENTATION_ONLY],
                severity_confidence=1.0,
                audit_disposition=AuditDisposition.AUTO_CLASSIFIED,
            )
        return None


class TestProcedureChangeRule:
    """Rule 9.5 — Test procedure changes → HIGH.

    A change to which standard revision applies, or by how long a device must be
    stressed, directly affects whether an existing type-approval certificate is valid.
    """

    def evaluate(self, ctx: ClassificationContext) -> ClassificationResult | None:
        if (
            ctx.change_type == "MODIFIED"
            and ctx.test_procedure_change
            and ctx.meaning_change not in ("unchanged", "")
        ):
            return ClassificationResult(
                severity="high",
                reasons=_collect_reasons(ctx) + ["TEST_PROCEDURE_CHANGED"],
                semantic_impact=SemanticImpact.TECHNICAL,
                severity_reason_codes=[SeverityReasonCode.TEST_METHOD_CHANGED],
                severity_confidence=0.90,
                audit_disposition=AuditDisposition.REQUIRES_HUMAN_REVIEW,
            )
        return None


class TestSetupChangeRule:
    """Rule 9.6 — Test setup parameter changes → MEDIUM."""

    def evaluate(self, ctx: ClassificationContext) -> ClassificationResult | None:
        if (
            ctx.change_type == "MODIFIED"
            and ctx.test_setup_change
            and ctx.meaning_change not in ("unchanged", "")
        ):
            return ClassificationResult(
                severity="medium",
                reasons=_collect_reasons(ctx) + ["TEST_SETUP_CHANGED"],
                semantic_impact=SemanticImpact.TECHNICAL,
                severity_confidence=0.85,
                audit_disposition=AuditDisposition.REQUIRES_HUMAN_REVIEW,
            )
        return None


class EMCExpertRule:
    """Rule 9.7 — EMC domain expert escalations.

    Fires when ``testing_department == "EMC"`` (or "EMV") on a MODIFIED node.
    Escalates to HIGH cases that the generic rules would classify as MEDIUM:

    - Any numeric value changed (limits, levels — always HIGH for EMC).
    - Any table cell content changed (EMC tables encode test conditions and
      acceptance criteria as a knowledge graph — cell ≠ row position).
    - Any normative-verb weakening (shall→should → compliance risk → HIGH).

    Formatting-only and cosmetic-only changes are deferred to the earlier rules
    (they correctly produce LOW regardless of domain).
    """

    _DEPT = frozenset({"EMC", "EMV"})

    def evaluate(self, ctx: ClassificationContext) -> ClassificationResult | None:
        if ctx.testing_department.upper() not in self._DEPT:
            return None
        if ctx.change_type != "MODIFIED":
            return None
        # Defer formatting/cosmetic-only to earlier rules (they produce LOW correctly)
        if ctx.formatting_only_change and not ctx.numeric_changes:
            return None
        if ctx.cosmetic_change and not _has_content_signal(ctx):
            return None

        # Weakened obligation → HIGH (EMC cannot relax test requirements)
        if (
            ctx.requirement_verb_change
            and ctx.requirement_verb_change.get("direction") == "weakened"
        ):
            return ClassificationResult(
                severity="high",
                reasons=_collect_reasons(ctx) + ["emc_obligation_weakened"],
                semantic_impact=SemanticImpact.NORMATIVE,
                severity_reason_codes=[
                    SeverityReasonCode.OBLIGATION_WEAKENED,
                    SeverityReasonCode.EVIDENCE_MAY_BE_INVALIDATED,
                ],
                severity_confidence=0.95,
                audit_disposition=AuditDisposition.ESCALATED,
            )

        # Any numeric change in EMC context → HIGH (limits and levels changed)
        if ctx.numeric_changes:
            reason_codes: list[SeverityReasonCode] = [SeverityReasonCode.NUMERIC_LIMIT_CHANGED]
            if ctx.ontology_entity_type in _DOMAIN_ENTITY_MAP:
                reason_codes.append(_DOMAIN_ENTITY_MAP[ctx.ontology_entity_type][0])
            reason_codes.append(SeverityReasonCode.EVIDENCE_MAY_BE_INVALIDATED)
            return ClassificationResult(
                severity="high",
                reasons=_collect_reasons(ctx) + ["emc_numeric_change"],
                semantic_impact=SemanticImpact.TECHNICAL,
                severity_reason_codes=reason_codes,
                severity_confidence=0.92,
                audit_disposition=AuditDisposition.ESCALATED,
            )

        # Table content change: EMC tables encode test conditions as a knowledge graph
        if ctx.node_type == "table" and ctx.table_changes:
            return ClassificationResult(
                severity="high",
                reasons=_collect_reasons(ctx) + ["emc_table_content_changed"],
                semantic_impact=SemanticImpact.TECHNICAL,
                severity_reason_codes=[
                    SeverityReasonCode.TEST_LEVEL_CHANGED,
                    SeverityReasonCode.EVIDENCE_MAY_BE_INVALIDATED,
                ],
                severity_confidence=0.90,
                audit_disposition=AuditDisposition.ESCALATED,
            )

        return None


class SafetyExpertRule:
    """Rule 9.8 — Product safety domain expert escalations.

    Fires when ``testing_department == "Safety"`` on a MODIFIED node.
    Escalates to HIGH:
    - Weakened obligation (safety requirements can never be weakened).
    - Numeric changes (safety thresholds, leakage currents, voltage limits).
    - Test procedure changes.
    """

    _DEPT = frozenset({"SAFETY"})

    def evaluate(self, ctx: ClassificationContext) -> ClassificationResult | None:
        if ctx.testing_department.upper() not in self._DEPT:
            return None
        if ctx.change_type != "MODIFIED":
            return None
        if ctx.formatting_only_change and not ctx.numeric_changes:
            return None
        if ctx.cosmetic_change and not _has_content_signal(ctx):
            return None

        if (
            ctx.requirement_verb_change
            and ctx.requirement_verb_change.get("direction") == "weakened"
        ):
            return ClassificationResult(
                severity="high",
                reasons=_collect_reasons(ctx) + ["safety_obligation_weakened"],
                semantic_impact=SemanticImpact.NORMATIVE,
                severity_reason_codes=[
                    SeverityReasonCode.OBLIGATION_WEAKENED,
                    SeverityReasonCode.EVIDENCE_MAY_BE_INVALIDATED,
                ],
                severity_confidence=0.95,
                audit_disposition=AuditDisposition.ESCALATED,
            )

        if ctx.numeric_changes:
            return ClassificationResult(
                severity="high",
                reasons=_collect_reasons(ctx) + ["safety_numeric_limit_changed"],
                semantic_impact=SemanticImpact.TECHNICAL,
                severity_reason_codes=[
                    SeverityReasonCode.NUMERIC_LIMIT_CHANGED,
                    SeverityReasonCode.EVIDENCE_MAY_BE_INVALIDATED,
                ],
                severity_confidence=0.92,
                audit_disposition=AuditDisposition.ESCALATED,
            )

        if ctx.test_procedure_change:
            return ClassificationResult(
                severity="high",
                reasons=_collect_reasons(ctx) + ["safety_test_procedure_changed"],
                semantic_impact=SemanticImpact.TECHNICAL,
                severity_reason_codes=[SeverityReasonCode.TEST_METHOD_CHANGED],
                severity_confidence=0.90,
                audit_disposition=AuditDisposition.REQUIRES_HUMAN_REVIEW,
            )

        return None


class EnvironmentalExpertRule:
    """Rule 9.9 — Environmental testing domain expert escalations.

    Fires when ``testing_department == "Environment"`` on a MODIFIED node.
    Escalates to HIGH:
    - Weakened obligation (durability/conditioning requirements cannot be reduced).
    - Numeric changes (temperature, humidity, cycles, dwell times).
    - Test setup changes (conditioning, soak, environment parameters).
    - Test procedure changes (standard edition or method changes).
    """

    _DEPT = frozenset({"ENVIRONMENT", "ENVIRONMENTAL"})

    def evaluate(self, ctx: ClassificationContext) -> ClassificationResult | None:
        if ctx.testing_department.upper() not in self._DEPT:
            return None
        if ctx.change_type != "MODIFIED":
            return None
        if ctx.formatting_only_change and not ctx.numeric_changes:
            return None
        if ctx.cosmetic_change and not _has_content_signal(ctx):
            return None

        if (
            ctx.requirement_verb_change
            and ctx.requirement_verb_change.get("direction") == "weakened"
        ):
            return ClassificationResult(
                severity="high",
                reasons=_collect_reasons(ctx) + ["env_obligation_weakened"],
                semantic_impact=SemanticImpact.NORMATIVE,
                severity_reason_codes=[
                    SeverityReasonCode.OBLIGATION_WEAKENED,
                    SeverityReasonCode.EVIDENCE_MAY_BE_INVALIDATED,
                ],
                severity_confidence=0.95,
                audit_disposition=AuditDisposition.ESCALATED,
            )

        if ctx.numeric_changes:
            return ClassificationResult(
                severity="high",
                reasons=_collect_reasons(ctx) + ["env_numeric_condition_changed"],
                semantic_impact=SemanticImpact.TECHNICAL,
                severity_reason_codes=[
                    SeverityReasonCode.NUMERIC_LIMIT_CHANGED,
                    SeverityReasonCode.EVIDENCE_MAY_BE_INVALIDATED,
                ],
                severity_confidence=0.92,
                audit_disposition=AuditDisposition.ESCALATED,
            )

        if ctx.test_setup_change or ctx.test_procedure_change:
            return ClassificationResult(
                severity="high",
                reasons=_collect_reasons(ctx) + ["env_test_condition_changed"],
                semantic_impact=SemanticImpact.TECHNICAL,
                severity_reason_codes=[SeverityReasonCode.TEST_METHOD_CHANGED],
                severity_confidence=0.88,
                audit_disposition=AuditDisposition.REQUIRES_HUMAN_REVIEW,
            )

        return None


class EntityGraphChangeRule:
    """Rule 4.8 — Entity graph change for a high-severity domain entity → HIGH.

    `_diff_entity_graphs()` in table_diff_engine.py appends dicts with
    ``{"type": "entity_graph_change", "entity_type": "<name>", ...}`` to
    ``table_changes``.  The generic ContentSignalRule sees any table_changes as
    MEDIUM.  This rule inspects the entity_type and escalates to HIGH when the
    changed entity is in the domain high-severity set.
    """

    def evaluate(self, ctx: ClassificationContext) -> ClassificationResult | None:
        if not ctx.table_changes:
            return None
        for change in ctx.table_changes:
            if change.get("type") != "entity_graph_change":
                continue
            entity_type = change.get("entity_type", "")
            # Import lazily to avoid circular import at module load time
            try:
                from grc_policy_server.services.comparison.table_diff_engine import (  # noqa: PLC0415
                    _HIGH_SEVERITY_ENTITIES,
                )
            except Exception:
                return None
            if entity_type in _HIGH_SEVERITY_ENTITIES:
                return ClassificationResult(
                    severity="high",
                    reasons=_collect_reasons(ctx) + ["entity_graph_high_severity"],
                    semantic_impact=SemanticImpact.TECHNICAL,
                    severity_reason_codes=[
                        SeverityReasonCode.NUMERIC_LIMIT_CHANGED,
                        SeverityReasonCode.EVIDENCE_MAY_BE_INVALIDATED,
                    ],
                    severity_confidence=0.91,
                    audit_disposition=AuditDisposition.ESCALATED,
                )
        return None


class DefaultRule:
    """Rule 10 — Catch-all → LOW.

    Fires when no earlier rule matched.  The change is considered cosmetic or
    negligible: the node was matched, wording is nearly identical, no semantic
    signals detected.
    """

    def evaluate(self, ctx: ClassificationContext) -> ClassificationResult | None:
        return ClassificationResult(severity="low", reasons=_collect_reasons(ctx))


# ──────────────────────────────────────────────────────────────────────────────
# Rule engine
# ──────────────────────────────────────────────────────────────────────────────


class RuleEngine:
    """Evaluates a prioritised list of `ClassifierRule` objects.

    Rules are tried in insertion order.  The first rule whose `evaluate` method
    returns a non-None result wins; subsequent rules are not evaluated.
    """

    def __init__(self, rules: list[ClassifierRule]) -> None:
        self._rules = rules

    def classify(self, ctx: ClassificationContext) -> ClassificationResult:
        for rule in self._rules:
            result = rule.evaluate(ctx)
            if result is not None:
                return result
        # Unreachable when DefaultRule is in the list, but be defensive.
        return ClassificationResult(severity="low", reasons=_collect_reasons(ctx))


# ──────────────────────────────────────────────────────────────────────────────
# Public service
# ──────────────────────────────────────────────────────────────────────────────

# Module-level singleton — rules are stateless, safe to share across threads.
_DEFAULT_ENGINE = RuleEngine(
    [
        StructuralRenumberingRule(),  # Rule 0.5 — section renumbered, same text  → low
        StructuralLabelRule(),      # Rule 0   — structural label change      → low
        ReferenceNumberOnlyRule(),  # Rule 1   — reference-number-only       → low
        FormattingOnlyRule(),  # Rule 2   — formatting-only              → low
        CosmeticOnlyRule(),  # Rule 3   — cosmetic MODIFIED            → low
        AddedRemovedRule(),  # Rule 4   — ADDED / REMOVED              → context-tiered
        # Domain expert rules fire BEFORE generic MEDIUM rules so they can
        # escalate obligation/numeric/table changes from MEDIUM → HIGH.
        EMCExpertRule(),            # Rule 4.5 — EMC domain expert             → high (numeric/table/weakened)
        SafetyExpertRule(),         # Rule 4.6 — Safety domain expert          → high (numeric/weakened/procedure)
        EnvironmentalExpertRule(),  # Rule 4.7 — Environmental domain expert   → high (numeric/setup/procedure)
        EntityGraphChangeRule(),    # Rule 4.8 — entity graph high-sev entity  → high
        ObligationChangeRule(),  # Rule 5   — obligation verb change       → medium
        HighDistanceRule(),  # Rule 6   — distance > 0.75              → high
        MovedRule(),  # Rule 7   — moved node/section           → medium (always)
        SplitMergeRule(),  # Rule 8   — split/merged                 → low or medium
        ContentSignalRule(),  # Rule 9   — content drift ≤ 60 %        → medium
        DomainEntityRule(),  # Rule 9.1 — ontology entity changed      → high + reason codes
        NormativeObligationEscalationRule(),  # Rule 9.2 — obligation weakened          → medium + review
        ObligationStrengthCodeRule(),  # Rule 9.3 — obligation strengthened      → medium + code
        PresentationOnlyRule(),      # Rule 9.4 — cosmetic + formatting only   → low + PRESENTATION_ONLY
        TestProcedureChangeRule(),  # Rule 9.5 — test procedure changed        → high
        TestSetupChangeRule(),      # Rule 9.6 — test setup parameter changed  → medium
        DefaultRule(),  # Rule 10  — catch-all                    → low
    ]
)


class SeverityClassifier:
    """Stateless service that classifies a `ClassificationContext` into a severity verdict.

    Usage
    -----
    The default instance uses the pre-wired rule engine above and is suitable
    for all standard comparison pipelines::

        classifier = SeverityClassifier()
        result = classifier.classify(ctx)
        # result.severity  → "low" | "medium" | "high"
        # result.impact    → "Low" | "Medium" | "High"
        # result.reasons   → ["moved", "numeric_change", ...]

    Custom rules
    ------------
    Pass a custom `RuleEngine` to override the default rule set — useful for
    domain-specific pipelines or unit tests::

        engine = RuleEngine([MySpecialRule(), DefaultRule()])
        classifier = SeverityClassifier(engine=engine)
    """

    def __init__(self, engine: RuleEngine | None = None) -> None:
        self._engine = engine or _DEFAULT_ENGINE

    def classify(self, ctx: ClassificationContext) -> ClassificationResult:
        return self._engine.classify(ctx)
