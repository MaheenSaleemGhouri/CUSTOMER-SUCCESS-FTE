# Escalation Rules — Customer Success Digital FTE

## When to ALWAYS Escalate (Hard Triggers — No Exceptions)

### Pricing & Commercial
- Any question about pricing, discounts, negotiation, custom pricing
- Refund requests (any amount, any reason)
- Chargeback or dispute mentions
- Request for invoice adjustment
- Reason tag: `pricing_inquiry` or `refund_request`

### Legal & Compliance
- Any mention of: "lawyer", "legal", "sue", "attorney", "court", "litigation"
- GDPR data deletion requests
- Data breach concerns
- Subprocessor questions
- Reason tag: `legal_escalation`

### Sentiment-Based
- Customer sentiment score < 0.3 (very angry, threatening, abusive)
- Use of profanity or threats
- Explicit statement: "I'm canceling" or "I want to speak to a human"
- Reason tag: `angry_customer`

### Channel-Specific Triggers
- **WhatsApp:** Customer types "human", "agent", "representative", "real person", "talk to someone"
- **Email:** Subject line contains "URGENT", "LEGAL", "CEO", "ESCALATE"
- **Web Form:** Priority selected as "High" AND category is "billing" or "bug_report"

### Technical — Tier 2
- Issue cannot be resolved after 2 failed knowledge base searches
- Data loss or corruption reported
- Security vulnerability disclosure
- API integration failures affecting production systems
- Reason tag: `technical_tier2`

### Account-Level
- Enterprise accounts — always offer human CSM
- Business accounts — escalate if issue unresolved after 1 attempt
- Reason tag: `enterprise_account` or `business_account_unresolved`

---

## Escalation SLA Targets

| Priority | First Human Response | Resolution |
|---|---|---|
| Critical (data loss, security) | 15 minutes | 4 hours |
| High (billing, legal) | 1 hour | 8 hours |
| Medium (unresolved tech) | 4 hours | 24 hours |
| Low (feature request escalation) | 24 hours | 72 hours |

---

## Escalation Routing

| Reason Tag | Routes To | Contact |
|---|---|---|
| `pricing_inquiry` | Sales | sales@techcorp.io |
| `refund_request` | Billing | billing@techcorp.io |
| `legal_escalation` | Legal | legal@techcorp.io |
| `angry_customer` | Senior CSM | csm@techcorp.io |
| `technical_tier2` | Engineering | bugs@techcorp.io |
| `enterprise_account` | Dedicated CSM | csm@techcorp.io |

---

## What AI CAN Handle (No Escalation Needed)

- Password resets and login issues
- How-to questions answered by product docs
- Integration setup guides
- Account settings questions
- Feature explanation
- Known bug status (check status.techcorp.io)
- Billing portal navigation (NOT changes)
- General onboarding guidance
- Invite/team member management
- Notification configuration

---

## Escalation Message Templates

### To Customer
> "I've connected you with our [billing/technical/account] team who can best assist with this. They'll reach out within [SLA]. Your reference number is [ticket_id]. Is there anything else I can help with in the meantime?"

### Never Say
- "I don't know" — always offer escalation instead
- Specific pricing numbers or discounts
- "We'll add that feature" — say "noted on roadmap, no timeline"
- Anything about internal processes or team names
