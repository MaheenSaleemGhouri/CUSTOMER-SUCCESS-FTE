"""
prototype.py — Exercise 1.3 | Customer Success Digital FTE
Stage 1: Incubation | Memory + Sentiment Tracking + Cross-Channel Identity

Builds on Exercise 1.2 (channel-aware agent) by adding:
  - Conversation memory window (last N turns injected into every agent call)
  - Sentiment history + trend analysis (improving / stable / worsening)
  - Topic extraction and frequency tracking
  - Cross-channel identity resolver (email ↔ phone → same customer)
  - Session management + anger spike detection
  - Rich context prompt built from full customer history

Usage:
    python prototype.py                   # interactive mode
    python prototype.py --demo            # run multi-turn demo scenarios
    python prototype.py --ticket T001     # run single demo ticket
    python prototype.py --scenario cross  # demo cross-channel continuity
"""

import asyncio
import json
import uuid
import re
import os
import argparse
import textwrap
from collections import defaultdict
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from agents import Agent, Runner, function_tool


# ═══════════════════════════════════════════════════
# CONSTANTS & ENUMS
# ═══════════════════════════════════════════════════

class Channel(str, Enum):
    EMAIL    = "email"
    WHATSAPP = "whatsapp"
    WEB_FORM = "web_form"


MEMORY_WINDOW        = 10    # max turns kept in active context
SENTIMENT_HISTORY_CAP = 20   # max sentiment data points stored
SESSION_TIMEOUT_MINS  = 30   # minutes of inactivity → new session

ESCALATION_ROUTING = {
    "pricing_inquiry":             "sales@techcorp.io",
    "refund_request":              "billing@techcorp.io",
    "legal_escalation":            "legal@techcorp.io",
    "angry_customer":              "csm@techcorp.io",
    "technical_tier2":             "bugs@techcorp.io",
    "enterprise_account":          "csm@techcorp.io",
    "business_account_unresolved": "csm@techcorp.io",
    "human_requested":             "csm@techcorp.io",
    "partnership_request":         "partnerships@techcorp.io",
    "anger_spike":                 "csm@techcorp.io",
}

ESCALATION_SLA = {
    "critical": "15 minutes",
    "high":     "1 hour",
    "medium":   "4 hours",
    "low":      "24 hours",
}

LEGAL_KEYWORDS   = {"lawyer", "legal", "sue", "attorney", "court", "litigation"}
HUMAN_KEYWORDS   = {"human", "agent", "representative", "real person", "talk to someone"}
PRICING_KEYWORDS = {"pricing", "price", "cost", "discount", "negotiat", "quote"}
REFUND_KEYWORDS  = {"refund", "chargeback", "dispute", "invoice adjustment", "money back",
                    "charged twice", "duplicate charge"}

WHATSAPP_MAX_CHARS = 300
EMAIL_MAX_WORDS    = 500
WEBFORM_MAX_WORDS  = 300


# ═══════════════════════════════════════════════════
# TOPIC EXTRACTION  (1.3 NEW)
# ═══════════════════════════════════════════════════

TOPIC_KEYWORDS: dict[str, list[str]] = {
    "password_reset":     ["password", "reset", "forgot", "login", "sign in", "locked out"],
    "two_factor_auth":    ["2fa", "two factor", "authenticator", "totp", "backup code"],
    "billing":            ["billing", "invoice", "charge", "payment", "subscription", "plan", "price"],
    "refund":             ["refund", "money back", "credit", "chargeback"],
    "integration_slack":  ["slack", "slack integration", "slack notification"],
    "integration_github": ["github", "gitlab", "pull request", "pr", "branch"],
    "integration_zapier": ["zapier", "zap", "webhook", "automation"],
    "integration_teams":  ["teams", "microsoft teams", "ms teams"],
    "integration_zoom":   ["zoom", "meeting link"],
    "integration_sso":    ["sso", "saml", "single sign-on", "azure ad", "okta"],
    "team_management":    ["invite", "team member", "permission", "role", "admin", "member", "guest"],
    "storage":            ["storage", "file", "upload", "5gb", "50gb"],
    "gantt_pdf_bug":      ["gantt", "pdf", "export", "error 500"],
    "notifications":      ["notification", "alert", "email notification", "slack notif"],
    "calendar_view":      ["calendar", "calendar view", "task disappear", "missing task"],
    "data_loss":          ["lost data", "deleted", "missing data", "disappeared", "recover", "restore"],
    "legal_compliance":   ["gdpr", "ferpa", "legal", "lawyer", "attorney", "compliance", "data deletion"],
    "security":           ["security", "vulnerability", "xss", "breach", "soc 2", "penetration"],
    "api":                ["api", "rate limit", "token", "endpoint", "webhook"],
    "onboarding":         ["getting started", "new user", "how do i", "tutorial", "guide"],
    "mobile_app":         ["mobile", "ios", "android", "app"],
    "feature_request":    ["feature request", "would love", "please add", "roadmap", "suggestion"],
    "plan_upgrade":       ["upgrade", "growth plan", "business plan", "enterprise"],
    "cancellation":       ["cancel", "cancellation", "leave", "switching to"],
    "custom_templates":   ["template", "custom template", "project template"],
    "export":             ["export", "download", "csv", "excel"],
}


def extract_topics(message: str) -> list[str]:
    """Return list of detected topic tags from a message."""
    text   = message.lower()
    topics = []
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            topics.append(topic)
    return topics


# ═══════════════════════════════════════════════════
# SENTIMENT ENGINE  (1.3 ENHANCED)
# ═══════════════════════════════════════════════════

ANGRY_SIGNALS = {
    "broken", "terrible", "horrible", "unacceptable", "worst",
    "angry", "furious", "frustrated", "ridiculous", "useless",
    "scam", "hate", "awful", "pathetic", "incompetent", "disgusting",
}
HAPPY_SIGNALS = {
    "thank", "thanks", "great", "love", "amazing", "perfect",
    "excellent", "wonderful", "helpful", "appreciate", "pleased",
    "happy", "quick question", "awesome", "brilliant",
}


def estimate_sentiment(message: str) -> float:
    """Estimate sentiment 0.0 (angry) → 1.0 (happy) from message text."""
    text = message.lower()

    # Hard-floor for legal/threat language
    if any(kw in text for kw in LEGAL_KEYWORDS):
        return 0.1
    # Hard-floor for caps rage signals
    caps_words = len(re.findall(r'\b[A-Z]{3,}\b', message))
    if caps_words >= 3:
        return max(0.0, 0.25 - caps_words * 0.05)

    angry_count = sum(1 for w in ANGRY_SIGNALS if w in text)
    happy_count = sum(1 for w in HAPPY_SIGNALS if w in text)

    # Exclamation marks push score down slightly
    exclamations = message.count("!")
    score = 0.6 - (angry_count * 0.15) + (happy_count * 0.1) - (exclamations * 0.03)
    return max(0.0, min(1.0, score))


def get_sentiment_trend(history: list[float]) -> str:
    """
    Analyse sentiment trend from history list.
    Returns: 'improving' | 'stable' | 'worsening' | 'unknown'
    """
    if len(history) < 2:
        return "unknown"
    recent = history[-3:]   # last 3 data points
    older  = history[:-3] if len(history) > 3 else history[:1]
    recent_avg = sum(recent) / len(recent)
    older_avg  = sum(older)  / len(older)
    delta = recent_avg - older_avg
    if delta > 0.1:
        return "improving"
    if delta < -0.1:
        return "worsening"
    return "stable"


def detect_anger_spike(history: list[float]) -> bool:
    """
    Return True if the most recent sentiment dropped sharply (anger spike).
    Threshold: last score < 0.25 AND previous score was >= 0.4
    """
    if len(history) < 2:
        return False
    return history[-1] < 0.25 and history[-2] >= 0.4


# ═══════════════════════════════════════════════════
# CROSS-CHANNEL IDENTITY RESOLVER  (1.3 NEW)
# ═══════════════════════════════════════════════════

# Maps any identifier (phone, alias email) → canonical customer_id
CUSTOMER_ALIASES: dict[str, str] = {}


def resolve_identity(raw_id: str) -> str:
    """Resolve any alias to the canonical customer_id."""
    return CUSTOMER_ALIASES.get(raw_id.lower().strip(), raw_id.lower().strip())


def register_alias(alias_id: str, canonical_id: str) -> None:
    """
    Link an alias (e.g. phone number) to a canonical customer_id (e.g. email).
    Enables cross-channel continuity: same customer on WhatsApp + Email → one profile.
    """
    alias_id    = alias_id.lower().strip()
    canonical_id = canonical_id.lower().strip()
    if alias_id != canonical_id:
        CUSTOMER_ALIASES[alias_id] = canonical_id
        print(f"  [IDENTITY] Linked {alias_id} → {canonical_id}")


# ═══════════════════════════════════════════════════
# IN-MEMORY STATE STORE  (1.3 ENHANCED)
# ═══════════════════════════════════════════════════

CUSTOMER_STATES: dict[str, dict] = {}
TICKETS:         dict[str, dict] = {}
ESCALATIONS:     dict[str, dict] = {}
RESPONSES:       dict[str, list] = {}


def _new_session_id() -> str:
    return f"SES-{uuid.uuid4().hex[:6].upper()}"


def get_or_create_state(customer_id: str, channel: Channel) -> dict:
    """
    Return (or create) the full state object for a customer.
    Handles:
      - Identity resolution (aliases)
      - Session management (timeout → new session, increment counter)
      - Channel switch tracking
    """
    canonical_id = resolve_identity(customer_id)

    if canonical_id not in CUSTOMER_STATES:
        # First contact — create fresh state
        CUSTOMER_STATES[canonical_id] = {
            # Identity
            "customer_id":      canonical_id,
            "aliases":          [customer_id] if customer_id != canonical_id else [],

            # Channel history
            "original_channel": channel.value,
            "channel_switches": [],
            "channels_used":    [channel.value],

            # Conversation memory
            "conversation_history": [],   # full rolling window (MEMORY_WINDOW turns)

            # Sentiment tracking
            "sentiment_score":   0.65,    # start neutral-positive
            "sentiment_history": [],      # list of floats, capped at SENTIMENT_HISTORY_CAP
            "sentiment_trend":   "unknown",

            # Topic tracking
            "topics_discussed":   [],     # deduped list of all topics ever raised
            "topic_frequency":    {},     # topic → count

            # Session management
            "session_id":       _new_session_id(),
            "session_count":    1,
            "session_start":    datetime.utcnow().isoformat(),
            "last_seen":        datetime.utcnow().isoformat(),

            # Resolution tracking
            "resolution_status":  "pending",
            "escalation_count":   0,
            "successful_resolutions": 0,

            # Misc
            "created_at":       datetime.utcnow().isoformat(),
            "kb_search_count":  0,
        }
    else:
        state = CUSTOMER_STATES[canonical_id]

        # Session timeout check → start new session if inactive > SESSION_TIMEOUT_MINS
        last_seen = datetime.fromisoformat(state["last_seen"])
        if datetime.utcnow() - last_seen > timedelta(minutes=SESSION_TIMEOUT_MINS):
            state["session_id"]    = _new_session_id()
            state["session_count"] += 1
            state["session_start"] = datetime.utcnow().isoformat()
            state["kb_search_count"] = 0  # reset search counter for new session

        # Track channel switch
        if channel.value not in state["channels_used"]:
            state["channel_switches"].append({
                "from": state["channels_used"][-1],
                "to":   channel.value,
                "at":   datetime.utcnow().isoformat(),
            })
            state["channels_used"].append(channel.value)

        # Register alias if different from canonical
        if customer_id != canonical_id and customer_id not in state.get("aliases", []):
            state.setdefault("aliases", []).append(customer_id)

        state["last_seen"] = datetime.utcnow().isoformat()

    return CUSTOMER_STATES[canonical_id]


def update_sentiment(state: dict, message: str) -> float:
    """
    Compute sentiment for the new message, update state's running average
    and history. Return the new score and detect anger spikes.
    """
    new_score = estimate_sentiment(message)

    # Running weighted average (recent messages weighted more)
    state["sentiment_score"] = (state["sentiment_score"] * 0.55) + (new_score * 0.45)

    # Append to history (cap at SENTIMENT_HISTORY_CAP)
    history = state.setdefault("sentiment_history", [])
    history.append(round(new_score, 3))
    if len(history) > SENTIMENT_HISTORY_CAP:
        history.pop(0)

    # Update trend
    state["sentiment_trend"] = get_sentiment_trend(history)

    return new_score


def update_topics(state: dict, message: str) -> list[str]:
    """Extract topics from message, update frequency map and deduped list."""
    new_topics = extract_topics(message)
    freq = state.setdefault("topic_frequency", {})
    for t in new_topics:
        freq[t] = freq.get(t, 0) + 1
        if t not in state["topics_discussed"]:
            state["topics_discussed"].append(t)
    return new_topics


def add_to_memory(state: dict, role: str, content: str, channel: str, topics: list[str] = None) -> None:
    """
    Append a turn to conversation_history with a sliding window.
    Keeps the last MEMORY_WINDOW turns in memory.
    """
    history = state.setdefault("conversation_history", [])
    history.append({
        "role":      role,
        "content":   content,
        "channel":   channel,
        "topics":    topics or [],
        "ts":        datetime.utcnow().isoformat(),
        "session_id": state.get("session_id", ""),
    })
    # Trim to window
    if len(history) > MEMORY_WINDOW:
        state["conversation_history"] = history[-MEMORY_WINDOW:]


# ═══════════════════════════════════════════════════
# KNOWLEDGE BASE
# ═══════════════════════════════════════════════════

KNOWLEDGE_BASE = [
    {
        "id": "kb_password_reset", "category": "authentication",
        "title": "Password Reset",
        "content": (
            "To reset your password: 1) Go to app.techcorp.io/login 2) Click 'Forgot Password' "
            "3) Enter your email and check inbox 4) Link expires in 24 hours. "
            "If no email: check spam, whitelist noreply@techcorp.io. Contact support if missing after 5 min."
        ),
    },
    {
        "id": "kb_2fa_setup", "category": "authentication",
        "title": "Two-Factor Authentication",
        "content": (
            "Enable 2FA: Settings → Security → Two-Factor Authentication. "
            "Supports TOTP (Google Authenticator, Authy) and SMS. Save backup codes immediately. "
            "Locked out: use backup codes or contact support for manual verification."
        ),
    },
    {
        "id": "kb_add_team_member", "category": "team_management",
        "title": "Adding Team Members",
        "content": (
            "Add member: Settings → Team → Invite Member → enter email + role. Expires in 7 days. "
            "Resend: Settings → Team → Pending Invites. "
            "Roles: Owner (full), Admin (manage team/settings), Member (projects), Guest (limited). "
            "Can't see projects after accepting? Check project-level permissions in project Settings."
        ),
    },
    {
        "id": "kb_slack_integration", "category": "integrations",
        "title": "Slack Integration",
        "content": (
            "Slack integration: Growth plan and above only (NOT Starter). "
            "Setup: Settings → Integrations → Slack → Connect → OAuth → choose channel → configure events. "
            "Known: 2-5 min delay during peak hours. If not working: disconnect and reconnect. "
            "Starter plan customers must upgrade to Growth to use Slack."
        ),
    },
    {
        "id": "kb_gantt_pdf_bug", "category": "known_issues",
        "title": "Gantt PDF Export Bug",
        "content": (
            "KNOWN ISSUE: Gantt PDF export intermittently fails with error 500. "
            "Fix releasing in v3.2.1 next Tuesday. "
            "Workaround: Use CSV export instead — same data, opens in Excel/Sheets."
        ),
    },
    {
        "id": "kb_plans", "category": "plans",
        "title": "Plan Features and Limits",
        "content": (
            "Starter ($29/mo): 5 users, 5GB, no integrations, email support. "
            "Growth ($79/mo): 20 users, 50GB, all integrations, WhatsApp support. "
            "Business ($199/mo): 100 users, SSO, audit logs, API 1K req/hr. "
            "Enterprise (custom): unlimited users, on-prem, API 10K req/hr, custom SLA. "
            "Upgrades: immediate. Downgrades: next billing cycle."
        ),
    },
    {
        "id": "kb_storage", "category": "plans",
        "title": "Storage Limits",
        "content": (
            "Storage full: new uploads blocked. Existing files unaffected. "
            "Free space: Settings → Storage → delete unused files. "
            "Upgrade to increase: Growth=50GB, Business=100GB, Enterprise=unlimited. "
            "Billing: app.techcorp.io/settings/billing"
        ),
    },
    {
        "id": "kb_billing", "category": "billing",
        "title": "Billing and Subscriptions",
        "content": (
            "Billing portal: app.techcorp.io/settings/billing. View invoices, update payment, change plan. "
            "Refunds and invoice adjustments: handled by billing team, NOT by AI — billing@techcorp.io. "
            "Upgrades: immediate. Cancellations: active until period end, then frozen."
        ),
    },
    {
        "id": "kb_data_retention", "category": "account",
        "title": "Data Retention on Cancellation",
        "content": (
            "After cancel: active until period end → archived 30 days → permanently deleted. "
            "Export before canceling: Settings → Export → Download Everything (CSV/JSON). "
            "Resubscribe within 30 days: full data restored. After 30 days: unrecoverable."
        ),
    },
    {
        "id": "kb_notifications", "category": "features",
        "title": "Notification Settings",
        "content": (
            "Manage: Settings → Notifications. Types: in-app, email, Slack, MS Teams. "
            "Not working? Check: Settings → Notifications enabled, browser permissions for in-app, "
            "and Settings → Integrations for Slack/Teams connectivity."
        ),
    },
    {
        "id": "kb_export", "category": "features",
        "title": "Export Options",
        "content": (
            "CSV: Reports → select → Export → CSV. Excel: same path → Excel. "
            "PDF: available for most views (Gantt PDF has known bug). "
            "Full export: Settings → Export → Download Everything."
        ),
    },
    {
        "id": "kb_github", "category": "integrations",
        "title": "GitHub PR Linking",
        "content": (
            "GitHub (Growth+ only): Settings → Integrations → GitHub. "
            "Link PR to task: include [TASK-ID] or #TASK-ID in PR title or description. "
            "Example: 'Fix login bug [TASK-123]'. Appears in task Activity tab within 2-3 min. "
            "Ensure correct repository selected in integration settings."
        ),
    },
    {
        "id": "kb_sso", "category": "security",
        "title": "SSO SAML Setup",
        "content": (
            "SSO: Business plan+. Settings → Security → Single Sign-On. "
            "ACS URL: https://app.techcorp.io/auth/saml/callback (exact — no trailing slash). "
            "Entity ID: https://app.techcorp.io/saml/metadata. "
            "Azure AD 'Reply URL' = ACS URL. Common error: mismatched URL or trailing slash."
        ),
    },
    {
        "id": "kb_api", "category": "developers",
        "title": "API and Webhooks",
        "content": (
            "API: Business+ only. Docs: docs.techcorp.io/api. Token: Settings → API → New Token. "
            "Rate limits: Business=1,000/hr, Enterprise=10,000/hr. "
            "Webhooks: Settings → Webhooks → Add Endpoint. "
            "Signature: HMAC-SHA256. hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest(). "
            "Compare with X-TechCorp-Signature. Use raw bytes — don't decode body before hashing."
        ),
    },
    {
        "id": "kb_mobile", "category": "features",
        "title": "Mobile App",
        "content": (
            "Mobile app (iOS/Android) is in beta. Join: techcorp.io/beta. "
            "No confirmed general release date — users will be notified."
        ),
    },
    {
        "id": "kb_offline", "category": "features",
        "title": "Offline Mode",
        "content": (
            "Offline mode is under consideration — not currently available. "
            "TechCorp requires an internet connection. No confirmed timeline."
        ),
    },
    {
        "id": "kb_calendar", "category": "features",
        "title": "Calendar View — Tasks Not Showing",
        "content": (
            "Tasks not in Calendar: 1) Tasks need a due date to appear. "
            "2) Check filters (top-right) — filters may hide tasks. "
            "3) Verify date range shown (use nav arrows). "
            "4) Ensure project is not archived."
        ),
    },
    {
        "id": "kb_templates", "category": "features",
        "title": "Project Templates",
        "content": (
            "40+ built-in templates: New Project → Browse Templates. "
            "Workaround for custom templates: build ideal structure → Duplicate Project for reuse. "
            "Custom template creation from scratch: on roadmap, no release date."
        ),
    },
    {
        "id": "kb_workspaces", "category": "account",
        "title": "Multiple Workspaces",
        "content": (
            "One workspace per account. For isolation: use separate projects + role-based permissions, "
            "or create separate accounts. Multi-workspace per account: roadmap, no date."
        ),
    },
    {
        "id": "kb_security", "category": "security",
        "title": "Security and Compliance",
        "content": (
            "Certifications: SOC 2 Type II, GDPR, CCPA. Encryption: AES-256 at rest, TLS 1.3. "
            "SOC 2 reports, DPA, pen-test results: sales@techcorp.io (NDA required). "
            "GDPR data deletion: legal@techcorp.io."
        ),
    },
    {
        "id": "kb_onboarding", "category": "onboarding",
        "title": "Getting Started",
        "content": (
            "Quick start: 1) New Project → + New Project. 2) Add tasks → open project → + Add Task. "
            "3) Invite team: Settings → Team → Invite Member. "
            "4) Views: List, Kanban, Calendar, Gantt. 5) Notifications: Settings → Notifications. "
            "Tutorials: techcorp.io/support."
        ),
    },
    {
        "id": "kb_zapier", "category": "integrations",
        "title": "Zapier Integration",
        "content": (
            "Zapier (Growth+): Settings → Integrations → Zapier. "
            "If webhook fires on manual test but not live events: check event filter settings, "
            "verify endpoint URL in Zapier, check TechCorp webhook logs in Settings → Webhooks. "
            "May need to reconnect and re-select trigger events."
        ),
    },
    {
        "id": "kb_ms_teams", "category": "integrations",
        "title": "Microsoft Teams Integration",
        "content": (
            "MS Teams (Growth+): Settings → Integrations → Microsoft 365 → Teams. "
            "Not posting updates? Verify: integration shows 'Connected', correct Teams channel selected, "
            "events are checked in integration settings. Try disconnect → reconnect."
        ),
    },
]


# ═══════════════════════════════════════════════════
# CHANNEL FORMATTER
# ═══════════════════════════════════════════════════

def format_response(response: str, channel: Channel, customer_name: Optional[str] = None) -> str:
    """Apply channel-specific formatting rules to the response."""
    name = customer_name or "Customer"

    if channel == Channel.EMAIL:
        if not response.strip().startswith(("Dear", "Hello")):
            response = f"Dear {name},\n\n{response}"
        if "Best regards" not in response:
            response = (
                f"{response}\n\n"
                "Best regards,\n"
                "TechCorp Support Team\n"
                "support@techcorp.io | techcorp.io/support"
            )

    elif channel == Channel.WHATSAPP:
        if len(response) > WHATSAPP_MAX_CHARS:
            trimmed = response[:WHATSAPP_MAX_CHARS]
            last_break = max(trimmed.rfind(". "), trimmed.rfind("! "), trimmed.rfind("? "))
            if last_break > int(WHATSAPP_MAX_CHARS * 0.6):
                response = trimmed[:last_break + 1]
            else:
                response = trimmed.rstrip() + "..."
        if "Type 'human' for live support" not in response:
            response += "\n\n📱 Type 'human' for live support."

    elif channel == Channel.WEB_FORM:
        if "techcorp.io/support" not in response:
            response += (
                "\n\n---\n"
                "Need more help? Visit our support portal at techcorp.io/support "
                "or reply to this message."
            )

    return response


# ═══════════════════════════════════════════════════
# CONTEXT BUILDER  (1.3 NEW)
# ═══════════════════════════════════════════════════

def build_agent_context(
    message: str,
    channel: Channel,
    customer_id: str,
    state: dict,
    customer_name: Optional[str] = None,
    subject: Optional[str] = None,
    current_topics: Optional[list[str]] = None,
) -> str:
    """
    Build the full enriched context string to inject into the agent prompt.
    Includes: customer profile, sentiment, session info, conversation history window.
    """
    history = state.get("conversation_history", [])
    trend   = state.get("sentiment_trend", "unknown")
    score   = state.get("sentiment_score", 0.65)
    topics  = state.get("topics_discussed", [])
    freq    = state.get("topic_frequency", {})
    session = state.get("session_count", 1)
    aliases = state.get("aliases", [])
    channels_used = state.get("channels_used", [channel.value])

    # Sentiment interpretation
    if score < 0.3:
        sentiment_label = "VERY ANGRY"
    elif score < 0.5:
        sentiment_label = "Frustrated"
    elif score < 0.7:
        sentiment_label = "Neutral"
    else:
        sentiment_label = "Positive"

    # Build conversation history block (last MEMORY_WINDOW turns)
    history_block = ""
    if history:
        history_block = "\n[CONVERSATION HISTORY — last {} turns]\n".format(len(history))
        for turn in history[-MEMORY_WINDOW:]:
            role_label = "Customer" if turn["role"] == "user" else "Agent"
            channel_tag = f"[{turn['channel'].upper()}]" if turn.get("channel") else ""
            content_preview = turn["content"][:200] + ("..." if len(turn["content"]) > 200 else "")
            history_block += f"  {role_label} {channel_tag}: {content_preview}\n"

    # Top topics by frequency
    top_topics = sorted(freq.items(), key=lambda x: x[1], reverse=True)[:5]
    topic_str  = ", ".join(f"{t}(×{c})" for t, c in top_topics) if top_topics else "none yet"

    # Channel switch info
    switches = state.get("channel_switches", [])
    switch_str = " → ".join(channels_used) if len(channels_used) > 1 else channels_used[0]

    context = f"""
[CHANNEL]: {channel.value.upper()}
[CUSTOMER_ID]: {customer_id}
[CUSTOMER_NAME]: {customer_name or "Unknown"}
[CANONICAL_ID]: {resolve_identity(customer_id)}
[KNOWN_ALIASES]: {", ".join(aliases) if aliases else "none"}

[SENTIMENT]: {score:.2f} / 1.0 — {sentiment_label}
[SENTIMENT_TREND]: {trend}
[SENTIMENT_HISTORY_LAST_5]: {state.get("sentiment_history", [])[-5:]}

[SESSION]: #{session} | Session ID: {state.get("session_id", "?")}
[CHANNEL_JOURNEY]: {switch_str}
[TOPICS_DISCUSSED]: {", ".join(topics) if topics else "none"}
[TOP_TOPICS_BY_FREQUENCY]: {topic_str}
[CURRENT_MESSAGE_TOPICS]: {", ".join(current_topics) if current_topics else "none detected"}
{history_block}
[SUBJECT]: {subject or "N/A"}

[NEW MESSAGE]:
{message}
"""
    return context.strip()


# ═══════════════════════════════════════════════════
# THE FIVE TOOLS
# ═══════════════════════════════════════════════════

# ── Tool 1 ──────────────────────────────────────────

@function_tool
def search_knowledge_base(query: str, max_results: int = 3, category: Optional[str] = None) -> str:
    """
    Search the TechCorp knowledge base for information relevant to the customer's issue.
    Always call this BEFORE attempting to answer any product or technical question.
    If this returns no results twice in a row, escalate to human with reason='technical_tier2'.

    Args:
        query: Customer's question or issue in natural language.
        max_results: Max results to return (default 3).
        category: Optional filter: authentication | integrations | billing | plans |
                  features | security | developers | onboarding | known_issues | account.

    Returns:
        JSON with found=True/False and list of relevant knowledge base entries.
    """
    query_lower = query.lower()
    query_words = set(re.findall(r'\w+', query_lower))

    scored = []
    for entry in KNOWLEDGE_BASE:
        if category and entry.get("category") != category:
            continue
        text       = (entry["title"] + " " + entry["content"]).lower()
        text_words = set(re.findall(r'\w+', text))
        overlap    = len(query_words & text_words)
        title_hit  = len(query_words & set(re.findall(r'\w+', entry["title"].lower()))) * 2
        score = overlap + title_hit
        if score > 2:
            scored.append((score, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    results = [e for _, e in scored[:max_results]]

    if not results:
        return json.dumps({"found": False, "results": [],
                           "message": "No matching entries. If this is the second failed search, escalate."})

    return json.dumps({"found": True, "results": [
        {"id": r["id"], "category": r["category"], "title": r["title"], "content": r["content"]}
        for r in results
    ]})


# ── Tool 2 ──────────────────────────────────────────

@function_tool
def create_ticket(
    customer_id: str,
    issue: str,
    priority: str = "medium",
    channel: str = "web_form",
    category: Optional[str] = None,
) -> str:
    """
    Create a support ticket. ALWAYS the FIRST tool called — no exceptions.
    Every customer message, even a simple question, must generate a ticket.

    Args:
        customer_id: Customer's email or phone number.
        issue: 1-2 sentence description of the issue.
        priority: 'low' | 'medium' | 'high' | 'critical'.
        channel: 'email' | 'whatsapp' | 'web_form'.
        category: 'authentication' | 'billing' | 'technical' | 'general' |
                  'integration' | 'legal' | 'feature_request'.

    Returns:
        JSON with ticket_id and metadata.
    """
    canonical_id = resolve_identity(customer_id)
    ticket_id    = f"TKT-{uuid.uuid4().hex[:8].upper()}"
    now          = datetime.utcnow().isoformat()

    TICKETS[ticket_id] = {
        "ticket_id":   ticket_id,
        "customer_id": canonical_id,
        "raw_id":      customer_id,
        "issue":       issue,
        "priority":    priority,
        "channel":     channel,
        "category":    category or "general",
        "status":      "open",
        "created_at":  now,
        "updated_at":  now,
        "escalated":   False,
        "resolution":  None,
    }

    print(f"\n  🎫 [TICKET] {ticket_id} | {channel} | priority={priority} | {canonical_id}")
    return json.dumps({
        "ticket_id": ticket_id, "status": "open",
        "created_at": now, "customer_id": canonical_id,
        "channel": channel, "priority": priority,
    })


# ── Tool 3 ──────────────────────────────────────────

@function_tool
def get_customer_history(customer_id: str) -> str:
    """
    Retrieve the full cross-channel history for a customer including:
    prior tickets, conversation memory, sentiment trend, and topics discussed.
    Call this after create_ticket to personalise the response and avoid
    asking customers to repeat themselves.

    Args:
        customer_id: Customer email or phone. Aliases are resolved automatically.

    Returns:
        JSON with customer profile, all prior tickets, sentiment analysis,
        topics discussed, and last 5 conversation turns.
    """
    canonical_id  = resolve_identity(customer_id)
    state         = CUSTOMER_STATES.get(canonical_id)
    prior_tickets = [t for t in TICKETS.values() if t["customer_id"] == canonical_id]

    if not state and not prior_tickets:
        return json.dumps({
            "customer_id":     canonical_id,
            "is_new_customer": True,
            "message":         "First contact — no prior history.",
            "tickets": [], "history": [], "channels_used": [], "topics_discussed": [],
        })

    return json.dumps({
        "customer_id":           canonical_id,
        "is_new_customer":       False,
        "aliases":               state.get("aliases", []) if state else [],
        "channels_used":         state.get("channels_used", []) if state else [],
        "original_channel":      state.get("original_channel") if state else None,
        "channel_switches":      len(state.get("channel_switches", [])) if state else 0,

        # Sentiment
        "sentiment_score":       round(state.get("sentiment_score", 0.65), 3) if state else None,
        "sentiment_trend":       state.get("sentiment_trend", "unknown") if state else None,
        "sentiment_history":     state.get("sentiment_history", [])[-5:] if state else [],
        "anger_spike_detected":  detect_anger_spike(state.get("sentiment_history", [])) if state else False,

        # Topics
        "topics_discussed":      state.get("topics_discussed", []) if state else [],
        "topic_frequency":       state.get("topic_frequency", {}) if state else {},

        # Session
        "session_count":         state.get("session_count", 1) if state else 1,
        "escalation_count":      state.get("escalation_count", 0) if state else 0,
        "resolution_status":     state.get("resolution_status", "pending") if state else "pending",

        # Memory window (last 5 turns)
        "recent_conversation":   state.get("conversation_history", [])[-5:] if state else [],

        # Prior tickets
        "tickets": [
            {
                "ticket_id": t["ticket_id"], "issue": t["issue"],
                "channel": t["channel"], "status": t["status"],
                "priority": t["priority"], "created_at": t["created_at"],
                "escalated": t["escalated"],
            }
            for t in prior_tickets
        ],
    })


# ── Tool 4 ──────────────────────────────────────────

@function_tool
def escalate_to_human(ticket_id: str, reason: str, urgency: str = "normal") -> str:
    """
    Escalate this ticket to a human agent. Required in these situations:
    - Pricing, discount, or refund questions → reason='pricing_inquiry' or 'refund_request'
    - Legal keywords: lawyer, sue, attorney, court → reason='legal_escalation'
    - Very angry customer or sentiment < 0.3 → reason='angry_customer'
    - WhatsApp: customer says 'human', 'agent', 'representative' → reason='human_requested'
    - Data loss or corruption → reason='technical_tier2', urgency='critical'
    - 2 failed knowledge searches → reason='technical_tier2'
    - Sudden anger spike (was neutral, now furious) → reason='anger_spike'
    - Enterprise account unresolved → reason='enterprise_account'
    Never say 'I don't know' — escalate instead.

    Args:
        ticket_id: From create_ticket.
        reason: Routing tag (see list above).
        urgency: 'low' | 'normal' | 'high' | 'critical'.

    Returns:
        JSON with escalation_id, routing destination, SLA, and suggested message to customer.
    """
    escalation_id = f"ESC-{uuid.uuid4().hex[:6].upper()}"
    routing       = ESCALATION_ROUTING.get(reason, "csm@techcorp.io")
    priority_map  = {"critical": "critical", "high": "high", "normal": "medium", "low": "low"}
    sla           = ESCALATION_SLA.get(priority_map.get(urgency, "medium"), "4 hours")

    ESCALATIONS[escalation_id] = {
        "escalation_id": escalation_id, "ticket_id": ticket_id,
        "reason": reason, "routing": routing,
        "urgency": urgency, "sla": sla,
        "created_at": datetime.utcnow().isoformat(),
    }

    if ticket_id in TICKETS:
        TICKETS[ticket_id]["escalated"] = True
        TICKETS[ticket_id]["status"]    = "escalated"
        cid = TICKETS[ticket_id]["customer_id"]
        if cid in CUSTOMER_STATES:
            CUSTOMER_STATES[cid]["escalation_count"] = \
                CUSTOMER_STATES[cid].get("escalation_count", 0) + 1

    print(f"\n  🚨 [ESCALATE] {escalation_id} | reason={reason} | → {routing} | SLA={sla}")

    return json.dumps({
        "escalation_id": escalation_id, "ticket_id": ticket_id,
        "routed_to": routing, "reason": reason, "sla": sla,
        "message_to_use": (
            f"I've connected you with our specialist team who can best assist with this. "
            f"They'll reach out within {sla}. Your reference number is {ticket_id}. "
            "Is there anything else I can help with in the meantime?"
        ),
    })


# ── Tool 5 ──────────────────────────────────────────

@function_tool
def send_response(ticket_id: str, message: str, channel: str) -> str:
    """
    Send the final formatted response to the customer. ALWAYS the LAST tool called.
    Never reply to the customer directly without using this tool.
    Channel formatting (greeting, signature, length trimming, emoji) is applied automatically.

    Args:
        ticket_id: From create_ticket.
        message: Response content in neutral tone. Do NOT add greeting/signature yourself —
                 they are added automatically per channel rules.
        channel: 'email' | 'whatsapp' | 'web_form'.

    Returns:
        JSON with delivery_status and the fully formatted message that was sent.
    """
    try:
        ch = Channel(channel)
    except ValueError:
        ch = Channel.WEB_FORM

    customer_name = None
    if ticket_id in TICKETS:
        cid = TICKETS[ticket_id]["customer_id"]
        if "@" in cid:
            customer_name = cid.split("@")[0].replace(".", " ").title()
        TICKETS[ticket_id]["status"]     = "resolved"
        TICKETS[ticket_id]["resolution"] = message
        TICKETS[ticket_id]["updated_at"] = datetime.utcnow().isoformat()

        # Update resolution status in state
        if cid in CUSTOMER_STATES:
            CUSTOMER_STATES[cid]["resolution_status"] = "resolved"
            CUSTOMER_STATES[cid]["successful_resolutions"] = \
                CUSTOMER_STATES[cid].get("successful_resolutions", 0) + 1

    formatted = format_response(message, ch, customer_name)

    RESPONSES.setdefault(ticket_id, []).append({
        "channel": channel, "message": formatted,
        "sent_at": datetime.utcnow().isoformat(),
        "char_count": len(formatted),
        "word_count": len(formatted.split()),
    })

    print(f"\n  📤 [SENT] ticket={ticket_id} | channel={channel} | {len(formatted)} chars")
    return json.dumps({
        "delivery_status": "delivered", "ticket_id": ticket_id,
        "channel": channel, "formatted_message": formatted,
        "char_count": len(formatted),
    })


# ═══════════════════════════════════════════════════
# SYSTEM PROMPT
# ═══════════════════════════════════════════════════

CUSTOMER_SUCCESS_SYSTEM_PROMPT = """
You are Alex, the Customer Success AI for TechCorp — a B2B SaaS project management platform.
You provide 24/7 support across Email, WhatsApp, and Web Form.

## Channel Awareness

**EMAIL:** Professional, warm, thorough.
- Always start: "Dear [Name]," or "Hello [Name],"
- 200–500 words. Numbered lists for steps. Complete answers.
- End with: Best regards, TechCorp Support Team
- No emoji, no slang.

**WHATSAPP:** Conversational, friendly, BRIEF.
- Target 160 chars. Hard max: 300 chars. Break into short sentences.
- 1-2 emoji max. No numbered lists.
- If customer types "human", "agent", or "representative" → escalate immediately.

**WEB FORM:** Semi-formal, direct.
- No greeting needed. 100–300 words. Can use headers/bullets.
- Always end with reference to techcorp.io/support.

## Required Workflow (Order is Mandatory)

1. create_ticket() — FIRST, always.
2. get_customer_history() — check prior interactions, don't make them repeat themselves.
3. search_knowledge_base() — find the answer before responding.
4. escalate_to_human() — if any trigger applies.
5. send_response() — LAST, always. Never reply directly.

## Hard Constraints

- NEVER quote prices, discounts, or costs → escalate: pricing_inquiry
- NEVER process refunds → escalate: refund_request
- NEVER discuss legal matters → escalate: legal_escalation
- NEVER say "I don't know" → escalate instead
- NEVER mention competitor names (Asana, Notion, Monday, ClickUp, Jira, Trello)
- NEVER promise feature timelines or roadmap dates
- NEVER reveal internal routing, team names, or processes
- create_ticket FIRST. send_response LAST. Always.

## Escalation Triggers

- Pricing / discount / refund / invoice → pricing_inquiry or refund_request
- lawyer / sue / attorney / court / legal / litigation → legal_escalation
- GDPR deletion / data breach / security vulnerability → legal_escalation
- Very angry, threatening, profanity → angry_customer
- WhatsApp: human / agent / representative → human_requested
- Data loss or corruption → technical_tier2, urgency=critical
- 2 failed knowledge searches → technical_tier2
- Anger spike (sentiment was OK, now very low) → anger_spike
- Enterprise/Business account unresolved → enterprise_account

## Sentiment Awareness

The context block includes SENTIMENT and SENTIMENT_TREND.
- If SENTIMENT < 0.3 or SENTIMENT_TREND = worsening → lead with empathy FIRST.
- Empathy phrases: "I completely understand how frustrating this must be."
  "That's not the experience we want you to have."
  "Let me make sure we get this sorted right away."
- If ANGER_SPIKE_DETECTED = True → escalate proactively.

## Conversation Memory

The context includes CONVERSATION HISTORY. Use this to:
- Avoid repeating information already given
- Recognize returning customers and reference prior issues
- Detect if a prior solution didn't work → escalate
- Personalise tone based on relationship length

## Response Quality

- Use the customer's name always.
- Reference exact settings paths: "Settings → Integrations → Slack"
- Plan-gated features: explain limitation + offer upgrade path, never just "no"
- Known bugs: acknowledge + workaround + fix timeline
"""


# ═══════════════════════════════════════════════════
# AGENT
# ═══════════════════════════════════════════════════

customer_success_agent = Agent(
    name="Customer Success FTE",
    model="gpt-4o",
    instructions=CUSTOMER_SUCCESS_SYSTEM_PROMPT,
    tools=[
        search_knowledge_base,
        create_ticket,
        get_customer_history,
        escalate_to_human,
        send_response,
    ],
)


# ═══════════════════════════════════════════════════
# PROCESS MESSAGE  (1.3 ENHANCED)
# ═══════════════════════════════════════════════════

async def process_message(
    message:       str,
    channel:       Channel,
    customer_id:   str,
    customer_name: Optional[str] = None,
    subject:       Optional[str] = None,
    alias_for:     Optional[str] = None,   # 1.3 NEW: link this ID to a canonical ID
) -> dict:
    """
    Process one customer message end-to-end:
      1. Resolve identity / register alias
      2. Get or create state
      3. Update sentiment + topics
      4. Detect anger spike → auto-add to context
      5. Build enriched context prompt
      6. Run agent
      7. Store response in memory
    """
    # 1. Identity resolution
    if alias_for:
        register_alias(customer_id, alias_for)
    canonical_id = resolve_identity(customer_id)

    # 2. State
    state = get_or_create_state(canonical_id, channel)

    # 3. Sentiment + topics
    raw_sentiment   = update_sentiment(state, message)
    current_topics  = update_topics(state, message)
    anger_spike     = detect_anger_spike(state["sentiment_history"])

    # 4. Add message to memory BEFORE calling agent (agent sees history up to this turn)
    add_to_memory(state, "user", message, channel.value, current_topics)

    # 5. Build context
    context = build_agent_context(
        message=message, channel=channel,
        customer_id=canonical_id, state=state,
        customer_name=customer_name, subject=subject,
        current_topics=current_topics,
    )

    # Append anger spike flag if detected
    if anger_spike:
        context += "\n\n[⚠ ANGER_SPIKE_DETECTED: True — consider proactive escalation]"

    # Console header
    score         = state["sentiment_score"]
    trend         = state["sentiment_trend"]
    session_label = f"Session #{state['session_count']}"
    print(f"\n{'═'*62}")
    print(f"  Channel:  {channel.value.upper()}  |  Customer: {canonical_id}")
    print(f"  {session_label}  |  Sentiment: {score:.2f} ({trend})")
    if current_topics:
        print(f"  Topics:   {', '.join(current_topics[:4])}")
    if subject:
        print(f"  Subject:  {subject}")
    msg_preview = message[:90] + ("..." if len(message) > 90 else "")
    print(f"  Message:  {msg_preview}")
    print(f"{'═'*62}")

    # 6. Run agent
    try:
        result      = await Runner.run(customer_success_agent, context)
        final_output = result.final_output

        # 7. Store agent reply in memory
        add_to_memory(state, "assistant", final_output, channel.value)

        # Resolve ticket status
        latest_ticket = None
        for t in reversed(list(TICKETS.values())):
            if t["customer_id"] == canonical_id:
                latest_ticket = t
                break
        if latest_ticket:
            state["resolution_status"] = latest_ticket["status"]

        return {
            "success":      True,
            "output":       final_output,
            "channel":      channel.value,
            "customer_id":  canonical_id,
            "sentiment":    round(score, 3),
            "trend":        trend,
            "topics":       current_topics,
            "anger_spike":  anger_spike,
            "status":       state["resolution_status"],
            "ticket":       latest_ticket,
            "session":      state["session_count"],
        }

    except Exception as e:
        print(f"\n  ✗ ERROR: {e}")
        return {"success": False, "error": str(e), "channel": channel.value, "customer_id": canonical_id}


# ═══════════════════════════════════════════════════
# DEMO SCENARIOS  (1.3: MULTI-TURN + CROSS-CHANNEL)
# ═══════════════════════════════════════════════════

SINGLE_TICKETS = [
    {
        "id": "T001", "channel": Channel.EMAIL,
        "customer_id": "sarah.johnson@acmecorp.com", "name": "Sarah Johnson",
        "subject": "Cannot reset my password",
        "message": (
            "Hi, I've been trying to reset my password for the last hour but I'm not "
            "receiving the reset email. I've checked spam and it's not there. This is urgent "
            "as I have a client meeting in 30 minutes. Please help ASAP!"
        ),
    },
    {
        "id": "T002", "channel": Channel.WHATSAPP,
        "customer_id": "+15125550142", "name": "Marcus",
        "subject": None,
        "message": "Hey how do I add someone to my team? Keep getting an error",
    },
    {
        "id": "T004", "channel": Channel.EMAIL,
        "customer_id": "cfo@retailbrand.com", "name": "Jennifer Walsh",
        "subject": "Billing question - need invoice adjusted",
        "message": (
            "Hello, I noticed our last invoice charged us for 45 users but we only have "
            "38 active users. We'd like a credit for the difference. Also, what would the "
            "cost be if we needed more storage? We're on the Business plan."
        ),
    },
    {
        "id": "T005", "channel": Channel.WHATSAPP,
        "customer_id": "+15125550198", "name": "Raj",
        "subject": None,
        "message": (
            "This app is completely broken!! We lost 2 weeks of project data and my team "
            "is furious. I need to speak to a manager RIGHT NOW. This is unacceptable."
        ),
    },
    {
        "id": "T011", "channel": Channel.WHATSAPP,
        "customer_id": "+15125550334", "name": "Tom",
        "subject": None,
        "message": "PDF export from Gantt is broken again. Getting error 500",
    },
    {
        "id": "T006", "channel": Channel.WEB_FORM,
        "customer_id": "pm@designstudio.co", "name": "Emma Chen",
        "subject": "How to set up Gantt view",
        "message": (
            "I can't figure out how to use the Gantt chart view. I switch to it but my "
            "tasks don't show up with any timeline. We have deadlines set on all tasks. "
            "What am I missing?"
        ),
    },
]

# Cross-channel scenario: same customer contacts via WhatsApp, then follows up by Email
CROSS_CHANNEL_SCENARIO = [
    {
        "turn": 1,
        "label": "First contact — WhatsApp",
        "channel": Channel.WHATSAPP,
        "customer_id": "+15125550999",
        "name": "Diana",
        "alias_for": None,
        "subject": None,
        "message": "notifications not working at all 😤 been like this for 2 days",
    },
    {
        "turn": 2,
        "label": "Follow-up — same WhatsApp conversation",
        "channel": Channel.WHATSAPP,
        "customer_id": "+15125550999",
        "name": "Diana",
        "alias_for": None,
        "subject": None,
        "message": "I already tried disconnecting and reconnecting slack. still broken",
    },
    {
        "turn": 3,
        "label": "Switches to Email — links identity",
        "channel": Channel.EMAIL,
        "customer_id": "diana.park@brandco.com",
        "name": "Diana Park",
        "alias_for": "+15125550999",   # 1.3: link phone → email as same customer
        "subject": "Slack notifications still not working after 3 days",
        "message": (
            "Hello, I contacted you via WhatsApp two days ago about Slack notifications "
            "not working. The suggested fix didn't work. I'm now sending this from my "
            "email as I need this resolved urgently — we use Slack notifications for all "
            "client alerts and our team is missing important updates. Please escalate this."
        ),
    },
]

# Sentiment degradation scenario: customer gets progressively angrier
SENTIMENT_DEGRADATION_SCENARIO = [
    {
        "turn": 1, "label": "Calm start",
        "channel": Channel.WEB_FORM,
        "customer_id": "manager@firm.com", "name": "Alex Morgan",
        "alias_for": None, "subject": "Time tracking export issue",
        "message": "Hi, our Excel export from time tracking reports shows a blank file. CSV works fine.",
    },
    {
        "turn": 2, "label": "Frustrated — fix didn't work",
        "channel": Channel.WEB_FORM,
        "customer_id": "manager@firm.com", "name": "Alex Morgan",
        "alias_for": None, "subject": "Re: Time tracking export issue",
        "message": (
            "I tried what you suggested and it's STILL showing a blank Excel file. "
            "This is affecting our payroll processing. We need this fixed TODAY."
        ),
    },
    {
        "turn": 3, "label": "Anger spike",
        "channel": Channel.WEB_FORM,
        "customer_id": "manager@firm.com", "name": "Alex Morgan",
        "alias_for": None, "subject": "URGENT - payroll deadline at risk",
        "message": (
            "This is absolutely unacceptable. We have a payroll deadline in 2 HOURS "
            "and your system is broken. If we miss payroll because of this I will be "
            "contacting my lawyer about the financial damages. Fix this NOW."
        ),
    },
]


async def run_demo_single(ticket_id: Optional[str] = None):
    """Run single-turn demo tickets."""
    tickets = SINGLE_TICKETS
    if ticket_id:
        tickets = [t for t in SINGLE_TICKETS if t["id"] == ticket_id]
        if not tickets:
            print(f"Ticket {ticket_id} not in demo set.")
            return

    print("\n" + "█"*62)
    print("  SINGLE-TICKET DEMO — Channel-Aware Responses")
    print("█"*62)

    for i, t in enumerate(tickets, 1):
        print(f"\n[{i}/{len(tickets)}] {t['id']} — {t['channel'].value.upper()}")
        result = await process_message(
            message=t["message"], channel=t["channel"],
            customer_id=t["customer_id"], customer_name=t.get("name"),
            subject=t.get("subject"),
        )
        if result["success"]:
            print(f"  ✓ status={result.get('status')} | sentiment={result.get('sentiment'):.2f}")
        else:
            print(f"  ✗ {result.get('error')}")

        if i < len(tickets):
            input("\n  ↵ Enter to continue...")

    _print_summary()


async def run_cross_channel_demo():
    """Demo: same customer, 3 turns, switches channel mid-conversation."""
    print("\n" + "█"*62)
    print("  CROSS-CHANNEL CONTINUITY DEMO")
    print("  Customer: Diana Park — WhatsApp → WhatsApp → Email")
    print("█"*62)

    for step in CROSS_CHANNEL_SCENARIO:
        print(f"\n─── Turn {step['turn']}: {step['label']} ───")
        result = await process_message(
            message=step["message"], channel=step["channel"],
            customer_id=step["customer_id"], customer_name=step.get("name"),
            subject=step.get("subject"), alias_for=step.get("alias_for"),
        )
        if result["success"]:
            print(f"  ✓ sentiment={result.get('sentiment'):.2f} | trend={result.get('trend')}")
        input("\n  ↵ Enter for next turn...")

    _print_summary()


async def run_sentiment_degradation_demo():
    """Demo: sentiment worsens → agent escalates proactively."""
    print("\n" + "█"*62)
    print("  SENTIMENT DEGRADATION DEMO")
    print("  Customer: Alex Morgan — calm → frustrated → legal threat")
    print("█"*62)

    for step in SENTIMENT_DEGRADATION_SCENARIO:
        print(f"\n─── Turn {step['turn']}: {step['label']} ───")
        result = await process_message(
            message=step["message"], channel=step["channel"],
            customer_id=step["customer_id"], customer_name=step.get("name"),
            subject=step.get("subject"), alias_for=step.get("alias_for"),
        )
        if result["success"]:
            spike  = "⚡ ANGER SPIKE" if result.get("anger_spike") else ""
            print(f"  ✓ sentiment={result.get('sentiment'):.2f} | trend={result.get('trend')} {spike}")
        input("\n  ↵ Enter for next turn...")

    _print_summary()


def _print_summary():
    print("\n\n" + "═"*62)
    print("  SESSION SUMMARY")
    print("═"*62)
    total     = len(TICKETS)
    resolved  = sum(1 for t in TICKETS.values() if t["status"] == "resolved")
    escalated = sum(1 for t in TICKETS.values() if t["escalated"])
    print(f"  Tickets:     {total}")
    print(f"  Resolved:    {resolved}")
    print(f"  Escalated:   {escalated}")
    if total:
        print(f"  Esc. rate:   {escalated/total*100:.0f}%")
    print(f"  Identities:  {len(CUSTOMER_STATES)} unique customers")
    print(f"  Aliases:     {len(CUSTOMER_ALIASES)} linked")
    print(f"  Escalations: {len(ESCALATIONS)}")
    if ESCALATIONS:
        for e in ESCALATIONS.values():
            print(f"    {e['escalation_id']} → {e['routing']} ({e['reason']})")
    print("═"*62)


# ═══════════════════════════════════════════════════
# INTERACTIVE MODE
# ═══════════════════════════════════════════════════

async def run_interactive():
    print("\n" + "█"*62)
    print("  CUSTOMER SUCCESS FTE — INTERACTIVE MODE (1.3)")
    print("  Commands: quit | stats | alias | memory")
    print("█"*62)

    channel_map = {"1": Channel.EMAIL, "2": Channel.WHATSAPP, "3": Channel.WEB_FORM}

    while True:
        print("\n[Channel] 1=Email  2=WhatsApp  3=Web Form  q=quit  stats=stats  memory=memory  alias=link IDs")
        ch_input = input("  Select: ").strip().lower()

        if ch_input in ("q", "quit"):
            _print_summary()
            break

        if ch_input == "stats":
            _print_summary()
            continue

        if ch_input == "memory":
            cid = input("  Customer ID: ").strip()
            canonical = resolve_identity(cid)
            state = CUSTOMER_STATES.get(canonical)
            if not state:
                print("  No state found.")
                continue
            print(f"\n  Customer: {canonical}")
            print(f"  Sentiment: {state['sentiment_score']:.2f} ({state['sentiment_trend']})")
            print(f"  Topics: {state['topics_discussed']}")
            print(f"  Sessions: {state['session_count']}")
            print(f"  History ({len(state['conversation_history'])} turns):")
            for turn in state["conversation_history"][-4:]:
                role  = "You" if turn["role"] == "user" else "Agent"
                preview = turn["content"][:100]
                print(f"    [{role}] {preview}")
            continue

        if ch_input == "alias":
            alias_id = input("  Alias ID (phone/secondary email): ").strip()
            canon_id = input("  Canonical ID (primary email): ").strip()
            register_alias(alias_id, canon_id)
            continue

        channel = channel_map.get(ch_input, Channel.WEB_FORM)
        customer_id   = input("  Customer ID: ").strip() or f"anon-{uuid.uuid4().hex[:4]}"
        customer_name = input("  Name (optional): ").strip() or None
        subject       = input("  Subject (email only): ").strip() if channel == Channel.EMAIL else None
        message       = input("  Message: ").strip()
        if not message:
            continue

        result = await process_message(
            message=message, channel=channel,
            customer_id=customer_id, customer_name=customer_name,
            subject=subject,
        )
        if not result["success"]:
            print(f"\n  ERROR: {result.get('error')}")


# ═══════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Customer Success FTE Prototype v1.3")
    parser.add_argument("--demo",     action="store_true",  help="Run single-ticket demo")
    parser.add_argument("--ticket",   type=str,             help="Run specific ticket (e.g. T001)")
    parser.add_argument("--scenario", type=str,             help="cross | sentiment",
                        choices=["cross", "sentiment"])
    args = parser.parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        print("\n⚠  OPENAI_API_KEY not set. Set with: export OPENAI_API_KEY=sk-...")

    if args.scenario == "cross":
        asyncio.run(run_cross_channel_demo())
    elif args.scenario == "sentiment":
        asyncio.run(run_sentiment_degradation_demo())
    elif args.demo or args.ticket:
        asyncio.run(run_demo_single(ticket_id=args.ticket))
    else:
        asyncio.run(run_interactive())


if __name__ == "__main__":
    main()
