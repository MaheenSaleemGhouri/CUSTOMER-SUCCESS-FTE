# Exercise 1.1 — Discovery Log
## Customer Success Digital FTE | Incubation Phase

**Date:** 2026-03-06
**Analyst:** Agent Factory (Claude)
**Source Files Analyzed:**
- `context/company-profile.md`
- `context/product-docs.md`
- `context/sample-tickets.json` (55 tickets)
- `context/escalation-rules.md`
- `context/brand-voice.md`

---

## 1. BUSINESS CONTEXT SUMMARY

**Company:** TechCorp Inc. — B2B SaaS project management platform
**ARR:** $8.2M | **Customers:** 4,200 active SMBs | **Monthly ticket volume:** ~1,800
**Current State:** CSAT 3.8/5.0 | 6.2hr avg response time | 34% escalation rate
**Target State:** 24/7 FTE at <$1,000/year | <3s processing | >85% accuracy | <20% escalation

### Plan Tiers (Relevant to Responses)
| Plan | Users | Key Limitation |
|---|---|---|
| Starter | 5 | No integrations, 5GB storage |
| Growth | 20 | Integrations, 50GB, WhatsApp support |
| Business | 100 | SSO, audit logs, API 1,000 req/hr |
| Enterprise | Unlimited | Custom SLA, on-prem, 10,000 req/hr API |

**Critical note:** Plan restrictions are a common source of confusion — 3 tickets (T003, T016, T018) arise because customers try features unavailable on their tier.

---

## 2. TICKET DISTRIBUTION ANALYSIS

### Volume by Channel
| Channel | Count | % | Pattern |
|---|---|---|---|
| Email | 15 | 27% | Formal, complex, escalation-heavy |
| WhatsApp | 20 | 36% | Casual, short, self-service friendly |
| Web Form | 20 | 36% | Detailed, structured, technical-heavy |

### Priority Distribution
| Priority | Count | % | Escalation Rate |
|---|---|---|---|
| Critical | 5 | 9% | **100%** — data loss, legal, security |
| High | 14 | 25% | **79%** — billing, angry, enterprise |
| Medium | 21 | 38% | **48%** — mixed |
| Low | 15 | 27% | **33%** — mostly self-service |

### Sentiment Distribution
| Bucket | Range | Count | % | Behavior |
|---|---|---|---|---|
| Angry | < 0.3 | 12 | 22% | 92% escalate |
| Neutral | 0.3–0.6 | 27 | 49% | 55% escalate |
| Happy | > 0.6 | 16 | 29% | 81% resolve via self-service |

**Key insight:** Sentiment is the strongest predictor of escalation, stronger than issue category.

---

## 3. ISSUE TAXONOMY

| Category | Count | % | Primary Channel | Resolution |
|---|---|---|---|---|
| Integration Issues | 11 | 20% | Web Form, Email | Mixed — depends on plan |
| Account/Auth/Access | 9 | 16% | All three | Mostly resolve |
| Billing/Pricing | 8 | 15% | Email, Web Form | Always escalate |
| Feature Capability (How-To) | 10 | 18% | WhatsApp, Web Form | Always resolve |
| Legal/Compliance | 6 | 11% | Email | Always escalate |
| Critical/Data Loss | 5 | 9% | All three | Always escalate |
| Feature Requests | 1 | 2% | Web Form | Acknowledge + log |

### Top 5 Most Common Issues
1. **Integration setup/failure** (11 tickets) — Slack, Teams, GitHub, Zapier, Azure AD
2. **Account/Auth** (9 tickets) — password reset, 2FA, permissions, lockout
3. **Billing/Pricing** (8 tickets) — refunds, upgrades, duplicate charges
4. **General how-to** (10 tickets) — feature explanations, plan capabilities
5. **Legal/Compliance** (6 tickets) — GDPR, FERPA, security disclosures

---

## 4. CHANNEL-SPECIFIC ANALYSIS

### 4.1 Email Channel

**Typical message length:** 185 words average (range: 35–240 words)

**Tone profile:**
- 100% formal address ("Dear [Name]," or "Hello [Name],")
- Professional business language
- Multi-paragraph with context and history
- Complex multi-part questions common (T004 asks about user count + storage pricing)

**Subject line patterns:**
- Specific and descriptive (never generic)
- Legal/urgency markers: "GDPR Data Deletion Request", "Notice of Intent", "URGENT"
- Problem statements: "Cannot reset my password", "Accidentally deleted a project"

**Issue distribution in Email:**
- Billing/Commercial: 4 tickets (27%)
- Legal/Compliance: 4 tickets (27%)
- Technical: 3 tickets (20%)
- Account/Onboarding: 4 tickets (27%)

**Sentiment profile:** Bimodal — either very low (legal/billing anger) or high (upgrade inquiries). Few neutral email tickets.

**Required response style:**
- Greeting: `Dear [Name],` always
- Structure: Acknowledge → Solution/Steps → Next steps → Signature
- Length: 200–500 words
- Format: Numbered lists for steps, bullets for options
- Signature: `Best regards, TechCorp Support Team | support@techcorp.io | techcorp.io/support`
- Never: emoji, slang, abbreviations, passive voice overuse

---

### 4.2 WhatsApp Channel

**Typical message length:** 40 characters average (range: 17–97 chars)

**Tone profile:**
- All lowercase common
- Abbreviations rare but present ("q" for "question")
- Conversational: questions without punctuation
- Emoji used for emotional amplification (😤 for anger, 👋 for greeting)

**Urgency indicators:**
- ALL CAPS: T005 ("COMPLETELY BROKEN!!", "RIGHT NOW")
- Double exclamation: T005, T020 ("disappeared!!")
- Short message length correlates with urgency or extreme frustration

**Issue distribution in WhatsApp:**
- How-to / Feature questions: 6 tickets (30%)
- Known issues / Status checks: 3 tickets (15%)
- Technical help: 5 tickets (25%)
- Escalation (angry/human request): 5 tickets (25%)

**Sentiment profile:** More balanced than email. Casual language masks moderate frustration (0.3–0.5). High sentiment users (0.7–0.85) use WhatsApp for quick questions.

**Hard escalation triggers in WhatsApp (per rules):**
- "human", "agent", "representative", "real person", "talk to someone" → IMMEDIATE escalate
- Sentiment < 0.3 → IMMEDIATE escalate

**Required response style:**
- Greeting: "Hi [Name]! 👋" or skip if simple answer
- Length: Target 160 chars, HARD MAX 300 chars per message
- Multi-message: Preferred over wall of text
- Emoji: 1–2 per message max, natural only
- No numbered lists — use plain text steps
- Never: walls of text, overly formal, passive voice
- Close: Optional brief sign-off "Let me know if you need help! 😊"

---

### 4.3 Web Form Channel

**Typical message length:** 140 words average (range: 50–220 words)

**Tone profile:**
- Structured problem statements (2–4 paragraphs)
- Steps already attempted provided upfront
- Error messages quoted verbatim (T015: "Invalid assertion consumer service URL", T021: "HMAC-SHA256")
- Technical detail proportional to user's technical level

**Category distribution:**
- Technical: 8 tickets (40%)
- Billing/Account: 6 tickets (30%)
- General: 5 tickets (25%)
- Feedback: 1 ticket (5%)

**Sentiment profile:** More measured than email — customers frustrated but professional. Lowest-sentiment web form tickets (T054: 0.05) use legal threats but maintain grammatical professionalism.

**Required response style:**
- No greeting required — get to the answer
- Structure: Direct answer → Supporting steps → Resource links → Offer further help
- Length: 100–300 words
- Format: Can use headers + bullets but keep clean
- Always close with: "Visit our support portal at techcorp.io/support"
- Never: Undefined jargon, passive voice overuse

---

## 5. ESCALATION PATTERN ANALYSIS

### Overall Escalation Rate: 42% (23/55 tickets)
*(Higher than baseline 34% — sample biased toward edge cases)*

### Escalation Routing Summary
| Reason Tag | Routing | Ticket Count | Target SLA |
|---|---|---|---|
| `legal_escalation` | legal@techcorp.io | 5 | 15 minutes |
| `refund_request` | billing@techcorp.io | 4 | 1 hour |
| `pricing_inquiry` | sales@techcorp.io | 6 | 24 hours |
| `angry_customer` | csm@techcorp.io | 3 | 1 hour |
| `technical_tier2` | bugs@techcorp.io | 5 | 4 hours |
| `enterprise_account` | csm@techcorp.io | 3 | 1 hour |
| `human_requested` | csm@techcorp.io | 1 | 1 hour |
| `partnership_request` | partnerships | 1 | 24 hours |

### Hard Escalation Triggers (Never Override)
1. Any pricing question → `pricing_inquiry`
2. Any refund request → `refund_request`
3. Legal keywords: "lawyer", "legal", "sue", "attorney", "court" → `legal_escalation`
4. GDPR/FERPA/compliance legal requests → `legal_escalation`
5. Security vulnerability disclosure → `legal_escalation` (route to security@)
6. Sentiment < 0.3 → `angry_customer`
7. WhatsApp: "human", "agent", "representative" → `human_requested`
8. Data loss or corruption reported → `technical_tier2`
9. Enterprise account unresolved → `enterprise_account`
10. 2+ failed knowledge searches → `technical_tier2`

### What AI CAN Resolve (No Escalation Needed)
- Password resets and login help
- 2FA setup guidance
- How-to questions covered by product docs
- Integration setup walkthroughs
- Known bug status (status.techcorp.io reference)
- Plan feature explanations (including "not in your plan" with upgrade note)
- Team member management
- Notification setup
- Data retention policies
- Export instructions
- Onboarding guidance

---

## 6. EDGE CASES CATALOG

### Email Edge Cases (10)
| # | Ticket | Edge Case | Handling |
|---|---|---|---|
| E1 | T022 | Formal legal threat letter (data breach claim) | Immediate escalate → legal@, acknowledge receipt only |
| E2 | T007 | GDPR Article 17 formal erasure request | Escalate → legal@, acknowledge 30-day legal obligation |
| E3 | T034 | Responsible disclosure XSS vulnerability | Escalate → security@, not legal@ (different routing) |
| E4 | T025 | SOC 2 audit documentation request (non-customer evaluating) | Resolve+escalate → provide basic info, route to sales |
| E5 | T037 | FERPA compliance request (school district) | Escalate → legal@, not standard support |
| E6 | T049 | Duplicate billing charge (fraud-like complaint) | Escalate → billing@, high urgency |
| E7 | T028 | Project deletion recovery — no backup | Escalate → tier2 (might need engineering access) |
| E8 | T004 | Multi-part: billing dispute + pricing question | Escalate → billing@ (both triggers in one ticket) |
| E9 | T013 | Enterprise pricing + volume discount negotiation | Escalate → sales@, warm handoff with context |
| E10 | T043 | Partnership inquiry from non-customer | Route to partnerships@, outside standard support |

### WhatsApp Edge Cases (10)
| # | Ticket | Edge Case | Handling |
|---|---|---|---|
| W1 | T005 | Critical anger + data loss + manager demand in one message | Immediate senior CSM escalation + empathy first |
| W2 | T014 | "I need a real person" — no context provided | Escalate; ask for issue description while routing |
| W3 | T044 | 2FA lockout — time-sensitive, no details provided | Ask for account email + backup code situation |
| W4 | T020 | Tasks "disappeared" — ambiguous (deleted vs filtered) | Ask clarifying question before solution |
| W5 | T032 | Angry emoji (😤) as primary frustration indicator | Treat as low sentiment; provide empathy first |
| W6 | T011 | "Again" implies prior incident — customer expects known issue awareness | Acknowledge known issue (Gantt PDF bug), workaround + fix timeline |
| W7 | T017 | "zoom integration down?" — ambiguous (asking or reporting?) | Respond to both interpretations in one short message |
| W8 | T026 | Feature not yet available (offline mode) asked casually | Honest answer, no false promises, roadmap note |
| W9 | T050 | Follow-up status check — may have prior ticket open | Check customer history first before replying |
| W10 | T002 | Error without error message — need more info | Ask for error message/screenshot before troubleshooting |

### Web Form Edge Cases (10)
| # | Ticket | Edge Case | Handling |
|---|---|---|---|
| F1 | T054 | Legal threat + account suspension + data loss (all combined) | Immediate escalate → legal@; acknowledge urgency |
| F2 | T030 | Churn notification framed as support request | Escalate → CSM for win-back attempt, not just process cancellation |
| F3 | T048 | On-prem deployment request — Enterprise feature needing custom scope | Escalate → enterprise CSM + sales, not standard support |
| F4 | T036 | Multi-month refund ($597) with "underutilization" justification | Escalate → billing@, note customer's reason |
| F5 | T009 | API rate limit exceeds plan — needs upgrade/custom | Escalate → CSM + sales, explain Enterprise option |
| F6 | T024 | Excel export blank (payroll-critical) — time-sensitive | Provide CSV workaround immediately, escalate to tier2 in parallel |
| F7 | T015 | SSO Azure AD failure with specific error message | Attempt troubleshooting first; escalate to tier2 if 2 steps fail |
| F8 | T042 | Webhook fires on test but not live — silent failure | Complex debugging needed; escalate to tier2 with all context |
| F9 | T051 | Feature request mentioning competitor (Asana) by name | Acknowledge, log for product team; NEVER discuss Asana |
| F10 | T045 | Cancellation + data preservation question | Resolve (explain 30-day preservation + export option); no billing escalation needed |

### Additional Edge Cases (5 — cross-channel patterns)
| # | Pattern | Example | Handling |
|---|---|---|---|
| X1 | Plan-gated feature confusion | T003 (Slack on Starter), T018 (audit logs on Growth) | Always explain plan limitation + upgrade path, never just "no" |
| X2 | Empty or near-empty messages | Hypothetical: "help" or "?" | Ask clarifying question: "Hi! Happy to help — what are you trying to do?" |
| X3 | Cross-channel follow-up | T050 follows T011 on same issue | Check customer history first; recognize returning customer |
| X4 | Issue cannot be reproduced | T006 (Gantt empty for customer only) | Request screen recording or specific project ID to replicate |
| X5 | Competitor comparison | T051 (Asana), T053 (Notion) | Acknowledge feature gap honestly; highlight TechCorp strengths; no competitor disparagement |

---

## 7. RESOLUTION DECISION FRAMEWORK

```
Incoming Ticket
     │
     ├── Sentiment < 0.3? ──────────────────────────► ESCALATE (angry_customer)
     │
     ├── Legal keywords present? ────────────────────► ESCALATE (legal_escalation)
     │
     ├── Pricing/refund/discount question? ──────────► ESCALATE (pricing_inquiry / refund_request)
     │
     ├── Data loss reported? ─────────────────────────► ESCALATE (technical_tier2)
     │
     ├── WhatsApp: "human/agent/representative"? ─────► ESCALATE (human_requested)
     │
     ├── Enterprise account + unresolved? ───────────► ESCALATE (enterprise_account)
     │
     ├── How-to question? ────────────────────────────► RESOLVE (knowledge_base search)
     │
     ├── Auth/password/access issue? ────────────────► RESOLVE (guided steps)
     │
     ├── Feature capability question? ───────────────► RESOLVE (explain + upgrade note if plan-gated)
     │
     ├── Known bug/issue? ────────────────────────────► RESOLVE (workaround + timeline)
     │
     ├── Technical issue (integration)? ─────────────► TRY TO RESOLVE (2 attempts)
     │                                                         │
     │                                                  Unresolved?
     │                                                         ▼
     │                                                  ESCALATE (technical_tier2)
     │
     └── Feature request? ────────────────────────────► ACKNOWLEDGE + LOG
```

---

## 8. SKILLS REQUIRED BY THE FTE

Based on analysis, the FTE needs these 5 core skills:

| Skill | Triggered By | Action |
|---|---|---|
| `search_knowledge_base` | Any how-to or feature question | Query docs, return relevant answer |
| `create_ticket` | Every single interaction | ALWAYS first tool called |
| `get_customer_history` | Cross-channel follow-ups, repeat issues | Check prior tickets before responding |
| `escalate_to_human` | Any hard trigger condition | Route with reason tag and context |
| `send_response` | After resolution/escalation message formed | ALWAYS last tool called |

---

## 9. PERFORMANCE BASELINE OBSERVATIONS

**From company-profile.md:**
- Current avg first response: 6.2 hours → FTE target: <30 seconds
- Current CSAT: 3.8/5.0 → FTE target: >4.2/5.0 (estimated)
- Current escalation rate: 34% → FTE target: <20%
- Monthly ticket volume: ~1,800 → FTE handles: estimated 1,000–1,200 (the 67% resolvable)

**Predicted AI-Resolvable Without Escalation:**
- Based on sample: 58% of tickets resolve without escalation
- Applied to 1,800/mo: ~1,044 tickets auto-resolved per month
- Human load reduced to: ~756 tickets/month (down from 1,800)
- Estimated CSM time savings: 63% reduction in Tier-1 volume

---

## 10. CLARIFYING QUESTIONS FOR DIRECTOR

Before writing the prototype, I need answers on:

1. **Customer identification across channels:** Primary key = email. WhatsApp customers may never provide email. Do we use phone number as fallback primary key? Or require email confirmation first?

2. **Prototype LLM:** The constitution says production uses `gpt-4o`. For prototype.py (Stage 1), should I also use `gpt-4o` or a lighter model for cost efficiency during iteration?

3. **Knowledge base seeding:** The 5 context files are the knowledge base for now. Any additional product documentation to include before prototype build?

4. **WhatsApp "human" escalation:** When a customer says "human" on WhatsApp, do we: (a) immediately close the channel and say a human will call, or (b) keep the chat open and let the human agent take over mid-conversation?

5. **Response language:** All sample tickets are English. Multi-language support required? Or English-only for this hackathon?

---

## DISCOVERY LOG SUMMARY

| Dimension | Finding |
|---|---|
| Total tickets analyzed | 55 |
| Channels | 3 (email 27%, WhatsApp 36%, web form 36%) |
| Escalation rate | 42% (sample-biased; real target <20%) |
| Top issue | Integration setup/failure (20% of volume) |
| Highest risk category | Legal/data loss (critical, always escalate) |
| Sentiment predictor strength | High — best single predictor of escalation |
| Edge cases documented | 35 total (10 per channel + 5 cross-channel) |
| Skills needed | 5 core (search, ticket, history, escalate, respond) |
| Channel style delta | Highest between Email (200+ words) and WhatsApp (40 chars) |
| Biggest gotcha | Plan-gated features cause confusion on Starter/Growth tiers |

**Ready for Exercise 1.2 — Prototype Build.**

---

*Discovery Log v1.0 | Exercise 1.1 Complete*
