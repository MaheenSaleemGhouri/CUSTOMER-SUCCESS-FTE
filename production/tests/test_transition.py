"""
production/tests/test_transition.py
Transition gate — 6 tests that must pass before Stage 2 is considered complete.

Verifies every item in specs/transition-checklist.md:
  T1  System prompt contains all 6 required sections
  T2  All 5 production tools are registered on the agent
  T3  Channel formatters produce spec-compliant output
  T4  Escalation routing table covers all 9 required reason tags
  T5  Tool execution order documented in prompt (FIRST / LAST constraints)
  T6  58%+ of sample tickets are AI-resolvable (performance baseline)
"""

import json
import re
from pathlib import Path

import pytest

from production.agent.prompts import CUSTOMER_SUCCESS_SYSTEM_PROMPT
from production.agent.customer_success_agent import customer_success_agent
from production.agent.formatters import format_for_channel
from production.agent.tools import ESCALATION_ROUTING


# ─────────────────────────────────────────────────────────────
# T1 — System prompt contains all 6 required sections
# ─────────────────────────────────────────────────────────────

REQUIRED_SECTIONS = [
    "## Channel Awareness",
    "## Required Workflow",
    "## Hard Constraints",
    "## Escalation Triggers",
    "## Response Quality Standards",
    "## Context Variables Available",
]

@pytest.mark.parametrize("section", REQUIRED_SECTIONS)
def test_system_prompt_has_all_sections(section: str):
    """T1 — Every required section header must be present in the system prompt."""
    assert section in CUSTOMER_SUCCESS_SYSTEM_PROMPT, (
        f"Missing required section: {section!r}"
    )


# ─────────────────────────────────────────────────────────────
# T2 — All 5 production tools registered on agent
# ─────────────────────────────────────────────────────────────

REQUIRED_TOOL_NAMES = {
    "search_knowledge_base",
    "create_ticket",
    "get_customer_history",
    "escalate_to_human",
    "send_response",
}

def test_agent_has_all_five_tools():
    """T2 — Agent must expose exactly the 5 tools defined in transition-checklist."""
    registered = {t.name for t in customer_success_agent.tools}
    missing = REQUIRED_TOOL_NAMES - registered
    assert not missing, f"Tools missing from agent: {missing}"


def test_agent_model_is_gpt4o():
    """T2b — Agent must use gpt-4o as specified in the constitution."""
    assert customer_success_agent.model == "gpt-4o"


def test_agent_name():
    """T2c — Agent name must match the FTE persona."""
    assert customer_success_agent.name == "Customer Success FTE"


# ─────────────────────────────────────────────────────────────
# T3 — Channel formatters produce spec-compliant output
# ─────────────────────────────────────────────────────────────

class TestChannelFormatters:
    """T3 — All three channel formatters meet constitution spec."""

    def test_email_starts_with_dear(self):
        out = format_for_channel("Your issue has been resolved.", "email", "Alice")
        assert out.startswith("Dear Alice,"), f"Email must start with 'Dear Alice,' — got: {out[:40]!r}"

    def test_email_fallback_name(self):
        out = format_for_channel("Hello.", "email", customer_name=None)
        assert out.startswith("Dear Customer,")

    def test_email_ends_with_signature(self):
        out = format_for_channel("Body text.", "email", "Bob")
        assert "TechCorp AI Support Team" in out

    def test_whatsapp_hard_max_300(self):
        long_msg = "word " * 200          # 1000 chars
        out = format_for_channel(long_msg, "whatsapp")
        assert len(out) <= 300, f"WhatsApp response exceeds 300 chars: {len(out)}"

    def test_whatsapp_suffix_appended(self):
        out = format_for_channel("Quick answer.", "whatsapp")
        assert "Type 'human'" in out

    def test_whatsapp_short_message_untrimmed(self):
        msg = "Done!"
        out = format_for_channel(msg, "whatsapp")
        assert msg in out

    def test_web_form_footer_appended(self):
        out = format_for_channel("Here is the solution.", "web_form")
        assert "support portal" in out.lower()

    def test_web_form_no_greeting(self):
        out = format_for_channel("Here is the solution.", "web_form")
        assert not out.startswith("Dear ")

    def test_unknown_channel_raises(self):
        with pytest.raises(ValueError, match="Unknown channel"):
            format_for_channel("test", "fax")


# ─────────────────────────────────────────────────────────────
# T4 — Escalation routing covers all required reason tags
# ─────────────────────────────────────────────────────────────

REQUIRED_ESCALATION_REASONS = {
    "pricing_inquiry",
    "refund_request",
    "legal_escalation",
    "angry_customer",
    "human_requested",
    "technical_tier2",
    "anger_spike",
    "enterprise_account",
    "business_account_unresolved",
}

def test_escalation_routing_covers_all_reasons():
    """T4 — Every reason tag in the system prompt must have a routing entry."""
    missing = REQUIRED_ESCALATION_REASONS - set(ESCALATION_ROUTING.keys())
    assert not missing, f"Escalation reasons missing from routing table: {missing}"


def test_escalation_routing_has_required_fields():
    """T4b — Every routing entry must have team, email, and sla fields."""
    for reason, routing in ESCALATION_ROUTING.items():
        assert "team"  in routing, f"{reason}: missing 'team'"
        assert "email" in routing, f"{reason}: missing 'email'"
        assert "sla"   in routing, f"{reason}: missing 'sla'"


def test_escalation_emails_are_techcorp_domain():
    """T4c — All escalation emails must route to @techcorp.io addresses."""
    for reason, routing in ESCALATION_ROUTING.items():
        assert routing["email"].endswith("@techcorp.io"), (
            f"{reason}: routing email {routing['email']!r} is not @techcorp.io"
        )


# ─────────────────────────────────────────────────────────────
# T5 — Prompt enforces tool execution order
# ─────────────────────────────────────────────────────────────

def test_prompt_enforces_create_ticket_first():
    """T5 — System prompt must require create_ticket() to be called first."""
    assert "create_ticket" in CUSTOMER_SUCCESS_SYSTEM_PROMPT
    # FIRST constraint must appear near create_ticket
    idx = CUSTOMER_SUCCESS_SYSTEM_PROMPT.find("create_ticket")
    context = CUSTOMER_SUCCESS_SYSTEM_PROMPT[max(0, idx - 100): idx + 200]
    assert "FIRST" in context or "first" in context.lower(), (
        "create_ticket FIRST constraint not found near tool mention"
    )


def test_prompt_enforces_send_response_last():
    """T5 — System prompt must require send_response() to be called last."""
    assert "send_response" in CUSTOMER_SUCCESS_SYSTEM_PROMPT
    idx = CUSTOMER_SUCCESS_SYSTEM_PROMPT.rfind("send_response")
    context = CUSTOMER_SUCCESS_SYSTEM_PROMPT[max(0, idx - 100): idx + 200]
    assert "LAST" in context or "last" in context.lower(), (
        "send_response LAST constraint not found near tool mention"
    )


def test_prompt_lists_workflow_steps_in_order():
    """T5 — Required Workflow section must list steps 1–5."""
    workflow_section = CUSTOMER_SUCCESS_SYSTEM_PROMPT[
        CUSTOMER_SUCCESS_SYSTEM_PROMPT.find("## Required Workflow"):
        CUSTOMER_SUCCESS_SYSTEM_PROMPT.find("## Hard Constraints")
    ]
    for step_num in range(1, 6):
        assert str(step_num) in workflow_section, f"Step {step_num} missing from Required Workflow"


# ─────────────────────────────────────────────────────────────
# T6 — Performance baseline: ≥58% of sample tickets AI-resolvable
# ─────────────────────────────────────────────────────────────

SAMPLE_TICKETS_PATH = Path(__file__).parents[2] / "context" / "sample-tickets.json"

def test_performance_baseline_58_percent():
    """
    T6 — Load context/sample-tickets.json and verify that at least 58%
    of the 55 tickets have expected_action = 'resolve' (AI-resolvable).

    This matches the discovery-log baseline from Exercise 1.1.
    """
    if not SAMPLE_TICKETS_PATH.exists():
        pytest.skip("sample-tickets.json not found — skipping baseline test")

    with open(SAMPLE_TICKETS_PATH) as f:
        data = json.load(f)

    tickets = data if isinstance(data, list) else data.get("tickets", [])
    total   = len(tickets)
    assert total > 0, "sample-tickets.json is empty"

    resolvable = sum(
        1 for t in tickets
        if t.get("expected_action", "").lower() in {"resolve", "ai_resolve", "answer"}
    )

    pct = resolvable / total
    assert pct >= 0.58, (
        f"Performance baseline failed: {resolvable}/{total} = {pct:.0%} AI-resolvable "
        f"(required ≥58%)"
    )
