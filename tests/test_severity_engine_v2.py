"""Tests for Phase B severity engine upgrade.

Covers new enums, extended ClassificationContext/Result, and domain-specific rules.
Backward compatibility: existing severity values must not change.
"""

from __future__ import annotations

import pytest

from grc_policy_server.services.comparison.severity_classifier import (
    AuditDisposition,
    ClassificationContext,
    ClassificationResult,
    DefaultRule,
    DomainEntityRule,
    NormativeObligationEscalationRule,
    ObligationStrengthCodeRule,
    PresentationOnlyRule,
    RuleEngine,
    SemanticImpact,
    SeverityClassifier,
    SeverityReasonCode,
)


def _ctx(**overrides) -> ClassificationContext:
    defaults = dict(
        change_type="MODIFIED",
        alignment_type="exact",
        node_type="paragraph",
        distance=0.1,
        meaning_change="unchanged",
        numeric_changes=[],
        requirement_verb_change=None,
        table_changes=[],
    )
    defaults.update(overrides)
    return ClassificationContext(**defaults)


class TestNewEnums:
    def test_severity_reason_code_values_are_strings(self):
        assert SeverityReasonCode.TEST_LEVEL_CHANGED == "TEST_LEVEL_CHANGED"
        assert SeverityReasonCode.OBLIGATION_WEAKENED == "OBLIGATION_WEAKENED"
        assert SeverityReasonCode.PRESENTATION_ONLY == "PRESENTATION_ONLY"

    def test_semantic_impact_values(self):
        assert SemanticImpact.TECHNICAL == "technical"
        assert SemanticImpact.NORMATIVE == "normative"
        assert SemanticImpact.EDITORIAL == "editorial"
        assert SemanticImpact.NONE == "none"

    def test_audit_disposition_values(self):
        assert AuditDisposition.AUTO_CLASSIFIED == "auto_classified"
        assert AuditDisposition.REQUIRES_HUMAN_REVIEW == "requires_human_review"
        assert AuditDisposition.ESCALATED == "escalated"


class TestClassificationResultNewFields:
    def test_defaults_are_backward_compatible(self):
        result = ClassificationResult(severity="medium", reasons=["test"])
        assert result.severity == "medium"
        assert result.reasons == ["test"]
        assert result.impact == "Medium"
        # New fields have sensible defaults
        assert result.semantic_impact == SemanticImpact.NONE
        assert result.severity_reason_codes == []
        assert result.severity_confidence == 1.0
        assert result.audit_disposition == AuditDisposition.AUTO_CLASSIFIED

    def test_can_construct_with_new_fields(self):
        result = ClassificationResult(
            severity="high",
            reasons=["test_level"],
            semantic_impact=SemanticImpact.TECHNICAL,
            severity_reason_codes=[SeverityReasonCode.TEST_LEVEL_CHANGED],
            severity_confidence=0.95,
            audit_disposition=AuditDisposition.ESCALATED,
        )
        assert result.severity == "high"
        assert SeverityReasonCode.TEST_LEVEL_CHANGED in result.severity_reason_codes
        assert result.audit_disposition == AuditDisposition.ESCALATED


class TestClassificationContextNewFields:
    def test_new_fields_are_optional_with_defaults(self):
        ctx = _ctx()
        assert ctx.normalized_facts == []
        assert ctx.ontology_entity_type == ""

    def test_can_set_ontology_entity_type(self):
        ctx = _ctx(ontology_entity_type="FieldStrength")
        assert ctx.ontology_entity_type == "FieldStrength"


class TestDomainEntityRule:
    rule = DomainEntityRule()

    def test_fires_for_field_strength(self):
        ctx = _ctx(change_type="MODIFIED", ontology_entity_type="FieldStrength")
        result = self.rule.evaluate(ctx)
        assert result is not None
        assert result.severity == "high"
        assert SeverityReasonCode.TEST_LEVEL_CHANGED in result.severity_reason_codes
        assert result.semantic_impact == SemanticImpact.TECHNICAL
        assert result.audit_disposition == AuditDisposition.ESCALATED

    def test_fires_for_frequency_range(self):
        ctx = _ctx(change_type="MODIFIED", ontology_entity_type="FrequencyRange")
        result = self.rule.evaluate(ctx)
        assert result is not None
        assert result.severity == "high"
        assert SeverityReasonCode.FREQUENCY_RANGE_CHANGED in result.severity_reason_codes

    def test_fires_for_emission_limit(self):
        ctx = _ctx(change_type="MODIFIED", ontology_entity_type="EmissionLimit")
        result = self.rule.evaluate(ctx)
        assert result is not None
        assert SeverityReasonCode.NUMERIC_LIMIT_CHANGED in result.severity_reason_codes

    def test_fires_for_acceptance_criterion(self):
        ctx = _ctx(change_type="MODIFIED", ontology_entity_type="AcceptanceCriterion")
        result = self.rule.evaluate(ctx)
        assert result is not None
        assert result.semantic_impact == SemanticImpact.NORMATIVE

    def test_does_not_fire_when_entity_empty(self):
        ctx = _ctx(ontology_entity_type="")
        result = self.rule.evaluate(ctx)
        assert result is None

    def test_does_not_fire_for_unknown_entity(self):
        ctx = _ctx(ontology_entity_type="SomethingElse")
        result = self.rule.evaluate(ctx)
        assert result is None


class TestNormativeObligationEscalationRule:
    rule = NormativeObligationEscalationRule()

    def test_fires_on_weakened_direction(self):
        ctx = _ctx(
            meaning_change="weakened",
            requirement_verb_change={"old": "shall", "new": "should", "direction": "weakened"},
        )
        result = self.rule.evaluate(ctx)
        assert result is not None
        assert result.severity == "medium"
        assert SeverityReasonCode.OBLIGATION_WEAKENED in result.severity_reason_codes
        assert result.audit_disposition == AuditDisposition.REQUIRES_HUMAN_REVIEW

    def test_does_not_fire_on_strengthened(self):
        ctx = _ctx(
            requirement_verb_change={"old": "should", "new": "shall", "direction": "strengthened"},
        )
        result = self.rule.evaluate(ctx)
        assert result is None

    def test_does_not_fire_without_verb_change(self):
        ctx = _ctx(requirement_verb_change=None)
        result = self.rule.evaluate(ctx)
        assert result is None


class TestObligationStrengthCodeRule:
    rule = ObligationStrengthCodeRule()

    def test_fires_on_strengthened(self):
        ctx = _ctx(
            requirement_verb_change={"old": "should", "new": "shall", "direction": "strengthened"},
        )
        result = self.rule.evaluate(ctx)
        assert result is not None
        assert SeverityReasonCode.OBLIGATION_STRENGTHENED in result.severity_reason_codes
        assert result.semantic_impact == SemanticImpact.NORMATIVE

    def test_does_not_fire_on_weakened(self):
        ctx = _ctx(
            requirement_verb_change={"old": "shall", "new": "should", "direction": "weakened"},
        )
        result = self.rule.evaluate(ctx)
        assert result is None


class TestPresentationOnlyRule:
    rule = PresentationOnlyRule()

    def test_fires_when_both_flags_set(self):
        ctx = _ctx(cosmetic_change=True, formatting_only_change=True)
        result = self.rule.evaluate(ctx)
        assert result is not None
        assert result.severity == "low"
        assert SeverityReasonCode.PRESENTATION_ONLY in result.severity_reason_codes
        assert result.semantic_impact == SemanticImpact.EDITORIAL

    def test_does_not_fire_with_only_cosmetic(self):
        ctx = _ctx(cosmetic_change=True, formatting_only_change=False)
        result = self.rule.evaluate(ctx)
        assert result is None

    def test_does_not_fire_with_only_formatting(self):
        ctx = _ctx(cosmetic_change=False, formatting_only_change=True)
        result = self.rule.evaluate(ctx)
        assert result is None


class TestBackwardCompatibility:
    """Ensure existing severity classifications are unchanged when no domain fields are set."""

    classifier = SeverityClassifier()

    def test_added_still_high(self):
        ctx = _ctx(change_type="ADDED")
        assert self.classifier.classify(ctx).severity == "high"

    def test_removed_still_high(self):
        ctx = _ctx(change_type="REMOVED")
        assert self.classifier.classify(ctx).severity == "high"

    def test_high_distance_still_high(self):
        ctx = _ctx(distance=0.75)
        assert self.classifier.classify(ctx).severity == "high"

    def test_formatting_only_still_low(self):
        ctx = _ctx(formatting_only_change=True)
        assert self.classifier.classify(ctx).severity == "low"

    def test_default_still_low(self):
        ctx = _ctx()
        assert self.classifier.classify(ctx).severity == "low"

    def test_moved_still_medium(self):
        ctx = _ctx(alignment_type="moved")
        assert self.classifier.classify(ctx).severity == "medium"

    def test_domain_entity_rule_inert_without_ontology_type(self):
        """Without ontology_entity_type, domain rules do not fire."""
        ctx = _ctx(ontology_entity_type="")
        result = self.classifier.classify(ctx)
        # Should fall through to DefaultRule → low
        assert result.severity == "low"
        assert result.severity_reason_codes == []
