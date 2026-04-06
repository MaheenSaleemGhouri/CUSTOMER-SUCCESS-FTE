"""
mcp_server.py — Exercise 1.4 | Customer Success Digital FTE
Stage 1: Incubation | MCP Server with 5 Channel-Aware Tools

This MCP server exposes all 5 customer support tools to Claude Desktop /
Claude Code for live incubation exploration. Run as a stdio subprocess.

Setup (Claude Desktop config):
    {
      "mcpServers": {
        "customer-success-fte": {
          "command": "python",
          "args": ["C:/Hackathon 5/mcp_server.py"],
          "env": {}
        }
      }
    }

Run standalone for testing:
    python mcp_server.py

Tools exposed:
    1. search_knowledge_base  — query TechCorp knowledge base
    2. create_ticket          — open a support ticket (always call first)
    3. get_customer_history   — fetch cross-channel customer profile
    4. escalate_to_human      — route to specialist team
    5. send_response          — deliver formatted reply via channel
"""

import asyncio
import json
import uuid
import re
import os
import sys
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Optional

import mcp.server.stdio
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions

# ─────────────────────────────────────────────────────────────
# LOGGING  (stderr so it doesn't pollute MCP stdio stream)
# ─────────────────────────────────────────────────────────────

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="[mcp_server] %(levelname)s %(message)s",
)
logger = logging.getLogger("customer-success-fte")


# ─────────────────────────────────────────────────────────────
# ENUMS & CONSTANTS
# ─────────────────────────────────────────────────────────────

class Channel(str, Enum):
    EMAIL    = "email"
    WHATSAPP = "whatsapp"
    WEB_FORM = "web_form"


ESCALATION_ROUTING = {
    "pricing_inquiry":             "sales@techcorp.io",
    "refund_request":              "billing@techcorp.io",
    "legal_escalation":            "legal@techcorp.io",
    "angry_customer":              "csm@techcorp.io",
    "technical_tier2":             "bugs@techcorp.io",
    "enterprise_account":          "csm@techcorp.io",
    "business_account_unresolved": "csm@techcorp.io",
    "human_requested":             "csm@techcorp.io",
    "anger_spike":                 "csm@techcorp.io",
    "partnership_request":         "partnerships@techcorp.io",
}

ESCALATION_SLA = {
    "critical": "15 minutes",
    "high":     "1 hour",
    "medium":   "4 hours",
    "low":      "24 hours",
}

WHATSAPP_MAX_CHARS = 300
SESSION_TIMEOUT_MINS = 30

LEGAL_KEYWORDS  = {"lawyer", "legal", "sue", "attorney", "court", "litigation"}
HUMAN_KEYWORDS  = {"human", "agent", "representative", "real person", "talk to someone"}

TOPIC_KEYWORDS: dict[str, list[str]] = {
    "password_reset":     ["password", "reset", "forgot", "login", "locked out"],
    "two_factor_auth":    ["2fa", "two factor", "authenticator", "totp", "backup code"],
    "billing":            ["billing", "invoice", "charge", "payment", "subscription"],
    "refund":             ["refund", "money back", "credit", "chargeback"],
    "integration_slack":  ["slack", "slack integration", "slack notification"],
    "integration_github": ["github", "pull request", "pr", "branch"],
    "integration_zapier": ["zapier", "zap", "webhook", "automation"],
    "integration_teams":  ["teams", "microsoft teams", "ms teams"],
    "integration_sso":    ["sso", "saml", "single sign-on", "azure ad"],
    "team_management":    ["invite", "team member", "permission", "role", "admin", "member"],
    "storage":            ["storage", "file", "upload", "5gb", "50gb"],
    "gantt_pdf_bug":      ["gantt", "pdf", "export", "error 500"],
    "notifications":      ["notification", "alert"],
    "calendar_view":      ["calendar", "calendar view", "task disappear", "missing task"],
    "data_loss":          ["lost data", "deleted", "missing data", "disappeared", "recover"],
    "legal_compliance":   ["gdpr", "ferpa", "legal", "lawyer", "compliance", "data deletion"],
    "security":           ["security", "vulnerability", "breach", "soc 2"],
    "api":                ["api", "rate limit", "token", "endpoint"],
    "onboarding":         ["getting started", "new user", "tutorial", "guide"],
    "mobile_app":         ["mobile", "ios", "android", "app"],
    "feature_request":    ["feature request", "would love", "please add", "roadmap"],
    "plan_upgrade":       ["upgrade", "growth plan", "business plan", "enterprise"],
    "cancellation":       ["cancel", "cancellation", "switching to"],
    "export":             ["export", "download", "csv", "excel"],
}


# ─────────────────────────────────────────────────────────────
# IN-MEMORY STORES
# ─────────────────────────────────────────────────────────────

CUSTOMER_STATES: dict[str, dict] = {}
TICKETS:         dict[str, dict] = {}
ESCALATIONS:     dict[str, dict] = {}
RESPONSES:       dict[str, list] = {}
CUSTOMER_ALIASES: dict[str, str] = {}   # alias → canonical


# ─────────────────────────────────────────────────────────────
# KNOWLEDGE BASE
# ─────────────────────────────────────────────────────────────

KNOWLEDGE_BASE: list[dict] = [
    {
        "id": "kb_password_reset", "category": "authentication",
        "title": "Password Reset",
        "content": (
            "To reset your password: 1) Go to app.techcorp.io/login 2) Click 'Forgot Password' "
            "3) Enter your email and check inbox 4) Link expires in 24 hours. "
            "No email? Check spam, whitelist noreply@techcorp.io. Contact support if missing after 5 min."
        ),
    },
    {
        "id": "kb_2fa", "category": "authentication",
        "title": "Two-Factor Authentication",
        "content": (
            "Enable 2FA: Settings → Security → Two-Factor Authentication. "
            "Supports TOTP (Google Authenticator, Authy) and SMS. Save backup codes immediately. "
            "Locked out: use backup codes or contact support for manual verification."
        ),
    },
    {
        "id": "kb_team_member", "category": "team_management",
        "title": "Adding Team Members",
        "content": (
            "Add member: Settings → Team → Invite Member → enter email + role. Expires 7 days. "
            "Resend: Settings → Team → Pending Invites. "
            "Roles: Owner (full), Admin (team/settings), Member (projects), Guest (limited). "
            "Can't see projects? Check project-level permissions in project Settings tab."
        ),
    },
    {
        "id": "kb_slack", "category": "integrations",
        "title": "Slack Integration",
        "content": (
            "Slack requires Growth plan or above (not available on Starter). "
            "Setup: Settings → Integrations → Slack → Connect → OAuth → select channel → configure events. "
            "Known: 2-5 min delay at peak hours. Not working? Disconnect and reconnect. "
            "Starter customers must upgrade to access Slack."
        ),
    },
    {
        "id": "kb_gantt_pdf", "category": "known_issues",
        "title": "Gantt PDF Export Bug",
        "content": (
            "KNOWN ISSUE: Gantt PDF export fails intermittently with error 500. "
            "Fix is in v3.2.1, releasing next Tuesday. "
            "Workaround: export to CSV — same data, opens in Excel/Sheets."
        ),
    },
    {
        "id": "kb_plans", "category": "plans",
        "title": "Plan Features and Limits",
        "content": (
            "Starter ($29/mo): 5 users, 5GB, no integrations, email support. "
            "Growth ($79/mo): 20 users, 50GB, all integrations, WhatsApp support. "
            "Business ($199/mo): 100 users, SSO, audit logs, API 1K req/hr. "
            "Enterprise (custom): unlimited, on-prem option, API 10K req/hr, custom SLA. "
            "Upgrades: immediate. Downgrades: next billing cycle."
        ),
    },
    {
        "id": "kb_storage", "category": "plans",
        "title": "Storage Limits",
        "content": (
            "At storage limit: new uploads blocked, existing files unaffected. "
            "Free space: Settings → Storage → delete unused files. "
            "Upgrade for more: Growth=50GB, Business=100GB, Enterprise=unlimited. "
            "Billing: app.techcorp.io/settings/billing."
        ),
    },
    {
        "id": "kb_billing", "category": "billing",
        "title": "Billing and Subscriptions",
        "content": (
            "Billing portal: app.techcorp.io/settings/billing — view invoices, update payment, change plan. "
            "Refunds and invoice adjustments: NOT handled by AI — billing team at billing@techcorp.io. "
            "Upgrades: immediate. Cancellations: active until period end, then account frozen."
        ),
    },
    {
        "id": "kb_data_retention", "category": "account",
        "title": "Data Retention on Cancellation",
        "content": (
            "After cancel: active until period end → archived 30 days → permanently deleted. "
            "Export first: Settings → Export → Download Everything (CSV/JSON). "
            "Resubscribe within 30 days: full restore. After 30 days: unrecoverable."
        ),
    },
    {
        "id": "kb_notifications", "category": "features",
        "title": "Notification Settings",
        "content": (
            "Manage: Settings → Notifications. Types: in-app, email, Slack, MS Teams. "
            "Not working? Check: notifications enabled in Settings, browser permissions (in-app), "
            "and Settings → Integrations for Slack/Teams connectivity."
        ),
    },
    {
        "id": "kb_export", "category": "features",
        "title": "Export Options",
        "content": (
            "CSV: Reports → select → Export → CSV. Excel: same → Excel (Gantt PDF has bug). "
            "Full account export: Settings → Export → Download Everything."
        ),
    },
    {
        "id": "kb_github", "category": "integrations",
        "title": "GitHub PR Linking",
        "content": (
            "GitHub (Growth+): Settings → Integrations → GitHub. "
            "Link PR to task: include [TASK-ID] or #TASK-ID in PR title/description. "
            "Example: 'Fix login bug [TASK-123]'. Appears in task Activity within 2-3 min. "
            "Ensure correct repository selected in integration settings."
        ),
    },
    {
        "id": "kb_sso", "category": "security",
        "title": "SSO SAML Setup (Business+)",
        "content": (
            "SSO: Business plan+. Settings → Security → Single Sign-On. "
            "ACS URL: https://app.techcorp.io/auth/saml/callback (no trailing slash). "
            "Entity ID: https://app.techcorp.io/saml/metadata. "
            "Azure AD 'Reply URL' = ACS URL. Common error: mismatched URL or trailing slash."
        ),
    },
    {
        "id": "kb_api", "category": "developers",
        "title": "API and Webhooks",
        "content": (
            "API: Business+. Docs: docs.techcorp.io/api. Token: Settings → API → New Token. "
            "Rate limits: Business=1,000/hr, Enterprise=10,000/hr. "
            "Webhooks: Settings → Webhooks → Add Endpoint. "
            "Signature: HMAC-SHA256. Use raw request body bytes — do not decode before hashing."
        ),
    },
    {
        "id": "kb_calendar", "category": "features",
        "title": "Calendar View — Tasks Not Showing",
        "content": (
            "Tasks need a due date to appear in Calendar view. "
            "Check filters (top-right) — they may hide tasks. "
            "Verify date range navigation. Ensure project is not archived."
        ),
    },
    {
        "id": "kb_onboarding", "category": "onboarding",
        "title": "Getting Started",
        "content": (
            "Quick start: 1) New Project → + New Project. 2) + Add Task → set due dates + assign. "
            "3) Settings → Team → Invite Member. 4) Views: List, Kanban, Calendar, Gantt. "
            "Tutorials: techcorp.io/support."
        ),
    },
    {
        "id": "kb_security", "category": "security",
        "title": "Security and Compliance",
        "content": (
            "Certifications: SOC 2 Type II, GDPR, CCPA. AES-256 at rest, TLS 1.3 in transit. "
            "SOC 2 reports / DPA / pen-test results: sales@techcorp.io (NDA required). "
            "GDPR data deletion requests: legal@techcorp.io."
        ),
    },
    {
        "id": "kb_mobile", "category": "features",
        "title": "Mobile App",
        "content": (
            "iOS/Android app is in beta. Join: techcorp.io/beta. "
            "No confirmed general release date — users notified at launch."
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
        "id": "kb_zapier", "category": "integrations",
        "title": "Zapier Integration",
        "content": (
            "Zapier (Growth+): Settings → Integrations → Zapier. "
            "Webhook fires on manual test but not live events? "
            "Check event filter settings, verify endpoint URL, reconnect and re-select trigger events."
        ),
    },
    {
        "id": "kb_ms_teams", "category": "integrations",
        "title": "Microsoft Teams Integration",
        "content": (
            "MS Teams (Growth+): Settings → Integrations → Microsoft 365 → Teams. "
            "Not posting? Verify: 'Connected' status, correct channel selected, events checked. "
            "Try disconnect → reconnect."
        ),
    },
    {
        "id": "kb_workspaces", "category": "account",
        "title": "Multiple Workspaces",
        "content": (
            "One workspace per account. For isolation: use separate projects + roles/permissions, "
            "or create separate accounts. Multi-workspace support: roadmap, no date."
        ),
    },
    {
        "id": "kb_templates", "category": "features",
        "title": "Project Templates",
        "content": (
            "40+ built-in templates: New Project → Browse Templates. "
            "Workaround for custom templates: build ideal project → use 'Duplicate Project'. "
            "Custom template creation from scratch: roadmap, no release date."
        ),
    },
]


# ─────────────────────────────────────────────────────────────
# HELPER FUNCTIONS
# ─────────────────────────────────────────────────────────────

def resolve_identity(customer_id: str) -> str:
    return CUSTOMER_ALIASES.get(customer_id.lower().strip(), customer_id.lower().strip())


def _extract_topics(message: str) -> list[str]:
    text = message.lower()
    return [topic for topic, kws in TOPIC_KEYWORDS.items() if any(kw in text for kw in kws)]


def _estimate_sentiment(message: str) -> float:
    text   = message.lower()
    angry  = {"broken", "terrible", "unacceptable", "furious", "useless", "scam", "awful"}
    happy  = {"thank", "thanks", "great", "love", "amazing", "helpful", "appreciate"}
    if any(kw in text for kw in LEGAL_KEYWORDS):
        return 0.1
    caps = len(re.findall(r'\b[A-Z]{3,}\b', message))
    if caps >= 3:
        return max(0.0, 0.25 - caps * 0.05)
    a = sum(1 for w in angry if w in text)
    h = sum(1 for w in happy if w in text)
    return max(0.0, min(1.0, 0.6 - a * 0.15 + h * 0.1 - message.count("!") * 0.03))


def _get_or_create_state(customer_id: str, channel: str) -> dict:
    canonical = resolve_identity(customer_id)
    if canonical not in CUSTOMER_STATES:
        CUSTOMER_STATES[canonical] = {
            "customer_id":          canonical,
            "original_channel":     channel,
            "channels_used":        [channel],
            "channel_switches":     [],
            "conversation_history": [],
            "sentiment_score":      0.65,
            "sentiment_history":    [],
            "sentiment_trend":      "unknown",
            "topics_discussed":     [],
            "topic_frequency":      {},
            "session_id":           f"SES-{uuid.uuid4().hex[:6].upper()}",
            "session_count":        1,
            "session_start":        datetime.utcnow().isoformat(),
            "last_seen":            datetime.utcnow().isoformat(),
            "resolution_status":    "pending",
            "escalation_count":     0,
            "kb_search_count":      0,
        }
    else:
        state = CUSTOMER_STATES[canonical]
        # Session timeout check
        last = datetime.fromisoformat(state["last_seen"])
        if datetime.utcnow() - last > timedelta(minutes=SESSION_TIMEOUT_MINS):
            state["session_id"]    = f"SES-{uuid.uuid4().hex[:6].upper()}"
            state["session_count"] += 1
            state["kb_search_count"] = 0
        # Channel switch tracking
        if channel not in state["channels_used"]:
            state["channel_switches"].append({"from": state["channels_used"][-1], "to": channel})
            state["channels_used"].append(channel)
        state["last_seen"] = datetime.utcnow().isoformat()
    return CUSTOMER_STATES[canonical]


def _update_state_after_message(state: dict, message: str, channel: str) -> dict:
    """Update sentiment, topics, and memory after receiving a new message."""
    # Sentiment
    new_score = _estimate_sentiment(message)
    state["sentiment_score"] = (state["sentiment_score"] * 0.55) + (new_score * 0.45)
    hist = state.setdefault("sentiment_history", [])
    hist.append(round(new_score, 3))
    if len(hist) > 20:
        hist.pop(0)
    # Trend
    if len(hist) >= 2:
        recent = sum(hist[-3:]) / len(hist[-3:])
        older  = sum(hist[:-3]) / len(hist[:-3]) if len(hist) > 3 else hist[0]
        delta  = recent - older
        state["sentiment_trend"] = "improving" if delta > 0.1 else ("worsening" if delta < -0.1 else "stable")
    # Topics
    new_topics = _extract_topics(message)
    freq = state.setdefault("topic_frequency", {})
    for t in new_topics:
        freq[t] = freq.get(t, 0) + 1
        if t not in state["topics_discussed"]:
            state["topics_discussed"].append(t)
    # Memory
    history = state.setdefault("conversation_history", [])
    history.append({
        "role": "user", "content": message, "channel": channel,
        "topics": new_topics, "ts": datetime.utcnow().isoformat(),
    })
    if len(history) > 10:
        state["conversation_history"] = history[-10:]
    return state


def _format_response(response: str, channel: str, customer_name: Optional[str] = None) -> str:
    """Apply channel-specific formatting rules."""
    name = customer_name or "Customer"

    if channel == Channel.EMAIL.value:
        if not response.strip().startswith(("Dear", "Hello")):
            response = f"Dear {name},\n\n{response}"
        if "Best regards" not in response:
            response += (
                "\n\nBest regards,\n"
                "TechCorp Support Team\n"
                "support@techcorp.io | techcorp.io/support"
            )

    elif channel == Channel.WHATSAPP.value:
        if len(response) > WHATSAPP_MAX_CHARS:
            trimmed   = response[:WHATSAPP_MAX_CHARS]
            last_break = max(trimmed.rfind(". "), trimmed.rfind("! "), trimmed.rfind("? "))
            if last_break > int(WHATSAPP_MAX_CHARS * 0.6):
                response = trimmed[:last_break + 1]
            else:
                response = trimmed.rstrip() + "..."
        if "Type 'human' for live support" not in response:
            response += "\n\n📱 Type 'human' for live support."

    elif channel == Channel.WEB_FORM.value:
        if "techcorp.io/support" not in response:
            response += (
                "\n\n---\nNeed more help? Visit our support portal at techcorp.io/support "
                "or reply to this message."
            )

    return response


def _search_kb(query: str, max_results: int = 3, category: Optional[str] = None) -> list[dict]:
    """Core KB search — returns scored results."""
    query_words = set(re.findall(r'\w+', query.lower()))
    scored = []
    for entry in KNOWLEDGE_BASE:
        if category and entry.get("category") != category:
            continue
        text      = (entry["title"] + " " + entry["content"]).lower()
        text_words = set(re.findall(r'\w+', text))
        overlap    = len(query_words & text_words)
        title_hit  = len(query_words & set(re.findall(r'\w+', entry["title"].lower()))) * 2
        score = overlap + title_hit
        if score > 2:
            scored.append((score, entry))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in scored[:max_results]]


# ─────────────────────────────────────────────────────────────
# MCP SERVER
# ─────────────────────────────────────────────────────────────

server = Server("customer-success-fte")


# ── List Tools ────────────────────────────────────────────────

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """Declare the 5 MCP tools with full JSON schemas."""
    return [

        types.Tool(
            name="search_knowledge_base",
            description=(
                "Search TechCorp's knowledge base for product documentation, how-to guides, "
                "integration setup instructions, known issues, and plan feature details. "
                "Always call this BEFORE attempting to answer any technical or product question. "
                "If this returns no results twice in a row, escalate to human immediately."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Customer question or issue description in natural language.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum results to return (default 3, max 5).",
                        "default": 3,
                        "minimum": 1,
                        "maximum": 5,
                    },
                    "category": {
                        "type": "string",
                        "description": "Optional category filter.",
                        "enum": [
                            "authentication", "integrations", "billing", "plans",
                            "features", "security", "developers", "onboarding",
                            "known_issues", "account", "team_management",
                        ],
                    },
                },
                "required": ["query"],
            },
        ),

        types.Tool(
            name="create_ticket",
            description=(
                "Open a support ticket for the customer interaction. "
                "ALWAYS call this FIRST before any other tool. Every customer message must create a ticket — "
                "even simple questions. This is mandatory and non-negotiable."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "Customer's unique identifier — email address or phone number.",
                    },
                    "issue": {
                        "type": "string",
                        "description": "Brief 1-2 sentence description of the customer's issue.",
                    },
                    "priority": {
                        "type": "string",
                        "description": "Ticket priority level.",
                        "enum": ["low", "medium", "high", "critical"],
                        "default": "medium",
                    },
                    "channel": {
                        "type": "string",
                        "description": "Channel this ticket arrived from.",
                        "enum": ["email", "whatsapp", "web_form"],
                        "default": "web_form",
                    },
                    "category": {
                        "type": "string",
                        "description": "Issue category for routing and reporting.",
                        "enum": [
                            "authentication", "billing", "technical", "general",
                            "integration", "legal", "feature_request", "account",
                        ],
                    },
                    "customer_name": {
                        "type": "string",
                        "description": "Customer's display name if known.",
                    },
                    "message": {
                        "type": "string",
                        "description": "The original customer message to store with the ticket.",
                    },
                },
                "required": ["customer_id", "issue", "channel"],
            },
        ),

        types.Tool(
            name="get_customer_history",
            description=(
                "Retrieve the complete cross-channel history for a customer including: "
                "all prior tickets, conversation memory (last 5 turns), sentiment trend, "
                "topics discussed, and channel journey. Call this after create_ticket to personalise "
                "the response and avoid asking customers to repeat themselves. "
                "Cross-channel aliases (email ↔ phone) are resolved automatically."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "Customer email or phone. Aliases are resolved automatically.",
                    },
                    "alias_for": {
                        "type": "string",
                        "description": (
                            "Optional: If this customer_id is an alias for a different primary ID, "
                            "provide the canonical ID here to link them. Example: customer_id='+15125550999' "
                            "alias_for='diana@brand.com' — merges WhatsApp and Email profiles."
                        ),
                    },
                },
                "required": ["customer_id"],
            },
        ),

        types.Tool(
            name="escalate_to_human",
            description=(
                "Escalate this ticket to a human specialist. Use when: "
                "(1) Customer asks about pricing, discounts, or refunds; "
                "(2) Legal keywords present: lawyer, sue, attorney, court, litigation; "
                "(3) GDPR data deletion, data breach, or security vulnerability reported; "
                "(4) Customer is very angry or sentiment score is below 0.3; "
                "(5) WhatsApp: customer types 'human', 'agent', or 'representative'; "
                "(6) Data loss or corruption is reported; "
                "(7) After 2 failed knowledge base searches; "
                "(8) Enterprise or Business account issue is unresolved. "
                "Never say 'I don't know' — escalate instead."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticket_id": {
                        "type": "string",
                        "description": "Ticket ID returned by create_ticket. Required.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Escalation reason tag used for routing.",
                        "enum": [
                            "pricing_inquiry",
                            "refund_request",
                            "legal_escalation",
                            "angry_customer",
                            "technical_tier2",
                            "enterprise_account",
                            "business_account_unresolved",
                            "human_requested",
                            "anger_spike",
                            "partnership_request",
                        ],
                    },
                    "urgency": {
                        "type": "string",
                        "description": "Urgency level — affects SLA commitment given to customer.",
                        "enum": ["low", "normal", "high", "critical"],
                        "default": "normal",
                    },
                    "context": {
                        "type": "string",
                        "description": (
                            "Optional brief context for the human agent picking this up. "
                            "1-2 sentences summarising the issue and what was already tried."
                        ),
                    },
                },
                "required": ["ticket_id", "reason"],
            },
        ),

        types.Tool(
            name="send_response",
            description=(
                "Send the final formatted response to the customer via their channel. "
                "ALWAYS the LAST tool called — never reply to the customer without using this tool. "
                "Channel formatting is applied automatically: "
                "email gets greeting + signature; whatsapp is trimmed to 300 chars; "
                "web_form gets support portal reference. "
                "Write the message in a neutral tone — do NOT add greeting/signature yourself."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticket_id": {
                        "type": "string",
                        "description": "Ticket ID from create_ticket.",
                    },
                    "message": {
                        "type": "string",
                        "description": (
                            "The response content in neutral tone. "
                            "Channel formatting (greeting, signature, emoji, length) is applied automatically. "
                            "Do NOT add 'Dear Customer' or 'Best regards' — they will be added for you."
                        ),
                    },
                    "channel": {
                        "type": "string",
                        "description": "Delivery channel.",
                        "enum": ["email", "whatsapp", "web_form"],
                    },
                    "customer_id": {
                        "type": "string",
                        "description": "Customer ID for name extraction and state update.",
                    },
                },
                "required": ["ticket_id", "message", "channel"],
            },
        ),

    ]


# ── Call Tool ─────────────────────────────────────────────────

@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict[str, Any]
) -> list[types.TextContent]:
    """Route tool calls to their implementations."""

    # ── 1. search_knowledge_base ─────────────────────────────

    if name == "search_knowledge_base":
        query       = arguments.get("query", "")
        max_results = int(arguments.get("max_results", 3))
        category    = arguments.get("category")

        results = _search_kb(query, max_results, category)

        if not results:
            logger.info("KB search: no results for '%s'", query[:60])
            payload = {
                "found":   False,
                "results": [],
                "message": "No matching knowledge base entries found. If this is the second failed search, escalate to human.",
            }
        else:
            logger.info("KB search: %d results for '%s'", len(results), query[:60])
            payload = {
                "found":   True,
                "results": [
                    {
                        "id":       r["id"],
                        "category": r["category"],
                        "title":    r["title"],
                        "content":  r["content"],
                    }
                    for r in results
                ],
            }

        return [types.TextContent(type="text", text=json.dumps(payload, indent=2))]

    # ── 2. create_ticket ─────────────────────────────────────

    elif name == "create_ticket":
        customer_id   = arguments.get("customer_id", "unknown")
        issue         = arguments.get("issue", "")
        priority      = arguments.get("priority", "medium")
        channel       = arguments.get("channel", "web_form")
        category      = arguments.get("category", "general")
        customer_name = arguments.get("customer_name")
        message_body  = arguments.get("message", "")

        canonical_id = resolve_identity(customer_id)
        ticket_id    = f"TKT-{uuid.uuid4().hex[:8].upper()}"
        now          = datetime.utcnow().isoformat()

        TICKETS[ticket_id] = {
            "ticket_id":     ticket_id,
            "customer_id":   canonical_id,
            "raw_id":        customer_id,
            "customer_name": customer_name,
            "issue":         issue,
            "priority":      priority,
            "channel":       channel,
            "category":      category,
            "status":        "open",
            "created_at":    now,
            "updated_at":    now,
            "escalated":     False,
            "resolution":    None,
            "original_message": message_body,
        }

        # Initialise / update state
        state = _get_or_create_state(canonical_id, channel)
        if message_body:
            _update_state_after_message(state, message_body, channel)

        logger.info("Ticket created: %s | %s | %s | %s", ticket_id, channel, priority, canonical_id)

        return [types.TextContent(type="text", text=json.dumps({
            "ticket_id":   ticket_id,
            "status":      "open",
            "created_at":  now,
            "customer_id": canonical_id,
            "channel":     channel,
            "priority":    priority,
            "category":    category,
        }, indent=2))]

    # ── 3. get_customer_history ──────────────────────────────

    elif name == "get_customer_history":
        customer_id = arguments.get("customer_id", "unknown")
        alias_for   = arguments.get("alias_for")

        # Register alias if provided
        if alias_for:
            alias_lower  = customer_id.lower().strip()
            canon_lower  = alias_for.lower().strip()
            if alias_lower != canon_lower:
                CUSTOMER_ALIASES[alias_lower] = canon_lower
                logger.info("Alias registered: %s → %s", alias_lower, canon_lower)

        canonical_id  = resolve_identity(customer_id)
        state         = CUSTOMER_STATES.get(canonical_id)
        prior_tickets = [t for t in TICKETS.values() if t["customer_id"] == canonical_id]

        if not state and not prior_tickets:
            return [types.TextContent(type="text", text=json.dumps({
                "customer_id":     canonical_id,
                "is_new_customer": True,
                "message":         "First contact — no prior history.",
                "tickets": [], "history": [], "topics_discussed": [],
            }, indent=2))]

        # Anger spike detection
        hist = state.get("sentiment_history", []) if state else []
        anger_spike = len(hist) >= 2 and hist[-1] < 0.25 and hist[-2] >= 0.4

        payload = {
            "customer_id":      canonical_id,
            "is_new_customer":  False,
            "channels_used":    state.get("channels_used", []) if state else [],
            "original_channel": state.get("original_channel") if state else None,
            "channel_switches": len(state.get("channel_switches", [])) if state else 0,

            # Sentiment
            "sentiment_score":      round(state.get("sentiment_score", 0.65), 3) if state else None,
            "sentiment_trend":      state.get("sentiment_trend", "unknown") if state else None,
            "sentiment_history":    hist[-5:],
            "anger_spike_detected": anger_spike,

            # Topics
            "topics_discussed": state.get("topics_discussed", []) if state else [],
            "topic_frequency":  state.get("topic_frequency", {}) if state else {},

            # Session
            "session_count":     state.get("session_count", 1) if state else 1,
            "escalation_count":  state.get("escalation_count", 0) if state else 0,
            "resolution_status": state.get("resolution_status", "pending") if state else "pending",

            # Memory
            "recent_conversation": state.get("conversation_history", [])[-5:] if state else [],

            # All tickets
            "tickets": [
                {
                    "ticket_id": t["ticket_id"],
                    "issue":     t["issue"],
                    "channel":   t["channel"],
                    "status":    t["status"],
                    "priority":  t["priority"],
                    "created_at": t["created_at"],
                    "escalated": t["escalated"],
                }
                for t in prior_tickets
            ],
        }

        logger.info("History fetched for %s — %d tickets, sentiment=%.2f",
                    canonical_id, len(prior_tickets), state.get("sentiment_score", 0.65) if state else 0.65)

        return [types.TextContent(type="text", text=json.dumps(payload, indent=2))]

    # ── 4. escalate_to_human ─────────────────────────────────

    elif name == "escalate_to_human":
        ticket_id = arguments.get("ticket_id", "")
        reason    = arguments.get("reason", "technical_tier2")
        urgency   = arguments.get("urgency", "normal")
        context   = arguments.get("context", "")

        routing  = ESCALATION_ROUTING.get(reason, "csm@techcorp.io")
        prio_map = {"critical": "critical", "high": "high", "normal": "medium", "low": "low"}
        sla      = ESCALATION_SLA.get(prio_map.get(urgency, "medium"), "4 hours")

        escalation_id = f"ESC-{uuid.uuid4().hex[:6].upper()}"
        ESCALATIONS[escalation_id] = {
            "escalation_id": escalation_id,
            "ticket_id":     ticket_id,
            "reason":        reason,
            "routing":       routing,
            "urgency":       urgency,
            "sla":           sla,
            "context":       context,
            "created_at":    datetime.utcnow().isoformat(),
        }

        # Update ticket + customer state
        if ticket_id in TICKETS:
            TICKETS[ticket_id]["escalated"] = True
            TICKETS[ticket_id]["status"]    = "escalated"
            TICKETS[ticket_id]["updated_at"] = datetime.utcnow().isoformat()
            cid = TICKETS[ticket_id]["customer_id"]
            if cid in CUSTOMER_STATES:
                CUSTOMER_STATES[cid]["escalation_count"] = \
                    CUSTOMER_STATES[cid].get("escalation_count", 0) + 1
                CUSTOMER_STATES[cid]["resolution_status"] = "escalated"

        logger.info("Escalation: %s | %s → %s | SLA=%s", escalation_id, reason, routing, sla)

        return [types.TextContent(type="text", text=json.dumps({
            "escalation_id": escalation_id,
            "ticket_id":     ticket_id,
            "routed_to":     routing,
            "reason":        reason,
            "sla":           sla,
            "urgency":       urgency,
            "message_to_use": (
                f"I've connected you with our specialist team who can best assist with this. "
                f"They'll reach out within {sla}. Your reference number is {ticket_id}. "
                "Is there anything else I can help with in the meantime?"
            ),
        }, indent=2))]

    # ── 5. send_response ─────────────────────────────────────

    elif name == "send_response":
        ticket_id   = arguments.get("ticket_id", "")
        message     = arguments.get("message", "")
        channel     = arguments.get("channel", "web_form")
        customer_id = arguments.get("customer_id", "")

        # Resolve customer name
        customer_name = None
        canonical_id  = resolve_identity(customer_id) if customer_id else None

        if ticket_id in TICKETS:
            cid = TICKETS[ticket_id]["customer_id"]
            canonical_id = canonical_id or cid
            stored_name  = TICKETS[ticket_id].get("customer_name")
            if stored_name:
                customer_name = stored_name
            elif "@" in cid:
                customer_name = cid.split("@")[0].replace(".", " ").title()

            TICKETS[ticket_id]["status"]     = "resolved"
            TICKETS[ticket_id]["resolution"] = message
            TICKETS[ticket_id]["updated_at"] = datetime.utcnow().isoformat()

        # Update customer state
        if canonical_id and canonical_id in CUSTOMER_STATES:
            state = CUSTOMER_STATES[canonical_id]
            state["resolution_status"] = "resolved"
            # Store agent reply in memory
            history = state.setdefault("conversation_history", [])
            history.append({
                "role": "assistant", "content": message, "channel": channel,
                "ts": datetime.utcnow().isoformat(),
            })
            if len(history) > 10:
                state["conversation_history"] = history[-10:]

        # Apply channel formatting
        formatted = _format_response(message, channel, customer_name)

        RESPONSES.setdefault(ticket_id, []).append({
            "channel":    channel,
            "message":    formatted,
            "sent_at":    datetime.utcnow().isoformat(),
            "char_count": len(formatted),
            "word_count": len(formatted.split()),
        })

        # Validate channel limits
        warnings = []
        if channel == "whatsapp" and len(formatted) > WHATSAPP_MAX_CHARS:
            warnings.append(f"WhatsApp response is {len(formatted)} chars — trimming applied.")
        if channel == "email" and len(formatted.split()) > 500:
            warnings.append(f"Email response is {len(formatted.split())} words — consider trimming.")

        logger.info("Response sent: ticket=%s | channel=%s | %d chars", ticket_id, channel, len(formatted))

        return [types.TextContent(type="text", text=json.dumps({
            "delivery_status":   "delivered",
            "ticket_id":         ticket_id,
            "channel":           channel,
            "char_count":        len(formatted),
            "word_count":        len(formatted.split()),
            "formatted_message": formatted,
            "warnings":          warnings,
        }, indent=2))]

    else:
        return [types.TextContent(type="text", text=json.dumps({
            "error": f"Unknown tool: {name}",
            "available_tools": [
                "search_knowledge_base", "create_ticket",
                "get_customer_history", "escalate_to_human", "send_response",
            ],
        }, indent=2))]


# ─────────────────────────────────────────────────────────────
# SERVER ENTRY POINT
# ─────────────────────────────────────────────────────────────

async def main():
    logger.info("Customer Success FTE — MCP Server starting (stdio)")
    logger.info("Tools: search_knowledge_base, create_ticket, get_customer_history, escalate_to_human, send_response")
    logger.info("KB entries loaded: %d", len(KNOWLEDGE_BASE))

    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="customer-success-fte",
                server_version="1.4.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
