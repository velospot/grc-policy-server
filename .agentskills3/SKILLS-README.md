# SKILLS — Compliance Intelligence Engine
## Agent Skill Files Index · v1.0

---

## What Is This Directory?

Each file in this directory is a **skill** — a self-contained instruction document
that a coding agent loads at the start of a task. Skills define exactly what an
agent owns, what it must do, how it must behave, and what it must never do.

**Every agent reads their skill file before writing any code.**
**Every agent reads `AGENTS.md` for cross-cutting contracts.**

---

## Agent Skills

| File | Agent | One-Line Role |
|------|-------|---------------|
| `AG-01-INGEST.md` | INGEST | PDF → ParsedDocument (OCR, parse, structure) |
| `AG-02-EXTRACT.md` | EXTRACT | ParsedDocument → Requirements + Evidence + Graph |
| `AG-03-STORE.md` | STORE | All DB schemas, migrations, repository interfaces |
| `AG-04-COMPARE.md` | COMPARE | 5-level deterministic diff engine |
| `AG-05-REASON.md` | REASON | Compliance impact, risk scoring, auditor copilot |
| `AG-06-API.md` | API | FastAPI gateway, job orchestration, domain mapping |
| `AG-07-UI.md` | UI | React auditor dashboard |
| `AG-08-INFRA.md` | INFRA | Docker Compose, GPU config, setup scripts |
| `AG-09-TEST.md` | TEST | Unit, integration, performance, AI eval harnesses |

---

## How To Use These Skills

### For a coding agent

```
1. Receive a task assignment (e.g. "Implement L5 table comparison")
2. Read AGENTS.md — identify which agent owns this task (AG-04 COMPARE)
3. Read the skill file for that agent: AG-04-COMPARE.md
4. Read shared contracts in services/shared/models/ that the task touches
5. Implement, following the skill's rules
6. Run the output checklist at the bottom of the skill file before submitting
```

### For a task router / orchestrator

Assign tasks based on the ownership table in `AGENTS.md §4`. Each agent's skill
file is the authoritative source for what that agent will and will not do.
Cross-boundary tasks must be explicitly coordinated — identify the owning agent,
have them implement the interface, then have the calling agent consume it.

---

## Non-Negotiable Rules (apply to all agents)

These rules apply regardless of which skill file you are reading:

1. **Air-gap**: no outbound HTTP at runtime. No model downloads. No CDN references.
2. **Structure first**: work with parsed structured objects, never raw text chunks.
3. **Evidence driven**: every compliance finding links to `{document_id, section, page}`.
4. **Deterministic before AI**: implement rule-based version first; LLM is last resort.
5. **Traceability**: all detected differences must be surfaced — `hiddenDiffsCount` is prohibited.
6. **GPU mutex**: every LLM inference call must wrap `acquire_gpu()`. No exceptions.
7. **testingDepartment**: domain-specific prompts selected by `testingDepartment`. Missing prompt = hard error.
8. **saveToDb defaults True**: all comparisons persisted by default.
9. **Document.size is int**: bytes, never a display string.

---

*Last updated: 2025-06 | Platform Version: 1.0 | Skills Version: 1.0*
