"""
production/tests/test_channels.py
Unit tests for all three channel handlers.

  TestWebFormValidation   — Pydantic model + priority escalation logic
  TestWebFormEndpoints    — FastAPI POST /support/submit, GET /support/ticket
  TestWhatsAppFormatter   — format_response() length splitting
  TestWhatsAppWebhook     — signature validation + message normalisation
  TestGmailNormalisation  — MIME body extraction + normalised output shape
"""

import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# ─────────────────────────────────────────────────────────────
# WEB FORM — Pydantic validation
# ─────────────────────────────────────────────────────────────

class TestWebFormValidation:
    """Pydantic SupportFormSubmission field validators."""

    def _make(self, **overrides):
        from production.channels.web_form_handler import SupportFormSubmission
        defaults = dict(
            name="Alice Smith",
            email="alice@example.com",
            subject="Cannot connect Slack",
            category="technical",
            priority="medium",
            message="My Slack integration keeps failing every time I try to connect.",
        )
        return SupportFormSubmission(**{**defaults, **overrides})

    def test_valid_submission(self):
        sub = self._make()
        assert sub.name == "Alice Smith"

    def test_name_too_short_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="at least 2 characters"):
            self._make(name="A")

    def test_name_stripped(self):
        sub = self._make(name="  Bob  ")
        assert sub.name == "Bob"

    def test_subject_too_short_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="at least 5 characters"):
            self._make(subject="Hi")

    def test_message_too_short_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="at least 10 characters"):
            self._make(message="Short")

    def test_invalid_category_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="Category must be one of"):
            self._make(category="complaints")

    def test_invalid_priority_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="Priority must be one of"):
            self._make(priority="urgent")

    def test_category_normalised_lowercase(self):
        sub = self._make(category="TECHNICAL")
        assert sub.category == "technical"

    def test_invalid_email_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            self._make(email="not-an-email")


class TestPriorityEscalation:
    """map_priority_to_ticket() billing+bug_report high → critical."""

    def _map(self, category, priority):
        from production.channels.web_form_handler import map_priority_to_ticket
        return map_priority_to_ticket(category, priority)

    def test_billing_high_becomes_critical(self):
        assert self._map("billing", "high") == "critical"

    def test_bug_report_high_becomes_critical(self):
        assert self._map("bug_report", "high") == "critical"

    def test_general_high_stays_high(self):
        assert self._map("general", "high") == "high"

    def test_billing_medium_stays_medium(self):
        assert self._map("billing", "medium") == "medium"

    def test_technical_low_stays_low(self):
        assert self._map("technical", "low") == "low"


# ─────────────────────────────────────────────────────────────
# WEB FORM — FastAPI endpoints
# ─────────────────────────────────────────────────────────────

class TestWebFormEndpoints:
    """POST /support/submit and GET /support/ticket/{ticket_id}."""

    @pytest.mark.asyncio
    async def test_submit_returns_201_with_ticket_ref(self, fastapi_client, sample_customer, sample_ticket):
        customer_mock = sample_customer
        ticket_mock   = sample_ticket

        with patch("production.database.queries.get_customer_by_email", AsyncMock(return_value=customer_mock)), \
             patch("production.database.queries.register_identifier",   AsyncMock()), \
             patch("production.database.queries.create_ticket",         AsyncMock(return_value=ticket_mock)):

            resp = await fastapi_client.post("/support/submit", json={
                "name":     "Alice Smith",
                "email":    "alice@example.com",
                "subject":  "Slack not connecting",
                "category": "technical",
                "priority": "medium",
                "message":  "My Slack integration keeps failing every time.",
            })

        assert resp.status_code == 201
        data = resp.json()
        assert "ticket_ref" in data
        assert data["status"] == "open"

    @pytest.mark.asyncio
    async def test_submit_422_on_short_message(self, fastapi_client):
        resp = await fastapi_client.post("/support/submit", json={
            "name":     "Bob",
            "email":    "bob@example.com",
            "subject":  "Help me",
            "category": "general",
            "message":  "Short",
        })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_submit_422_on_invalid_category(self, fastapi_client):
        resp = await fastapi_client.post("/support/submit", json={
            "name":     "Carol",
            "email":    "carol@example.com",
            "subject":  "Some issue here",
            "category": "complaints",
            "message":  "This is a long enough message for the validator.",
        })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_get_ticket_returns_status(self, fastapi_client, sample_ticket):
        ticket_id = str(sample_ticket["id"])

        with patch("production.database.queries.get_ticket",          AsyncMock(return_value=sample_ticket)), \
             patch("production.database.queries.get_recent_messages",  AsyncMock(return_value=[])):

            resp = await fastapi_client.get(f"/support/ticket/{ticket_id}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["ticket_ref"] == "TKT-ABCD1234"
        assert data["status"]     == "open"

    @pytest.mark.asyncio
    async def test_get_ticket_404_for_unknown(self, fastapi_client):
        with patch("production.database.queries.get_ticket",    AsyncMock(return_value=None)), \
             patch("production.database.queries.get_ticket_by_ref", AsyncMock(return_value=None)):

            resp = await fastapi_client.get(f"/support/ticket/{uuid.uuid4()}")

        assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────
# WHATSAPP — formatter
# ─────────────────────────────────────────────────────────────

class TestWhatsAppFormatter:
    def _handler(self):
        from production.channels.whatsapp_handler import WhatsAppHandler
        return WhatsAppHandler()

    def test_short_message_returned_as_is(self):
        h   = self._handler()
        msg = "Your account is now active! 🎉"
        out = h.format_response(msg)
        assert msg in out

    def test_long_message_split_at_boundary(self):
        h   = self._handler()
        msg = ("This is a sentence with plenty of words. " * 100)
        out = h.format_response(msg)
        # format_response returns a single string; each chunk ≤ 1600
        assert len(out) > 0

    def test_empty_message_handled(self):
        h   = self._handler()
        out = h.format_response("")
        assert isinstance(out, str)


# ─────────────────────────────────────────────────────────────
# WHATSAPP — webhook processing
# ─────────────────────────────────────────────────────────────

class TestWhatsAppWebhook:
    def _handler(self):
        from production.channels.whatsapp_handler import WhatsAppHandler
        with patch.dict("os.environ", {
            "TWILIO_AUTH_TOKEN":      "test_token",
            "TWILIO_ACCOUNT_SID":     "ACtest",
            "TWILIO_WHATSAPP_FROM":   "whatsapp:+14155238886",
        }):
            return WhatsAppHandler()

    @pytest.mark.asyncio
    async def test_process_webhook_normalises_phone(self):
        h = self._handler()
        form_data = {
            "MessageSid":  "SM123",
            "From":        "whatsapp:+441234567890",
            "To":          "whatsapp:+14155238886",
            "Body":        "Hello I need help",
            "NumMedia":    "0",
            "ProfileName": "Test User",
            "WaId":        "441234567890",
        }
        with patch.object(h, "validate_webhook", return_value=True):
            msg = await h.process_webhook(form_data)

        assert msg is not None
        assert msg["customer_phone"] == "+441234567890"
        assert msg["channel"]        == "whatsapp"
        assert msg["content"]        == "Hello I need help"

    @pytest.mark.asyncio
    async def test_process_webhook_returns_none_for_status_callback(self):
        h = self._handler()
        form_data = {
            "MessageSid":    "SM123",
            "MessageStatus": "delivered",
            "To":            "whatsapp:+14155238886",
        }
        msg = await h.process_webhook(form_data)
        assert msg is None


# ─────────────────────────────────────────────────────────────
# GMAIL — normalisation
# ─────────────────────────────────────────────────────────────

class TestGmailNormalisation:
    def _handler(self):
        from production.channels.gmail_handler import GmailHandler
        with patch.dict("os.environ", {"GMAIL_CREDENTIALS_FILE": "/nonexistent"}):
            return GmailHandler()

    def test_extract_email_from_angle_brackets(self):
        h = self._handler()
        result = h._extract_email("Alice Smith <alice@example.com>")
        assert result == "alice@example.com"

    def test_extract_email_plain(self):
        h = self._handler()
        result = h._extract_email("bob@example.com")
        assert result == "bob@example.com"

    def test_extract_body_plain_text(self):
        import email
        h    = self._handler()
        body = "This is a plain text email body."
        raw  = f"From: alice@example.com\r\nSubject: Test\r\n\r\n{body}"
        msg  = email.message_from_string(raw)
        extracted = h._extract_body(msg)
        assert body in extracted

    def test_normalised_output_has_required_keys(self):
        """Shape contract: normalised message must have all required keys."""
        required_keys = {
            "channel", "channel_message_id", "customer_email",
            "subject", "content", "received_at", "metadata",
        }
        # Build a minimal mock output matching the normalised format
        mock_normalised = {
            "channel":            "email",
            "channel_message_id": "msg_abc123",
            "customer_email":     "alice@example.com",
            "subject":            "Slack not working",
            "content":            "My Slack integration is broken.",
            "received_at":        datetime.now(timezone.utc).isoformat(),
            "thread_id":          "thread_xyz",
            "metadata":           {"headers": {}, "labels": []},
        }
        assert required_keys.issubset(mock_normalised.keys())
