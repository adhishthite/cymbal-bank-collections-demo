# Cymbal Bank — Vernacular Voice Bot for Collections & Recovery

Engineering blueprint and implementation for the **Cymbal Bank** Vernacular Collections & Recovery Voice Agent.

- **Live Cloud Run Service (`cymbal-collections-voice`, `asia-south1`)**: `https://cymbal-collections-voice-355880143137.asia-south1.run.app`
  - **1-Click Browser Demo Cockpit (`/`)**: `https://cymbal-collections-voice-355880143137.asia-south1.run.app/`
  - **Web Audio Phone Receiver (`/mobile`)**: `https://cymbal-collections-voice-355880143137.asia-south1.run.app/mobile/`
  - **Live Call Monitor & Ask Negotiation Ladder (`/monitor`)**: `https://cymbal-collections-voice-355880143137.asia-south1.run.app/monitor/`
- **GitHub Repository**: `https://github.com/adhishthite/cymbal-bank-collections-demo`

This application is an outbound vernacular voice bot for gold loan interest collections. The bot speaks through the **Gemini 3.8 Live API** (`gemini-3.8-live`) on Vertex AI. A FastAPI backend on Cloud Run (`cymbal-collections-voice`) holds the deterministic 2,000-account store, Ask ladder calculation, and tool invariants. A **Gemini Enterprise** (GE) agent built with the Agent Development Kit (ADK) and a **1-Click Browser Demo Cockpit** (`/`) start calls and inspect outcomes. Calls run over WebSockets directly to the `/mobile` Web Audio receiver (`TELEPHONY_MODE=mobile`, `TWILIO=off`) with live transcript and Ask ladder visualization on `/monitor`.

---

## Architecture Overview

```
                                  +------------------------------------+
                                  |  1-Click Browser Demo Cockpit (/)  |
                                  |   & Gemini Enterprise ADK Agent    |
                                  +-----------------+------------------+
                                                    |
                                    HTTP /api/demo/* or /api/agent/*
                                                    |
                                                    v
+------------------------+        +-----------------+------------------+        +------------------------+
|   Live Call Receiver   |  WS    |         FastAPI Backend            |  WS    |    Gemini Live API     |
| (/mobile Web Audio +   | <----> |   (Deterministic State Store,      | <----> |    (gemini-3.8-live)   |
|  /monitor SSE Ladder)  |        |    Invariants & Tool Dispatcher)   |        |                        |
+------------------------+        +------------------------------------+        +------------------------+
```

### 1-Click Browser Demo Flow (`/`, `/mobile`, `/monitor`)

1. **Open the Cockpit (`/`)**: View the **Cymbal Bank — Vernacular Collections Voice Agent Cockpit** with the 3 highlighted stage scenarios (Scenario A, Scenario B, Scenario C), the top 10 priority overdue accounts, and live portfolio KPIs.
2. **Step 1 — Open & Auto-Pair `/mobile` Web Phone (New Tab)**: Generates a fresh pairing token via `POST /api/demo/pair-link` and opens `/mobile?pair=...` in a new tab. Click **Ready** on `/mobile` to arm browser Web Audio.
3. **Step 2 — Open `/monitor` Live Negotiation Ladder (New Tab)**: Opens `/monitor?t=...` in a new tab to watch the real-time conversation script, Ask ladder rungs, tool execution ledger, and decision/consent gate metrics.
4. **Step 3 — Dial Customer Now**: Click **Dial Customer Now** on any scenario card or queue row. The `/mobile` browser phone rings immediately (`Cymbal Bank — AI Collections Desk`) over WebSockets (`gemini-3.8-live`).

---

## Featured Demo Scenarios

| Scenario | Customer | Loan ID | Language | Attempt & Play | End State |
| --- | --- | --- | --- | --- | --- |
| **A** | Kavita Saxena | `GL3506468235` | Hindi | Attempt 1 (Polite Reminder) | `PAYMENT_LINK_SENT_TODAY` (full due, ₹8,612) |
| **B** | Sajeev George | `GL0106397512` | Malayalam | Attempt 2 (2-Month Floor Negotiation) | `PARTIAL_SETTLEMENT_AGREED` (Asks ₹8,185 → ₹4,911 → floor ₹3,274) |
| **C** | Venkatesh Shetty (Hubballi) | `GL6095694164` | Kannada → Hindi | Attempt 3 (Hardship & Penalty Escalation) | `ESCALATED_BRANCH_HARDSHIP` |

---

## Repository Structure

```
cymbal-bank-collections-demo/
├── AGENTS.md                       # Build rules and decisions for coding agents
├── Makefile                        # Checks, dev server, deploy and admin targets
├── Dockerfile                      # Cloud Run image: bun stage (frontend) and uv stage (backend)
├── docs/                           # Specifications and review logs
├── agent/                          # ADK agents for Gemini Enterprise (text agent, A2UI card agent)
├── backend/                        # Python 3.13 FastAPI backend, state store and Live API bridges
│   ├── pyproject.toml              # Managed with `uv`
│   ├── src/muthoot_collections/    # Backend modules (store, live_session, telephony_mobile, main)
│   └── tests/                      # Offline end-to-end tests and opt-in Live tests
├── frontend/                       # Next.js static export (Bun, React 19, TypeScript): /, /mobile, /monitor
└── README.md                       # This document
```

---

## Getting Started

### Run Checks

```bash
make test    # Offline tests: HTTP API, scripted scenarios, call plan, demo cockpit
make lint    # ruff check
make check   # ruff format --check, ruff check and offline tests
```

### Local Development

```bash
make dev     # Starts FastAPI on port 8080 with CALL_WINDOW_BYPASS=true and TELEPHONY_MODE=mobile
```
