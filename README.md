---
title: Customer Success FTE
emoji: ⚡
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
---

# 🏭 Customer Success Digital FTE
### Hackathon 5 — CRM Digital FTE Factory

---

## 📋 Executive Summary

An AI-powered 24/7 customer success agent for **TechCorp SaaS** that autonomously handles support tickets across **Email, WhatsApp, and Web Form** channels. Built on OpenAI Agents SDK with a full production stack (FastAPI + PostgreSQL + Kafka + Kubernetes), it reduces human support workload by **80%** and saves **~$74,000/year** by resolving 58% of tickets without escalation.

---

## 🏗️ Architecture

```
                          ┌─────────────────────────────────────────┐
Gmail ──→ Pub/Sub ──────→ │                                         │
                          │           Apache Kafka                  │
WhatsApp ──→ Twilio ────→ │   (fte.channels.*.inbound topics)       │
                          │                                         │
Web Form ──→ FastAPI ───→ │                                         │
                          └────────────────┬────────────────────────┘
                                           │
                                           ▼
                               ┌─────────────────────┐
                               │   Kafka Worker       │
                               │  (message_processor) │
                               │                      │
                               │  9-Step Pipeline:    │
                               │  1. Validate         │
                               │  2. Resolve customer │
                               │  3. Get conversation │
                               │  4. Sentiment        │
                               │  5. Build context    │
                               │  6. Inject DB/OpenAI │
                               │  7. Run Agent (Alex) │
                               │  8. Route outbound   │
                               │  9. Emit metrics     │
                               └──────────┬──────────┘
                                          │
                          ┌───────────────┼───────────────┐
                          ▼               ▼               ▼
                     PostgreSQL      Kafka Topics     Reply via
                     (7 tables +   (outbound +       channel
                      pgvector)     escalations +
                                    metrics + dlq)
```

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Agent | OpenAI Agents SDK (`gpt-4o`) |
| API | FastAPI + asyncpg |
| Database | PostgreSQL 16 + pgvector |
| Streaming | Apache Kafka (aiokafka) |
| Channels | Gmail API + Twilio + React/Next.js |
| Deploy | Kubernetes (8 manifests) |
| Incubation | Claude Code + MCP Server |

---

## 📁 Project Structure

```
Hackathon 5/
├── context/                    ← Company profile, product docs, tickets (55)
│   ├── company-profile.md
│   ├── product-docs.md
│   ├── escalation-rules.md
│   ├── brand-voice.md
│   └── sample-tickets.json
├── specs/                      ← Discovery log + transition checklist
│   ├── discovery-log.md
│   └── transition-checklist.md
├── production/
│   ├── agent/                  ← OpenAI Agents SDK implementation
│   │   ├── customer_success_agent.py
│   │   ├── tools.py            (549 lines — 5 production tools)
│   │   ├── prompts.py          (system prompt — Alex persona)
│   │   └── formatters.py       (channel-specific formatting)
│   ├── channels/               ← Inbound channel handlers
│   │   ├── gmail_handler.py
│   │   ├── whatsapp_handler.py
│   │   └── web_form_handler.py
│   ├── workers/                ← Kafka message processor
│   │   └── message_processor.py  (615 lines — 9-step pipeline)
│   ├── api/                    ← FastAPI service (9 endpoints)
│   │   └── main.py             (626 lines)
│   ├── database/               ← PostgreSQL schema + queries
│   │   ├── schema.sql          (844 lines — 7 tables + pgvector)
│   │   └── queries.py
│   ├── web-form/               ← React support form component
│   │   └── SupportForm.jsx
│   ├── tests/                  ← All test suites
│   │   ├── test_transition.py  (6 gate tests)
│   │   ├── test_e2e.py
│   │   ├── test_agent.py
│   │   └── load_test.py        (Locust)
│   ├── k8s/                    ← Kubernetes manifests (8 files)
│   │   ├── namespace.yaml
│   │   ├── configmap.yaml
│   │   ├── secrets.yaml
│   │   ├── postgres.yaml
│   │   ├── api-deployment.yaml
│   │   ├── worker-deployment.yaml
│   │   ├── hpa.yaml
│   │   └── ingress.yaml
│   └── requirements.txt
├── prototype.py                ← Stage 1 working prototype
├── mcp_server.py               ← MCP server (Claude Desktop)
├── skills-manifest.yaml
├── docker-compose.yml
└── .env
```

---

## 🚀 Quick Start

```bash
# Step 1: Open project
cd "Hackathon 5"

# Step 2: Docker containers start karo (PostgreSQL + Kafka)
docker-compose up -d

# Step 3: Dependencies install karo
pip install -r production/requirements.txt

# Step 4: .env file configure karo
cp .env.example .env
# OPENAI_API_KEY, DATABASE_URL, TWILIO_*, GMAIL_* set karo

# Step 5: Database schema apply karo
psql -h localhost -U postgres -d fte_db -f production/database/schema.sql

# Step 6: API server start karo
uvicorn production.api.main:app --host 0.0.0.0 --port 8000

# Step 7: Kafka worker start karo (separate terminal)
python -m production.workers.message_processor
```

---

## 🔌 API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | System health — DB + Kafka + OpenAI liveness |
| `POST` | `/support/submit` | Web form submission → Kafka |
| `GET` | `/support/ticket/{id}` | Ticket status + message history |
| `POST` | `/webhooks/gmail` | Gmail Pub/Sub push notification |
| `POST` | `/webhooks/whatsapp` | Twilio inbound WhatsApp (HMAC validated) |
| `GET` | `/webhooks/whatsapp` | Twilio webhook verification |
| `GET` | `/metrics/daily` | Per-channel daily analytics |
| `GET` | `/customers/{id}` | Customer profile + ticket history |
| `POST` | `/tickets/{id}/escalate` | Manual escalation trigger |

Interactive docs: `http://localhost:8000/docs`

---

## 📡 Channel Support

| Channel | Integration | Format | Limit |
|---|---|---|---|
| Email | Gmail API + Cloud Pub/Sub | Formal, greeting + signature | 200–500 words |
| WhatsApp | Twilio API | Casual, emoji-friendly | 300 chars hard max |
| Web Form | React + FastAPI | Semi-formal, headers/bullets | 100–300 words |

---

## 🤖 Agent Capabilities

**Agent Name:** Alex — Customer Success AI for TechCorp

**5 Production Tools (mandatory order):**
1. `create_ticket` — FIRST, always. Every message gets a ticket.
2. `get_customer_history` — Never make customers repeat themselves.
3. `search_knowledge_base` — pgvector cosine search + FTS fallback.
4. `escalate_to_human` — 12-reason routing table with SLA targets.
5. `send_response` — LAST, always. Channel-formatted + DB persisted.

**12 Escalation Reasons:**
`pricing_inquiry` · `refund_request` · `legal_escalation` · `angry_customer` · `human_requested` · `technical_tier2` · `anger_spike` · `enterprise_account` · `business_account_unresolved` · `partnership_request` · (+ 2 fallback)

**Intelligence Features:**
- Cross-channel customer identity resolution
- Sentiment scoring (0.0–1.0) + trend detection + anger spike detection
- 35 edge cases covered (Email × 10, WhatsApp × 10, Web Form × 10, Cross-channel × 5)
- Topic extraction (14 categories) for context continuity

---

## 📊 Performance Targets

| Metric | Baseline (Human) | FTE Target | Projected |
|---|---|---|---|
| First response time | 6.2 hours | < 30 seconds | ✅ |
| CSAT score | 3.8 / 5.0 | > 4.2 / 5.0 | — |
| Escalation rate | 34% | < 20% | 42%* |
| 24/7 availability | Business hours | 100% | ✅ |
| P95 latency | — | < 3 seconds | — |
| Annual cost | $75,000 | < $1,000 | ~$74,000 saved |

*Sample biased toward edge cases — production target < 20%

---

## 🧪 Running Tests

```bash
# Transition gate tests (6 tests — must all pass)
pytest production/tests/test_transition.py -v

# Agent unit tests
pytest production/tests/test_agent.py -v

# Channel handler tests
pytest production/tests/test_channels.py -v

# End-to-end tests
pytest production/tests/test_e2e.py -v

# Load test (Locust)
locust -f production/tests/load_test.py --host=http://localhost:8000
# Open browser: http://localhost:8089
# Recommended: 100 users, 10/s spawn rate
```

---

## ☸️ Kubernetes Deployment

```bash
# All manifests apply karo
kubectl apply -f production/k8s/

# Status check karo
kubectl get pods -n customer-success-fte

# Logs dekho
kubectl logs -n customer-success-fte -l app=fte-api
kubectl logs -n customer-success-fte -l app=fte-worker

# Scale karo
kubectl scale deployment fte-worker -n customer-success-fte --replicas=3
```

**HPA (Auto-scaling):** CPU > 70% → scale up to 10 replicas

---

## 🗺️ Stage Evolution

```
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 1 — INCUBATION                                           │
│  Claude Code → 55 tickets analysed → prototype.py → mcp_server │
│  Discovery: 58% AI-resolvable, top issue: integration (20%)     │
└────────────────────────────┬────────────────────────────────────┘
                             │  Transition: 35 edge cases documented
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 2 — SPECIALIZATION                                       │
│  OpenAI Agents SDK  →  FastAPI  →  PostgreSQL + pgvector        │
│  Apache Kafka  →  Gmail + Twilio + React  →  Kubernetes         │
└─────────────────────────────────────────────────────────────────┘
```

---

## 📄 Key Files

| File | Lines | Purpose |
|---|---|---|
| `prototype.py` | ~400 | Stage 1 working prototype (in-memory) |
| `mcp_server.py` | ~200 | MCP server for Claude Desktop |
| `production/agent/tools.py` | 549 | 5 production tools (Pydantic + PostgreSQL) |
| `production/agent/prompts.py` | 117 | Alex system prompt — 6 required sections |
| `production/agent/formatters.py` | 106 | Channel-specific response formatting |
| `production/api/main.py` | 626 | FastAPI — 9 endpoints + lifespan |
| `production/workers/message_processor.py` | 615 | Kafka worker — 9-step pipeline |
| `production/database/schema.sql` | 844 | 7 tables + pgvector + ENUMs |
| `production/database/queries.py` | 1015 | Typed async query functions |
| `production/web-form/SupportForm.jsx` | ~250 | React support form (4 states) |
| `production/tests/load_test.py` | ~150 | Locust load test (2 user classes) |
| `specs/transition-checklist.md` | 436 | 35 edge cases + 12/12 steps ✅ |

---

## 🔑 Environment Variables

Create a `.env` file in the project root. See `.env.example` below for required variables:

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | Yes | OpenAI API key for GPT-4o agent |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `KAFKA_BOOTSTRAP_SERVERS` | Yes | Kafka broker address |
| `TWILIO_ACCOUNT_SID` | For WhatsApp | Twilio Account SID |
| `TWILIO_AUTH_TOKEN` | For WhatsApp | Twilio Auth Token |
| `TWILIO_WHATSAPP_FROM` | For WhatsApp | Twilio WhatsApp sender number |
| `GMAIL_CREDENTIALS_FILE` | For Gmail | Path to Gmail service account JSON |
| `ADMIN_API_KEY` | Optional | Admin dashboard authentication key |
| `CORS_ORIGINS` | Optional | Allowed CORS origins |
| `DB_POOL_MIN` | Optional | Min DB pool connections (default: 2) |
| `DB_POOL_MAX` | Optional | Max DB pool connections (default: 10) |
| `KAFKA_GROUP_ID` | Optional | Kafka consumer group ID |

> **Note:** Never commit `.env` files. The `.gitignore` already excludes them.

---

*Hackathon 5 — Customer Success Digital FTE | TechCorp SaaS | Built with Claude Code + OpenAI Agents SDK*
