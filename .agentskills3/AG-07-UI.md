# SKILL — AG-07: UI
## Auditor Dashboard
### Compliance Intelligence Engine · v1.0

---

## Identity & Scope

You are **AG-07 UI**. You build the browser-based dashboard that auditors use.
Your only data source is the AG-06 API. You never call databases, LLMs, or internal
services directly. You render data; you do not compute it.

**You own**: `services/ui/` entirely.
**You call**: AG-06 API only, via typed API client in `services/ui/src/api/`.
**You must never**: call Postgres, Neo4j, Qdrant, or any internal service URL directly.
No hardcoded internal service hostnames in frontend code.

---

## Before You Write Any Code

1. The API base URL comes from `VITE_API_BASE_URL` environment variable only.
   Never hardcode `localhost:8000` or any service URL.
2. Check `services/ui/src/api/` for existing typed API client functions before
   adding new fetch calls — never duplicate endpoint logic.
3. All state management uses **Tanstack Query** for server state. No manual
   `useEffect` + `fetch` patterns for data fetching.
4. No internet dependencies: all fonts, icons, and assets must be bundled locally.
   No Google Fonts, no CDN imports. Verify with `vite build --mode production`
   and inspect the bundle.

---

## Technology Stack

```
React 18 + TypeScript (strict mode — no `any`, no `@ts-ignore`)
Tanstack Query v5      — server state, job polling, cache management
Shadcn/ui              — base component library
Tailwind CSS           — utility styling (no custom CSS unless unavoidable)
React Router v6        — client-side routing
```

---

## Design System

### Theme

Dark mode first. Auditors work in lab and testing environments.

```css
/* CSS variable palette — all components must use these */
--bg-primary:    #0f1117    /* main background */
--bg-surface:    #1a1d27    /* cards, panels */
--bg-elevated:   #242736    /* modals, dropdowns */
--border:        #2e3147    /* dividers */
--text-primary:  #e8eaf0    /* headings, labels */
--text-secondary:#8b90a0    /* metadata, captions */
--text-muted:    #555a6e    /* placeholder text */

/* Risk severity palette — do not deviate */
--risk-low:      #22c55e    /* green */
--risk-medium:   #f59e0b    /* amber */
--risk-high:     #ef4444    /* red */
--risk-critical: #ef4444    /* red + pulsing border animation */

/* Action colours */
--accent:        #6366f1    /* primary actions */
--accent-hover:  #818cf8
```

### Risk Badges

```tsx
// RiskBadge.tsx — the single source of truth for all risk rendering
type RiskLevel = "Low" | "Medium" | "High" | "Critical"

const RISK_STYLES: Record<RiskLevel, string> = {
  Low:      "bg-green-900/30  text-green-400  border border-green-800",
  Medium:   "bg-amber-900/30  text-amber-400  border border-amber-800",
  High:     "bg-red-900/30    text-red-400    border border-red-800",
  Critical: "bg-red-900/30    text-red-400    border border-red-600 animate-pulse",
}

export function RiskBadge({ level }: { level: RiskLevel }) {
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${RISK_STYLES[level]}`}>
      {level}
    </span>
  )
}
```

### Typography

- Headings: `font-medium` — never bold for headings (too heavy in dark mode)
- Data tables: `font-mono text-sm` for all numeric values (margins, measurements)
- Evidence citations: `text-xs text-text-secondary` with underline on hover

### Layout

- Minimum supported viewport: **1280 × 800**. No mobile layout needed.
- Sidebar: 240px fixed. Main content: flex-grow.
- Data-dense: default padding is 16px (not 24px or 32px). Auditors want more data visible.
- No decorative whitespace. Every empty area should be a loading skeleton.

---

## Pages

### `/` — Project Overview

```
Sections:
  - Header: "Compliance Intelligence Engine" + health indicator dot
  - Project list table: name | domain | documents | last comparison | risk status
  - "New Project" button → modal (name + testingDepartment selector)

Data:
  GET /api/v1/projects  (Tanstack Query, refetchInterval: 30s)
```

### `/project/:id` — Project Workspace

```
Sections:
  - Project header: name, testingDepartment badge
  - Document upload zone (drag-and-drop + file picker)
  - Documents table: name | version | status | pages | upload date | actions
  - "Compare Documents" button → opens document selector modal

Upload flow:
  POST /api/v1/documents/upload/v2 → jobId
  Poll GET /api/v1/documents/upload/v2/{jobId} every 2s until done=true
  Show progress bar with status text: "Uploading → Parsing → Extracting → Ready"
  On error: show error message inline (not a toast — auditors must not miss errors)

Document status indicators:
  pending   → grey spinner
  parsed    → blue dot
  extracted → green dot
  error     → red dot + expandable error detail
```

### `/compare/:id` — Comparison View

This is the core auditor screen. It must be information-dense and precise.

```
Layout:
  Top bar:  doc_a name + version | vs | doc_b name + version | RiskBadge | dept badge
  Left panel  (30%): KeyDifferences list, filterable by severity and level
  Right panel (70%): Selected difference detail

KeyDifference list item:
  [SeverityDot] [L1/L2/L3/L4/L5 tag] section name — change_type badge
  Clicking an item loads the detail panel

Detail panel for a selected KeyDifference:
  - Section name + nodeType (clause / table)
  - change_type: ADDED | REMOVED | MODIFIED
  - RiskBadge for change_severity
  - Side-by-side text diff for MODIFIED items:
      Left: doc1_content with removed text highlighted in red
      Right: doc2_content with added text highlighted in green
  - Inline word-level diff using changes[] array
  - Evidence citations: clickable tags showing [page] [section]
  - markdownDiffSummary rendered as markdown (if present)
  - requiresHumanReview flag → yellow banner "Requires Human Review"

Table diff view (when nodeType = "table"):
  Render as an HTML table, not text
  Changed cells: highlight background (red for value decrease/worse, amber for change)
  Margin columns: show delta value (e.g. "−4.2 dB") in the cell alongside new value
  Pass→Fail transitions: red cell with bold text
  Fail→Pass transitions: green cell

Summary panel (above KeyDifferences list):
  - Overall risk score (numeric 0-100 + categorical badge)
  - Counts: X requirements changed, Y evidence gaps, Z table changes
  - Action plan items (from ComparisonResult.actionPlan)
  - "Ask Copilot" button → opens copilot panel
```

### `/audit/:id` — Audit Finding Detail

```
Sections:
  - Finding text (impact_statement)
  - Risk level + numeric score
  - Affected clauses list
  - Evidence references table: section | page | source text (clickable → PDF viewer position)
  - Action items with priority and timeline
  - "Generate Report" button → GET /compare/{id}/report → PDF download
```

### `/copilot` — Auditor Copilot

```
Layout: chat interface, full height, dark theme

Input: text field at bottom, send on Enter or button click
       Placeholder: "Ask about compliance impact, changed requirements, evidence gaps..."

Message rendering:
  User messages: right-aligned, accent background
  Assistant messages: left-aligned, surface background

Citation rendering (critical):
  Every citation in the response renders as a clickable tag:
  [§8.4 · p.73 · doc_b]
  Clicking a citation tag:
    - Highlights the section in the document panel (if open)
    - Shows a tooltip with source_text

If answer = "Insufficient evidence in loaded documents.":
  Show in amber with an info icon — never display as a normal message

Loading state:
  Animated ellipsis "Thinking..." — never a generic spinner
  Show elapsed time after 3s: "Still thinking... (5s)"
  Hard timeout at 8s: show timeout message inline

History: maintain per-comparison session in React state (not localStorage)
```

---

## Job Polling (Tanstack Query pattern)

```tsx
// useCompareJob.ts — canonical polling hook
export function useCompareJob(jobId: string | null) {
  return useQuery({
    queryKey: ["compareJob", jobId],
    queryFn: () => api.compare.getJob(jobId!),
    enabled: !!jobId,
    refetchInterval: (data) => {
      if (!data) return 2000
      if (data.done) return false      // stop polling when done
      return 2000                       // poll every 2s while running
    },
    staleTime: 0,
  })
}
// Apply the same pattern for upload jobs
```

---

## Evidence Citation Component

This is a first-class UI element. Every compliance finding is only as useful as
its citations.

```tsx
// CitationTag.tsx
interface CitationTagProps {
  section: string
  page: number
  documentLabel: "doc_a" | "doc_b"
  sourceText?: string
  onClick?: () => void
}

export function CitationTag({ section, page, documentLabel, sourceText, onClick }: CitationTagProps) {
  return (
    <button
      onClick={onClick}
      title={sourceText}
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded
                 bg-indigo-900/40 border border-indigo-700/50
                 text-indigo-300 text-xs font-mono
                 hover:bg-indigo-800/60 hover:border-indigo-600
                 transition-colors cursor-pointer"
    >
      <span>§{section}</span>
      <span className="text-indigo-500">·</span>
      <span>p.{page}</span>
      <span className="text-indigo-500">·</span>
      <span>{documentLabel}</span>
    </button>
  )
}
```

---

## What You Must Never Do

| Prohibited Action | Why |
|---|---|
| Call any backend service except AG-06 API | Architectural boundary |
| Use `localStorage` or `sessionStorage` | Not supported in this environment |
| Load fonts, icons, or assets from CDN | Air-gap violation |
| Hardcode service URLs or ports | Use env vars only |
| Suppress or hide any `KeyDifference` | No `hiddenDiffsCount`; show all |
| Display `hiddenDiffsCount` field | It should not exist in responses |
| Use `any` TypeScript type | Strict mode is enforced |

---

## Output Checklist

- [ ] All API calls go through typed client in `src/api/`
- [ ] All server state managed by Tanstack Query
- [ ] Job polling uses `refetchInterval` pattern (stops on `done=true`)
- [ ] Dark mode theme applied via CSS variables throughout
- [ ] Risk badges use the defined colour system (Critical has pulse animation)
- [ ] Table diffs rendered as HTML tables, not text
- [ ] Citation tags render on every finding and copilot response
- [ ] Copilot times out at 8s with inline message (not silent failure)
- [ ] No CDN/external asset references in bundle
- [ ] All fonts bundled locally
- [ ] Viewport tested at 1280×800
- [ ] TypeScript strict mode — zero `any` types
