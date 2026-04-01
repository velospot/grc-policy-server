import pytest

from grc_policy_server.models.schemas import DocumentReference, KeyDifference
from grc_policy_server.services.llm.ollama_client import (
    OllamaClient,
    PROMPT_MARKDOWN_DIFF_CLAUSE,
    PROMPT_MARKDOWN_DIFF_TABLE,
    PROMPT_MARKDOWN_DIFF_SUMMARY,  # back-compat alias for PROMPT_MARKDOWN_DIFF_CLAUSE
)


def test_compact_diffs_for_summary_concatenates_section_content():
    client = OllamaClient()
    diffs = [
        KeyDifference(
            changeType="MODIFIED",
            section="Access Control",
            doc1Content="Admins must use MFA.",
            doc2Content="Admins and vendors must use MFA.",
            impact="High",
            doc1Reference=DocumentReference(
                section="Access Control",
                page=2,
                sourceText="Admins must use MFA.",
            ),
            doc2Reference=DocumentReference(
                section="Access Control",
                page=2,
                sourceText="Admins and vendors must use MFA.",
            ),
        ),
        KeyDifference(
            changeType="MODIFIED",
            section="Access Control",
            doc1Content="Privileged accounts require manager approval.",
            doc2Content="Privileged accounts require security approval.",
            impact="High",
            doc1Reference=DocumentReference(
                section="Access Control",
                page=2,
                sourceText="Privileged accounts require manager approval.",
            ),
            doc2Reference=DocumentReference(
                section="Access Control",
                page=2,
                sourceText="Privileged accounts require security approval.",
            ),
        ),
    ]

    compact = client._compact_diffs_for_summary(diffs)

    assert len(compact) == 2
    assert "impact" not in compact[0]
    assert "Admins must use MFA." in compact[0]["doc1SectionContent"]
    assert (
        "Privileged accounts require manager approval."
        in compact[0]["doc1SectionContent"]
    )
    assert "Admins and vendors must use MFA." in compact[0]["doc2SectionContent"]
    assert (
        "Privileged accounts require security approval."
        in compact[0]["doc2SectionContent"]
    )


def test_prompt_summarize_changes_uses_detected_language():
    client = OllamaClient()
    prompt = client._prompt_summarize_changes(
        doc1_name="Dokument A",
        doc2_name="Dokument B",
        diffs=[],
        language="de",
    )

    assert "Sie sind Compliance-Analyst." in prompt
    assert "Dokument A: Dokument A" in prompt
    assert "Dokument B: Dokument B" in prompt
    assert "Zusammenfassung:" in prompt
    assert "Summary:" not in prompt


def test_prompt_summarize_changes_falls_back_to_english_for_unknown_language():
    client = OllamaClient()
    prompt = client._prompt_summarize_changes(
        doc1_name="Policy V1",
        doc2_name="Policy V2",
        diffs=[],
        language="unknown",
    )

    assert "You are a compliance analyst." in prompt
    assert "Document A: Policy V1" in prompt
    assert "Document B: Policy V2" in prompt
    assert "Summary:" in prompt
    assert "Zusammenfassung:" not in prompt
    assert "Résumé :" not in prompt


def test_prompt_summarize_changes_uses_canonical_diffs_json():
    client = OllamaClient()
    prompt = client._prompt_summarize_changes(
        doc1_name="A",
        doc2_name="B",
        diffs=[{"b": 2, "a": 1}],
        language="en",
    )

    assert 'Differences JSON:\n[{"a":1,"b":2}]' in prompt


@pytest.mark.anyio
async def test_detect_language_deterministic_german():
    client = OllamaClient()
    text = "Die Richtlinie legt fest, dass alle Benutzer MFA verwenden müssen."

    detected = await client.detect_language(text)

    assert detected == "de"


@pytest.mark.anyio
async def test_detect_language_deterministic_french():
    client = OllamaClient()
    text = "La politique exige que les utilisateurs doivent activer l'accès sécurisé."

    detected = await client.detect_language(text)

    assert detected == "fr"


# ---------------------------------------------------------------------------
# markdownDiffSummary — schema field
# ---------------------------------------------------------------------------


def test_key_difference_markdown_diff_summary_is_optional():
    diff = KeyDifference(
        changeType="MODIFIED",
        section="3.1 Access Control",
        doc1Content="MFA is recommended.",
        doc2Content="MFA is required.",
        impact="High",
        doc1Reference=DocumentReference(
            section="3.1", page=1, sourceText="MFA is recommended."
        ),
        doc2Reference=DocumentReference(
            section="3.1", page=1, sourceText="MFA is required."
        ),
    )

    assert diff.markdownDiffSummary is None


def test_key_difference_markdown_diff_summary_accepts_string():
    summary = "- <span style=\"color:red\">~~MFA is recommended.~~</span> → <span style=\"color:green\">**MFA is required.**</span>"
    diff = KeyDifference(
        changeType="MODIFIED",
        section="3.1 Access Control",
        doc1Content="MFA is recommended.",
        doc2Content="MFA is required.",
        impact="High",
        doc1Reference=DocumentReference(
            section="3.1", page=1, sourceText="MFA is recommended."
        ),
        doc2Reference=DocumentReference(
            section="3.1", page=1, sourceText="MFA is required."
        ),
        markdownDiffSummary=summary,
    )

    assert diff.markdownDiffSummary == summary


# ---------------------------------------------------------------------------
# _prompt_markdown_diff_summary — prompt builder
# ---------------------------------------------------------------------------


def test_prompt_markdown_diff_summary_added_uses_doc2_text():
    client = OllamaClient()
    prompt = client._prompt_markdown_diff_summary(
        node_type="clause",
        change_type="ADDED",
        doc1_source_text=None,
        doc2_source_text="All vendors must sign the NDA before access is granted.",
    )

    assert "ADDED" in prompt
    assert "All vendors must sign the NDA before access is granted." in prompt
    assert "Added content:" in prompt
    assert "Before:" not in prompt
    assert "Removed content:" not in prompt


def test_prompt_markdown_diff_summary_removed_uses_doc1_text():
    client = OllamaClient()
    prompt = client._prompt_markdown_diff_summary(
        node_type="clause",
        change_type="REMOVED",
        doc1_source_text="Quarterly audits are optional.",
        doc2_source_text=None,
    )

    assert "REMOVED" in prompt
    assert "Quarterly audits are optional." in prompt
    assert "Removed content:" in prompt
    assert "Added content:" not in prompt
    assert "Before:" not in prompt


def test_prompt_markdown_diff_summary_modified_uses_both_texts():
    client = OllamaClient()
    prompt = client._prompt_markdown_diff_summary(
        node_type="clause",
        change_type="MODIFIED",
        doc1_source_text="Passwords must be rotated every 90 days.",
        doc2_source_text="Passwords must be rotated every 60 days.",
    )

    assert "MODIFIED" in prompt
    assert "Passwords must be rotated every 90 days." in prompt
    assert "Passwords must be rotated every 60 days." in prompt
    assert "Before:" in prompt
    assert "After:" in prompt


def test_prompt_markdown_diff_summary_table_uses_table_template():
    """node_type=table must select the table prompt (Row/Col instructions present)."""
    client = OllamaClient()
    prompt = client._prompt_markdown_diff_summary(
        node_type="table",
        change_type="MODIFIED",
        doc1_source_text="| Role | Access |\n| Admin | Full |",
        doc2_source_text="| Role | Access |\n| Admin | Read-only |",
    )

    assert "MODIFIED" in prompt
    assert "| Admin | Full |" in prompt
    assert "| Admin | Read-only |" in prompt
    assert "Row" in prompt
    assert "Col" in prompt


def test_prompt_markdown_diff_summary_clause_has_no_table_instructions():
    """node_type=clause must NOT include table-specific Row/Col instructions."""
    client = OllamaClient()
    prompt = client._prompt_markdown_diff_summary(
        node_type="clause",
        change_type="MODIFIED",
        doc1_source_text="Admins must use MFA.",
        doc2_source_text="All users must use MFA.",
    )

    assert "Row" not in prompt
    assert "Col" not in prompt


def test_prompt_markdown_diff_summary_clause_contains_color_formatting():
    client = OllamaClient()
    prompt = client._prompt_markdown_diff_summary(
        node_type="clause",
        change_type="ADDED",
        doc1_source_text=None,
        doc2_source_text="New clause text.",
    )

    assert "color:green" in prompt
    assert "color:red" in prompt
    assert "~~" in prompt
    assert "**" in prompt


def test_prompt_markdown_diff_summary_added_empty_doc2_graceful():
    client = OllamaClient()
    prompt = client._prompt_markdown_diff_summary(
        node_type="clause",
        change_type="ADDED",
        doc1_source_text=None,
        doc2_source_text=None,
    )

    assert "ADDED" in prompt
    assert "Added content:" in prompt


def test_prompt_markdown_diff_summary_modified_missing_one_side_graceful():
    client = OllamaClient()
    prompt = client._prompt_markdown_diff_summary(
        node_type="clause",
        change_type="MODIFIED",
        doc1_source_text="Old requirement.",
        doc2_source_text=None,
    )

    assert "Before:" in prompt
    assert "Old requirement." in prompt
    assert "After:" in prompt


# ---------------------------------------------------------------------------
# Prompt format — clause vs table templates
# ---------------------------------------------------------------------------


def test_clause_prompt_contains_semantic_only_rule():
    """Clause prompt must explicitly require semantic-only output."""
    assert "Semantic changes ONLY" in PROMPT_MARKDOWN_DIFF_CLAUSE


def test_clause_prompt_ignores_cosmetic_changes():
    assert "IGNORE cosmetic" in PROMPT_MARKDOWN_DIFF_CLAUSE


def test_clause_prompt_outputs_nothing_when_no_change():
    assert "output nothing" in PROMPT_MARKDOWN_DIFF_CLAUSE


def test_clause_prompt_preserves_source_language():
    assert "same language as the source text" in PROMPT_MARKDOWN_DIFF_CLAUSE


def test_clause_prompt_contains_color_spans():
    assert "color:red" in PROMPT_MARKDOWN_DIFF_CLAUSE
    assert "color:green" in PROMPT_MARKDOWN_DIFF_CLAUSE


def test_clause_prompt_contains_strikethrough_and_bold():
    assert "~~" in PROMPT_MARKDOWN_DIFF_CLAUSE
    assert "**" in PROMPT_MARKDOWN_DIFF_CLAUSE


def test_clause_prompt_has_no_table_instructions():
    assert "Row" not in PROMPT_MARKDOWN_DIFF_CLAUSE
    assert "Col" not in PROMPT_MARKDOWN_DIFF_CLAUSE


def test_clause_prompt_explicitly_prohibits_table_mentions():
    """Clause prompt must explicitly forbid the LLM from mentioning table-related terms."""
    assert "Do NOT mention tables" in PROMPT_MARKDOWN_DIFF_CLAUSE


def test_clause_prompt_forbids_commentary_about_inapplicable_content():
    """LLM must be explicitly forbidden from adding commentary or 'not applicable' notes."""
    assert "No explanations, commentary" in PROMPT_MARKDOWN_DIFF_CLAUSE


def test_clause_prompt_exactly_one_format():
    """Clause prompt must demand exactly one output format, never combined."""
    assert "EXACTLY ONE" in PROMPT_MARKDOWN_DIFF_CLAUSE
    assert "never combine" in PROMPT_MARKDOWN_DIFF_CLAUSE


def test_clause_prompt_offers_diff_block_as_format_b():
    """Clause prompt must include fenced diff block as one of the format options."""
    assert "```diff" in PROMPT_MARKDOWN_DIFF_CLAUSE


def test_table_prompt_contains_row_col_format():
    assert "Row R, Col C" in PROMPT_MARKDOWN_DIFF_TABLE


def test_table_prompt_contains_diff_block_syntax():
    assert "```diff" in PROMPT_MARKDOWN_DIFF_TABLE


def test_table_prompt_prohibits_standalone_inline_bullets():
    """Table prompt must explicitly forbid standalone ~~phrase~~ / **phrase** bullets."""
    assert "Do NOT use standalone inline word/phrase bullets" in PROMPT_MARKDOWN_DIFF_TABLE


def test_table_prompt_exactly_one_format_never_combined():
    """Table prompt must demand exactly one format, never combined."""
    assert "EXACTLY ONE" in PROMPT_MARKDOWN_DIFF_TABLE
    assert "never combine" in PROMPT_MARKDOWN_DIFF_TABLE


def test_table_prompt_preserves_source_language():
    assert "same language as the source text" in PROMPT_MARKDOWN_DIFF_TABLE


def test_table_prompt_outputs_nothing_when_no_change():
    assert "output nothing" in PROMPT_MARKDOWN_DIFF_TABLE


def test_prompt_markdown_diff_summary_alias_equals_clause_prompt():
    """Back-compat alias must point to the clause prompt."""
    assert PROMPT_MARKDOWN_DIFF_SUMMARY is PROMPT_MARKDOWN_DIFF_CLAUSE


# ---------------------------------------------------------------------------
# _populate_markdown_diff_summaries — RealDiffEngine integration (unit)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_populate_markdown_diff_summaries_sets_field():
    """_populate_markdown_diff_summaries assigns LLM result to each diff."""
    from grc_policy_server.services.comparision.real_diff_engine import RealDiffEngine

    class _FakeLLM:
        async def generate_markdown_diff_summary(self, *, node_type, change_type,
                                                  doc1_source_text, doc2_source_text,
                                                  language=""):
            return f"diff:{change_type}:{node_type}"

    engine = RealDiffEngine.__new__(RealDiffEngine)
    engine.llm = _FakeLLM()

    diffs = [
        KeyDifference(
            changeType="MODIFIED",
            section="3.1",
            doc1Content="old",
            doc2Content="new",
            impact="High",
            doc1Reference=DocumentReference(section="3.1", page=1, sourceText="old text"),
            doc2Reference=DocumentReference(section="3.1", page=1, sourceText="new text"),
        ),
        KeyDifference(
            changeType="ADDED",
            section="4.0",
            doc1Content=None,
            doc2Content="brand new clause",
            impact="High",
            doc1Reference=None,
            doc2Reference=DocumentReference(section="4.0", page=2, sourceText="brand new clause"),
        ),
        KeyDifference(
            changeType="REMOVED",
            section="5.0",
            doc1Content="old clause",
            doc2Content=None,
            impact="High",
            doc1Reference=DocumentReference(section="5.0", page=3, sourceText="old clause"),
            doc2Reference=None,
        ),
    ]

    await engine._populate_markdown_diff_summaries(diffs)

    assert diffs[0].markdownDiffSummary == "diff:MODIFIED:clause"
    assert diffs[1].markdownDiffSummary == "diff:ADDED:clause"
    assert diffs[2].markdownDiffSummary == "diff:REMOVED:clause"


@pytest.mark.anyio
async def test_populate_markdown_diff_summaries_tolerates_llm_failure():
    """LLM failure for one diff must not prevent others from being populated."""
    from grc_policy_server.services.comparision.real_diff_engine import RealDiffEngine

    call_count = 0

    class _FlakyLLM:
        async def generate_markdown_diff_summary(self, *, node_type, change_type,
                                                  doc1_source_text, doc2_source_text,
                                                  language=""):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("LLM timeout")
            return f"ok:{change_type}"

    engine = RealDiffEngine.__new__(RealDiffEngine)
    engine.llm = _FlakyLLM()

    diffs = [
        KeyDifference(
            changeType="MODIFIED",
            section="A",
            doc1Content="x",
            doc2Content="y",
            impact="Low",
            doc1Reference=DocumentReference(section="A", page=1, sourceText="x"),
            doc2Reference=DocumentReference(section="A", page=1, sourceText="y"),
        ),
        KeyDifference(
            changeType="ADDED",
            section="B",
            doc1Content=None,
            doc2Content="z",
            impact="Low",
            doc1Reference=None,
            doc2Reference=DocumentReference(section="B", page=1, sourceText="z"),
        ),
    ]

    await engine._populate_markdown_diff_summaries(diffs)

    assert diffs[0].markdownDiffSummary is None  # failed call → stays None
    assert diffs[1].markdownDiffSummary == "ok:ADDED"


@pytest.mark.anyio
async def test_populate_markdown_diff_summaries_uses_source_text_from_references():
    """LLM must receive sourceText from doc1Reference/doc2Reference."""
    from grc_policy_server.services.comparision.real_diff_engine import RealDiffEngine

    captured: list[dict] = []

    class _CaptureLLM:
        async def generate_markdown_diff_summary(self, *, node_type, change_type,
                                                  doc1_source_text, doc2_source_text,
                                                  language=""):
            captured.append({
                "node_type": node_type,
                "change_type": change_type,
                "doc1": doc1_source_text,
                "doc2": doc2_source_text,
                "language": language,
            })
            return "summary"

    engine = RealDiffEngine.__new__(RealDiffEngine)
    engine.llm = _CaptureLLM()

    diff = KeyDifference(
        changeType="MODIFIED",
        section="1.1",
        doc1Content="short",
        doc2Content="short updated",
        impact="Medium",
        nodeType="table",
        doc1Reference=DocumentReference(section="1.1", page=1, sourceText="full source old"),
        doc2Reference=DocumentReference(section="1.1", page=1, sourceText="full source new"),
    )

    await engine._populate_markdown_diff_summaries([diff])

    assert len(captured) == 1
    assert captured[0]["doc1"] == "full source old"
    assert captured[0]["doc2"] == "full source new"
    assert captured[0]["node_type"] == "table"
    assert captured[0]["change_type"] == "MODIFIED"
    assert captured[0]["language"] == ""  # default when not passed


@pytest.mark.anyio
async def test_populate_markdown_diff_summaries_stream_engine():
    """Same populate logic works in RealDiffEngineStream."""
    from grc_policy_server.services.comparision.real_diff_engine_stream import RealDiffEngineStream

    class _FakeLLM:
        async def generate_markdown_diff_summary(self, *, node_type, change_type,
                                                  doc1_source_text, doc2_source_text,
                                                  language=""):
            return f"stream:{change_type}"

    engine = RealDiffEngineStream.__new__(RealDiffEngineStream)
    engine.llm = _FakeLLM()

    diffs = [
        KeyDifference(
            changeType="REMOVED",
            section="2.0",
            doc1Content="gone",
            doc2Content=None,
            impact="High",
            doc1Reference=DocumentReference(section="2.0", page=1, sourceText="gone"),
            doc2Reference=None,
        ),
    ]

    await engine._populate_markdown_diff_summaries(diffs)

    assert diffs[0].markdownDiffSummary == "stream:REMOVED"


@pytest.mark.anyio
async def test_populate_markdown_diff_summaries_empty_diffs_no_error():
    """Empty diff list must complete without error."""
    from grc_policy_server.services.comparision.real_diff_engine import RealDiffEngine

    class _FakeLLM:
        async def generate_markdown_diff_summary(self, *, node_type, change_type,
                                                  doc1_source_text, doc2_source_text,
                                                  language=""):
            return "should not be called"

    engine = RealDiffEngine.__new__(RealDiffEngine)
    engine.llm = _FakeLLM()

    await engine._populate_markdown_diff_summaries([])  # must not raise


@pytest.mark.anyio
async def test_populate_markdown_diff_summaries_passes_language():
    """language kwarg must be forwarded to each LLM call."""
    from grc_policy_server.services.comparision.real_diff_engine import RealDiffEngine

    received_languages: list[str] = []

    class _LangCaptureLLM:
        async def generate_markdown_diff_summary(self, *, node_type, change_type,
                                                  doc1_source_text, doc2_source_text,
                                                  language=""):
            received_languages.append(language)
            return "diff"

    engine = RealDiffEngine.__new__(RealDiffEngine)
    engine.llm = _LangCaptureLLM()

    diffs = [
        KeyDifference(
            changeType="MODIFIED",
            section="1",
            doc1Content="alt",
            doc2Content="neu",
            impact="High",
            doc1Reference=DocumentReference(section="1", page=1, sourceText="alt"),
            doc2Reference=DocumentReference(section="1", page=1, sourceText="neu"),
        ),
    ]

    await engine._populate_markdown_diff_summaries(diffs, language="de")

    assert received_languages == ["de"]


def test_prompt_markdown_diff_summary_prepends_must_language_for_known_language():
    """Known language code must prepend a mandatory MUST instruction as the first line."""
    client = OllamaClient()
    prompt_de = client._prompt_markdown_diff_summary(
        node_type="clause",
        change_type="MODIFIED",
        doc1_source_text="Jeder Benutzer muss MFA aktivieren.",
        doc2_source_text="Alle Benutzer müssen MFA verwenden.",
        language="de",
    )
    prompt_fr = client._prompt_markdown_diff_summary(
        node_type="clause",
        change_type="MODIFIED",
        doc1_source_text="Les utilisateurs doivent activer MFA.",
        doc2_source_text="Tous les utilisateurs doivent utiliser MFA.",
        language="fr",
    )

    assert prompt_de.startswith("IMPORTANT:")
    assert "MUST" in prompt_de
    assert "German" in prompt_de
    assert prompt_fr.startswith("IMPORTANT:")
    assert "French" in prompt_fr


def test_prompt_markdown_diff_summary_no_must_instruction_for_unknown_language():
    """Unknown or empty language must NOT prepend an explicit MUST instruction."""
    client = OllamaClient()
    prompt = client._prompt_markdown_diff_summary(
        node_type="clause",
        change_type="MODIFIED",
        doc1_source_text="Old text.",
        doc2_source_text="New text.",
        language="unknown",
    )

    assert not prompt.startswith("IMPORTANT:")
    assert "MUST write" not in prompt


@pytest.mark.anyio
async def test_generate_markdown_diff_summary_returns_empty_on_no_change():
    """LLM response signalling no change must be normalised to empty string."""
    client = OllamaClient()

    async def _fake_generate(prompt, temperature=None):
        return "(no semantic change)"

    client._generate_text = _fake_generate  # type: ignore[method-assign]

    result = await client.generate_markdown_diff_summary(
        node_type="clause",
        change_type="MODIFIED",
        doc1_source_text="Access control policy.",
        doc2_source_text="Access control policy.",
    )

    assert result == ""


@pytest.mark.anyio
async def test_populate_markdown_diff_summaries_sets_none_on_empty_result():
    """Empty string from LLM must leave markdownDiffSummary as None."""
    from grc_policy_server.services.comparision.real_diff_engine import RealDiffEngine

    class _NoChangeLLM:
        async def generate_markdown_diff_summary(self, *, node_type, change_type,
                                                  doc1_source_text, doc2_source_text,
                                                  language=""):
            return ""  # signals no semantic change

    engine = RealDiffEngine.__new__(RealDiffEngine)
    engine.llm = _NoChangeLLM()

    diff = KeyDifference(
        changeType="MODIFIED",
        section="1",
        doc1Content="same",
        doc2Content="same",
        impact="Low",
        doc1Reference=DocumentReference(section="1", page=1, sourceText="same"),
        doc2Reference=DocumentReference(section="1", page=1, sourceText="same"),
    )

    await engine._populate_markdown_diff_summaries([diff])

    assert diff.markdownDiffSummary is None
