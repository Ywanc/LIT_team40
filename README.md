# CiteCheck

**SAL Problem Statement 2 (LIT Team 40)**

CiteCheck verifies AI-generated Singapore legal answers. Paste text that cites cases; the app extracts every case mention, confirms each judgment on [LawNet OpenLaw](https://www.lawnet.com/openlaw), and optionally fact-checks asserted holdings and direct quotes against the judgment itself.

---

## Overview

Legal AI tools often invent cases, mis-cite real ones, or misstate holdings. CiteCheck turns that into a structured audit:

1. **Extract** case names, citations, holdings, and quotes from the pasted text.
2. **Verify existence** by searching LawNet and matching parties / citations.
3. **Fact-check statements** by reading the matched judgment and scoring each claim.

The UI streams progress so rows appear as soon as cases are extracted, then update through LawNet search and statement verification.

---

## Tech stack


| Layer                  | Stack                                                                 |
| ---------------------- | --------------------------------------------------------------------- |
| **Frontend**           | React 19, Vite, CSS                                                   |
| **Backend**            | Python, FastAPI, Uvicorn                                              |
| **Browser automation** | Playwright (Chromium) against LawNet OpenLaw                          |
| **LLM**                | OpenRouter (default: DeepSeek V4 Flash) for extraction + claim checks |
| **Validation**         | Pydantic schemas for structured extraction                            |


---

## Pipeline

```
User pastes AI legal text
        │
        ▼
┌───────────────────────┐
│ 1. Case extraction    │  LLM → case name, citation, asserted holdings,
│                       │  direct quotes (per case)
└───────────┬───────────┘
            │  
            ▼
┌───────────────────────┐
│ 2. LawNet verification│  Parallel Playwright searches
│                       │  Match parties + citations →
│                       │  Case found / wrong citation /
│                       │  Multiple cases / No case found
└───────────┬───────────┘
            │  (only for confirmed single matches, if enabled)
            ▼
┌───────────────────────┐
│ 3. Statement check    │  Fetch judgment once per case
│                       │  Per statement (parallel):
│                       │    a) Select relevant sections (LLM)
│                       │    b) Verify claim vs excerpts (LLM)
│                       │  Direct quotes: string / near-match
│                       │  against judgment text
└───────────────────────┘
```

### LawNet match logic

- Search OpenLaw with the extracted case query.
- Resolve result titles to the cited parties (and citation when present).
- Outcomes: **Case found**, **Case found — wrong citation**, **Multiple cases found**, or **No case found**.

### Statement check

- Load the full judgment HTML via Playwright.
- For each asserted holding (in parallel):
  1. **Select sections** — an LLM picks the most relevant TOC headings (and optionally the headnote) for that claim. Any substantive part of the judgment can be chosen (analysis of a legal test, issues, facts when relevant, etc.). For outcome / ruling / disposition claims specifically, final headings such as Conclusion are preferred when they fit.
  2. **Verify** — a second LLM call scores the claim against those excerpts only (`supported` / `partially_supported` / `contradicted` / `unsure`), with short supporting citations.
- Direct quotes are checked separately for verbatim or near matches in the judgment text (no LLM).

Streaming endpoint: `POST /audit/stream` (SSE) — phases and per-case status updates as work proceeds. Non-streaming: `POST /audit`.

---

## Project layout

```
LIT_team40/
├── Frontend/          # React + Vite UI (CiteCheck)
├── backend/
│   ├── api.py         # FastAPI routes
│   ├── main.py        # Audit pipeline + streaming
│   ├── extract_case_items.py
│   ├── verification_check.py
│   ├── claim_check.py
│   ├── quotation_check.py
│   └── llm_client.py
└── README.md
```

---

## Setup

### Backend

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # set OPENROUTER_API_KEY
python api.py          # http://localhost:8000
```

### Frontend

```bash
cd Frontend
npm install
npm run dev            # Vite proxies /api → backend :8000
```

Open the Vite URL (usually `http://localhost:5173`), paste AI text, and click **Verify citations**.