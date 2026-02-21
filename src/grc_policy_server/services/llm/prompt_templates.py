from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import ChatOllama

from grc_policy_server.core.config import settings

# 1) Prompt template (ChatPromptTemplate)
PROMPT_SUMMARIZE_DIFF = ChatPromptTemplate.from_messages(
    [
        ("system", "You are a GRC compliance analyst."),
        (
            "human",
            """\
Task: Summarize the change in one paragraph, strictly grounded in the provided texts.

Rules:
- Use ONLY the OLD and NEW text below.
- Do NOT invent new requirements, dates, or sections.
- If change is ambiguous, say so.

Section: {section}

OLD:
{old_text}

NEW:
{new_text}

Write a concise change summary and (if clear) the likely compliance impact.
""",
        ),
    ]
)

llm = ChatOllama(
    model=settings.ollama_generation_model,  # change to whatever you pulled in ollama
    temperature=0.0,  # helps reduce creative “compliance hallucinations”
    base_url=settings.ollama_url,
)

# 3) Chain + invoke
chain = PROMPT_SUMMARIZE_DIFF | llm


def summarize_diff(*, old_text: str, new_text: str, section: str) -> str:
    resp = chain.invoke(
        {"old_text": old_text, "new_text": new_text, "section": section}
    )
    return str(resp.content)
