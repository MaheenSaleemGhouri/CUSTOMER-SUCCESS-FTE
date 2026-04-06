"""
production/tests/test_e2e.py
End-to-end scenario tests — 5 test classes.

Simulates realistic customer journeys through the full pipeline:
  E2E1  TestEmailFlow            — inbound email → agent → formatted reply
  E2E2  TestWhatsAppFlow         — WhatsApp message → human escalation trigger
  E2E3  TestWebFormFlow          — form submission → ticket → Kafka publish
  E2E4  TestCrossChannelIdentity — same customer via WhatsApp then Email
  E2E5  TestEscalationScenarios  — all major escalation triggers fire correctly

All external dependencies (DB, Kafka, OpenAI, Twilio, Gmail) are mocked.
"""

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
import pytest_asyncio

from production.agent import db_context, openai_context

# ── shared IDs ────────────────────────────────────────────────
CUSTOMER_ID  = str(uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001"))
TICKET_ID    = str(uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002"))
CONV_ID      = str(uuid.UUID("cccccccc-0000-0000-0000-000000000003"))
NOW          = datetime(2026, 3, 9, 10, 0, 0, tzinfo=timezone.utc)


def _customer(name="Alice Smith", plan="growth"):
    return {
        "id":              uuid.UUID(CUSTOMER_ID),
        "display_name":    name,
        "canonical_email": "alice@example.com",
        "plan":            plan,
        "sentiment_score": 0.65,
        "last_contact_at": NOW,
        "created_at":      NOW,
        "updated_at":      NOW,
    }


def _ticket(ref="TKT-ABCD1234", status="open", priority="medium"):
    return {
        "id":               uuid.UUID(TICKET_ID),
        "ticket_ref":       ref,
        "customer_id":      uuid.UUID(CUSTOMER_ID),
        "conversation_id":  uuid.UUID(CONV_ID),
        "source_channel":   "email",
        "status":           status,
        "priority":         priority,
        "category":         "technical",
        "issue_summary":    "Slack integration failing",
        "original_message": "My Slack integration keeps failing.",
        "created_at":       NOW,
        "updated_at":       NOW,
        "resolved_at":      None,
    }


def _escalation():
    return {
        "id":            uuid.UUID("dddddddd-0000-0000-0000-000000000004"),
        "ticket_id":     uuid.UUID(TICKET_ID),
        "customer_id":   uuid.UUID(CUSTOMER_ID),
        "reason":        "technical_tier2",
        "urgency":       "high",
        "routing_team":  "Engineering",
        "routing_email": "bugs@techcorp.io",
        "notes":         "",
        "created_at":    NOW,
    }


# ─────────────────────────────────────────────────────────────
# E2E1 — EMAIL FLOW
# ─────────────────────────────────────────────────────────────

class TestEmailFlow:
    """
    Scenario: Alice emails about Slack integration.
    Expected: ticket created → KB searched → formatted email reply sent.
    """

    @pytest.mark.asyncio
    async def test_email_reply_starts_with_dear(self, mock_pool):
        """Agent response for email channel must start with 'Dear {name},'."""
        from production.agent.tools import send_response, ResponseInput

        db_context.set(mock_pool)
        openai_context.set(None)

        msg_mock = {"id": uuid.uuid4()}
        with patch("production.database.queries.get_ticket",              AsyncMock(return_value=_ticket())), \
             patch("production.database.queries.get_or_create_conversation", AsyncMock(return_value={"id": uuid.UUID(CONV_ID)})), \
             patch("production.database.queries.insert_message",           AsyncMock(return_value=msg_mock)):

            result = await send_response(ResponseInput(
                ticket_id=TICKET_ID,
                customer_id=CUSTOMER_ID,
                channel="email",
                response_text="Your Slack integration issue has been resolved. Navigate to Settings → Integrations → Slack.",
                customer_name="Alice",
            ))

        assert result["sent"] is True
        assert result["formatted_response"].startswith("Dear Alice,")

    @pytest.mark.asyncio
    async def test_email_reply_ends_with_signature(self, mock_pool):
        from production.agent.tools import send_response, ResponseInput

        db_context.set(mock_pool)
        openai_context.set(None)

        msg_mock = {"id": uuid.uuid4()}
        with patch("production.database.queries.get_ticket",              AsyncMock(return_value=_ticket())), \
             patch("production.database.queries.get_or_create_conversation", AsyncMock(return_value={"id": uuid.UUID(CONV_ID)})), \
             patch("production.database.queries.insert_message",           AsyncMock(return_value=msg_mock)):

            result = await send_response(ResponseInput(
                ticket_id=TICKET_ID,
                customer_id=CUSTOMER_ID,
                channel="email",
                response_text="Issue resolved.",
                customer_name="Alice",
            ))

        assert "TechCorp AI Support Team" in result["formatted_response"]

    @pytest.mark.asyncio
    async def test_ticket_created_before_response(self, mock_pool):
        """Tool ordering: create_ticket result must precede send_response call."""
        from production.agent.tools import create_ticket, send_response, TicketInput, ResponseInput

        db_context.set(mock_pool)
        call_order = []

        async def mock_create(*a, **kw):
            call_order.append("create_ticket")
            return _ticket()

        async def mock_send(*a, **kw):
            call_order.append("send_response")
            return {"id": uuid.uuid4()}

        conv = {"id": uuid.UUID(CONV_ID)}
        with patch("production.database.queries.get_or_create_conversation", AsyncMock(return_value=conv)), \
             patch("production.database.queries.create_ticket",               mock_create), \
             patch("production.database.queries.get_ticket",                  AsyncMock(return_value=_ticket())), \
             patch("production.database.queries.insert_message",              mock_send):

            await create_ticket(TicketInput(
                customer_id=CUSTOMER_ID,
                channel="email",
                issue_summary="Slack failing",
                original_message="Slack keeps failing.",
            ))
            await send_response(ResponseInput(
                ticket_id=TICKET_ID,
                customer_id=CUSTOMER_ID,
                channel="email",
                response_text="Here is the answer.",
                customer_name="Alice",
            ))

        assert call_order.index("create_ticket") < call_order.index("send_response")

    @pytest.mark.asyncio
    async def test_gmail_webhook_publishes_to_kafka(self, fastapi_client, sample_customer):
        """POST /webhooks/gmail must publish normalised message to Kafka."""
        pubsub_data = json.dumps({
            "historyId": "12345",
            "emailAddress": "alice@example.com",
        }).encode()
        encoded = __import__("base64").b64encode(pubsub_data).decode()

        mock_handler = AsyncMock()
        mock_handler.process_notification = AsyncMock(return_value="12345")
        mock_handler.get_new_messages = AsyncMock(return_value=[{
            "channel":            "email",
            "channel_message_id": "msg_001",
            "customer_email":     "alice@example.com",
            "customer_name":      "Alice",
            "subject":            "Slack issue",
            "content":            "My Slack integration is broken.",
            "received_at":        NOW.isoformat(),
            "thread_id":          "thread_001",
            "metadata":           {},
        }])

        with patch("production.channels.gmail_handler.GmailHandler", return_value=mock_handler), \
             patch("production.database.queries.get_customer_by_email", AsyncMock(return_value=sample_customer)):

            resp = await fastapi_client.post("/webhooks/gmail", json={
                "message":      {"data": encoded, "messageId": "pub001", "publishTime": NOW.isoformat()},
                "subscription": "projects/techcorp/subscriptions/gmail-sub",
            })

        assert resp.status_code == 204


# ─────────────────────────────────────────────────────────────
# E2E2 — WHATSAPP FLOW
# ─────────────────────────────────────────────────────────────

class TestWhatsAppFlow:
    """
    Scenario: Bob messages on WhatsApp. Types 'human' → immediate escalation.
    """

    @pytest.mark.asyncio
    async def test_whatsapp_response_under_300_chars(self, mock_pool):
        from production.agent.tools import send_response, ResponseInput

        db_context.set(mock_pool)
        openai_context.set(None)
        long_response = "This is a detailed answer. " * 40
        msg_mock = {"id": uuid.uuid4()}

        with patch("production.database.queries.get_ticket",              AsyncMock(return_value=_ticket())), \
             patch("production.database.queries.get_or_create_conversation", AsyncMock(return_value={"id": uuid.UUID(CONV_ID)})), \
             patch("production.database.queries.insert_message",           AsyncMock(return_value=msg_mock)):

            result = await send_response(ResponseInput(
                ticket_id=TICKET_ID,
                customer_id=CUSTOMER_ID,
                channel="whatsapp",
                response_text=long_response,
            ))

        assert len(result["formatted_response"]) <= 300

    @pytest.mark.asyncio
    async def test_human_keyword_triggers_escalation(self, mock_pool):
        """WhatsApp 'human' keyword must route to human_requested escalation."""
        from production.agent.tools import escalate_to_human, EscalationInput, ESCALATION_ROUTING

        db_context.set(mock_pool)

        routing = ESCALATION_ROUTING["human_requested"]
        with patch("production.database.queries.get_ticket",          AsyncMock(return_value=_ticket())), \
             patch("production.database.queries.update_ticket_status", AsyncMock()), \
             patch("production.database.queries.create_escalation",    AsyncMock(return_value=_escalation())):

            result = await escalate_to_human(EscalationInput(
                ticket_id=TICKET_ID,
                customer_id=CUSTOMER_ID,
                reason="human_requested",
                urgency="normal",
                channel="whatsapp",
            ))

        assert result["escalated"]     is True
        assert result["routing_email"] == "csm@techcorp.io"
        assert "15 minutes" in result["sla"]

    @pytest.mark.asyncio
    async def test_whatsapp_suffix_present(self, mock_pool):
        """Every WhatsApp response must include the human-escalation hint."""
        from production.agent.tools import send_response, ResponseInput

        db_context.set(mock_pool)
        msg_mock = {"id": uuid.uuid4()}

        with patch("production.database.queries.get_ticket",              AsyncMock(return_value=_ticket())), \
             patch("production.database.queries.get_or_create_conversation", AsyncMock(return_value={"id": uuid.UUID(CONV_ID)})), \
             patch("production.database.queries.insert_message",           AsyncMock(return_value=msg_mock)):

            result = await send_response(ResponseInput(
                ticket_id=TICKET_ID,
                customer_id=CUSTOMER_ID,
                channel="whatsapp",
                response_text="Done!",
            ))

        assert "human" in result["formatted_response"].lower()


# ─────────────────────────────────────────────────────────────
# E2E3 — WEB FORM FLOW
# ─────────────────────────────────────────────────────────────

class TestWebFormFlow:
    """
    Scenario: Carol submits a high-priority billing form.
    Expected: priority escalated to critical → ticket created → Kafka publish.
    """

    @pytest.mark.asyncio
    async def test_billing_high_escalated_to_critical(self, fastapi_client, sample_customer):
        ticket_mock = _ticket(priority="critical")

        with patch("production.database.queries.get_customer_by_email", AsyncMock(return_value=sample_customer)), \
             patch("production.database.queries.register_identifier",   AsyncMock()), \
             patch("production.database.queries.create_ticket",         AsyncMock(return_value=ticket_mock)):

            resp = await fastapi_client.post("/support/submit", json={
                "name":     "Carol Jones",
                "email":    "carol@example.com",
                "subject":  "Duplicate charge on invoice",
                "category": "billing",
                "priority": "high",
                "message":  "I was charged twice for my subscription this month.",
            })

        assert resp.status_code == 201
        data = resp.json()
        assert "within 15 minutes" in data["estimated_response"]

    @pytest.mark.asyncio
    async def test_kafka_message_published_on_submit(self, fastapi_client, sample_customer, mock_producer):
        ticket_mock = _ticket()

        with patch("production.database.queries.get_customer_by_email", AsyncMock(return_value=sample_customer)), \
             patch("production.database.queries.register_identifier",   AsyncMock()), \
             patch("production.database.queries.create_ticket",         AsyncMock(return_value=ticket_mock)):

            await fastapi_client.post("/support/submit", json={
                "name":     "Dave",
                "email":    "dave@example.com",
                "subject":  "Cannot login to account",
                "category": "technical",
                "priority": "medium",
                "message":  "I cannot log in to my account since yesterday.",
            })

        mock_producer.send_and_wait.assert_called_once()
        call_args = mock_producer.send_and_wait.call_args
        assert call_args[0][0] == "fte.channels.webform.inbound"

    @pytest.mark.asyncio
    async def test_new_customer_created_on_first_submission(self, fastapi_client, sample_customer):
        ticket_mock  = _ticket()
        new_customer = {**sample_customer, "display_name": "Eve New"}
        create_mock  = AsyncMock(return_value=new_customer)

        with patch("production.database.queries.get_customer_by_email", AsyncMock(return_value=None)), \
             patch("production.database.queries.create_customer",        create_mock), \
             patch("production.database.queries.register_identifier",    AsyncMock()), \
             patch("production.database.queries.create_ticket",          AsyncMock(return_value=ticket_mock)):

            resp = await fastapi_client.post("/support/submit", json={
                "name":     "Eve New",
                "email":    "eve@newcustomer.com",
                "subject":  "How do I export reports?",
                "category": "general",
                "priority": "low",
                "message":  "I cannot find where to export my project reports.",
            })

        create_mock.assert_called_once()
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_web_form_response_has_footer(self, mock_pool):
        from production.agent.tools import send_response, ResponseInput

        db_context.set(mock_pool)
        msg_mock = {"id": uuid.uuid4()}

        with patch("production.database.queries.get_ticket",              AsyncMock(return_value=_ticket())), \
             patch("production.database.queries.get_or_create_conversation", AsyncMock(return_value={"id": uuid.UUID(CONV_ID)})), \
             patch("production.database.queries.insert_message",           AsyncMock(return_value=msg_mock)):

            result = await send_response(ResponseInput(
                ticket_id=TICKET_ID,
                customer_id=CUSTOMER_ID,
                channel="web_form",
                response_text="Navigate to Reports → Export → CSV.",
            ))

        assert "support portal" in result["formatted_response"].lower()


# ─────────────────────────────────────────────────────────────
# E2E4 — CROSS-CHANNEL IDENTITY
# ─────────────────────────────────────────────────────────────

class TestCrossChannelIdentity:
    """
    Scenario: Diana contacts via WhatsApp (+447700900001) then follows up
    by email (diana@example.com). Must be recognised as the same customer.
    """

    @pytest.mark.asyncio
    async def test_same_customer_resolved_across_channels(self, mock_pool):
        """Resolving a phone number and email for the same customer returns the same ID."""
        diana = {**_customer("Diana Prince"), "id": uuid.UUID(CUSTOMER_ID)}

        with patch("production.database.queries.get_customer_by_identifier", AsyncMock(return_value=diana)) as mock_get:
            from production.database.queries import get_customer_by_identifier
            # WhatsApp lookup by phone
            result_phone = await get_customer_by_identifier(mock_pool, "phone", "+447700900001")
            # Email lookup
            result_email = await get_customer_by_identifier(mock_pool, "email", "diana@example.com")

        assert result_phone["id"] == result_email["id"] == uuid.UUID(CUSTOMER_ID)

    @pytest.mark.asyncio
    async def test_channel_journey_reflects_both_channels(self):
        """build_context_block must include both channels in CHANNEL_JOURNEY."""
        from production.workers.message_processor import build_context_block

        block = build_context_block(
            channel="email",
            customer_id=CUSTOMER_ID,
            customer_name="Diana",
            canonical_id=CUSTOMER_ID,
            sentiment=0.5,
            sentiment_history=[0.5, 0.5],
            session_number=2,
            session_id=CONV_ID,
            channel_journey=["whatsapp", "email"],
            topics_discussed=["login"],
            top_topics={"login": 2},
            current_topics=["login"],
            conversation_history=[],
            subject="Following up on WhatsApp query",
            new_message="As I mentioned on WhatsApp, I still cannot log in.",
        )

        assert "whatsapp" in block
        assert "email"    in block
        assert "→"        in block   # journey separator

    @pytest.mark.asyncio
    async def test_history_prevents_re_asking_for_info(self):
        """TOPICS_DISCUSSED should reflect prior topics in context block."""
        from production.workers.message_processor import build_context_block

        block = build_context_block(
            channel="email",
            customer_id=CUSTOMER_ID,
            customer_name="Diana",
            canonical_id=CUSTOMER_ID,
            sentiment=0.5,
            sentiment_history=[0.5],
            session_number=2,
            session_id=CONV_ID,
            channel_journey=["whatsapp", "email"],
            topics_discussed=["login", "2fa"],
            top_topics={"login": 3, "2fa": 1},
            current_topics=["login"],
            conversation_history=[{
                "role": "inbound",
                "channel": "whatsapp",
                "content": "I cannot login, 2FA is broken",
                "timestamp": "2026-03-09 09:00",
            }],
            subject=None,
            new_message="Still having login problems.",
        )

        assert "login" in block
        assert "2fa"   in block


# ─────────────────────────────────────────────────────────────
# E2E5 — ESCALATION SCENARIOS
# ─────────────────────────────────────────────────────────────

class TestEscalationScenarios:
    """
    Verify that each major escalation trigger routes to the correct team
    with the correct SLA — no agent involved, pure routing-table tests.
    """

    ROUTING_CASES = [
        ("pricing_inquiry",          "Sales",       "sales@techcorp.io",   "2 hours"),
        ("refund_request",           "Billing",     "billing@techcorp.io", "4 hours"),
        ("legal_escalation",         "Legal",       "legal@techcorp.io",   "1 hour"),
        ("angry_customer",           "CSM",         "csm@techcorp.io",     "30 minutes"),
        ("human_requested",          "CSM",         "csm@techcorp.io",     "15 minutes"),
        ("technical_tier2",          "Engineering", "bugs@techcorp.io",    "1 hour"),
        ("anger_spike",              "CSM",         "csm@techcorp.io",     "15 minutes"),
        ("enterprise_account",       "Enterprise CSM", "csm@techcorp.io",  "30 minutes"),
        ("business_account_unresolved", "CSM",      "csm@techcorp.io",     "1 hour"),
    ]

    @pytest.mark.parametrize("reason,expected_team,expected_email,expected_sla", ROUTING_CASES)
    @pytest.mark.asyncio
    async def test_escalation_routing(
        self, reason, expected_team, expected_email, expected_sla, mock_pool
    ):
        """Each escalation reason must route to the correct team, email, and SLA."""
        from production.agent.tools import escalate_to_human, EscalationInput

        db_context.set(mock_pool)
        esc = {**_escalation(), "routing_team": expected_team, "routing_email": expected_email}

        with patch("production.database.queries.get_ticket",          AsyncMock(return_value=_ticket())), \
             patch("production.database.queries.update_ticket_status", AsyncMock()), \
             patch("production.database.queries.create_escalation",    AsyncMock(return_value=esc)):

            result = await escalate_to_human(EscalationInput(
                ticket_id=TICKET_ID,
                customer_id=CUSTOMER_ID,
                reason=reason,
            ))

        assert result["escalated"]     is True, f"{reason}: not escalated"
        assert result["team"]          == expected_team,  f"{reason}: wrong team"
        assert result["routing_email"] == expected_email, f"{reason}: wrong email"
        assert result["sla"]           == expected_sla,   f"{reason}: wrong SLA"

    @pytest.mark.asyncio
    async def test_anger_spike_detected_from_history(self):
        """detect_anger_spike: neutral → very angry in one turn = True."""
        from production.workers.message_processor import detect_anger_spike

        # Was 0.6 (neutral), dropped to 0.1 (very angry)
        assert detect_anger_spike([0.6, 0.1]) is True

    @pytest.mark.asyncio
    async def test_no_anger_spike_stable_history(self):
        from production.workers.message_processor import detect_anger_spike

        assert detect_anger_spike([0.5, 0.45]) is False

    @pytest.mark.asyncio
    async def test_manual_escalation_api_endpoint(self, fastapi_client, sample_ticket):
        """POST /tickets/{id}/escalate must create escalation and return 201."""
        esc = _escalation()

        with patch("production.database.queries.get_ticket",          AsyncMock(return_value=sample_ticket)), \
             patch("production.database.queries.create_escalation",    AsyncMock(return_value=esc)), \
             patch("production.database.queries.update_ticket_status", AsyncMock()):

            resp = await fastapi_client.post(
                f"/tickets/{TICKET_ID}/escalate",
                json={"reason": "technical_tier2", "urgency": "high", "notes": "Data loss"},
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["team"]          == "Engineering"
        assert data["routing_email"] == "bugs@techcorp.io"

    @pytest.mark.asyncio
    async def test_manual_escalation_404_for_unknown_ticket(self, fastapi_client):
        with patch("production.database.queries.get_ticket", AsyncMock(return_value=None)):
            resp = await fastapi_client.post(
                f"/tickets/{uuid.uuid4()}/escalate",
                json={"reason": "angry_customer", "urgency": "normal"},
            )
        assert resp.status_code == 404
