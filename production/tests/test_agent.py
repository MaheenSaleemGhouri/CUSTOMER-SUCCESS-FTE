"""
production/tests/test_agent.py
Unit tests for the production agent tools and formatters.

Tests each @function_tool in isolation using mock asyncpg connections
and mock OpenAI client — no real network calls.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from production.agent import db_context, openai_context
from production.agent.tools import (
    KnowledgeSearchInput,
    TicketInput,
    CustomerHistoryInput,
    EscalationInput,
    ResponseInput,
    create_ticket,
    escalate_to_human,
    get_customer_history,
    search_knowledge_base,
    send_response,
)
from production.agent.formatters import (
    format_email,
    format_whatsapp,
    format_web_form,
    format_for_channel,
    WHATSAPP_HARD_MAX,
)

CUSTOMER_ID   = str(uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001"))
TICKET_ID     = str(uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002"))
CONV_ID       = str(uuid.UUID("cccccccc-0000-0000-0000-000000000003"))
NOW           = datetime(2026, 3, 9, 10, 0, 0, tzinfo=timezone.utc)


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _set_db_ctx(pool):
    """Inject a mock pool into the db_context ContextVar."""
    db_context.set(pool)


def _set_openai_ctx(client):
    openai_context.set(client)


# ─────────────────────────────────────────────────────────────
# FORMATTER UNIT TESTS
# ─────────────────────────────────────────────────────────────

class TestFormatEmail:
    def test_greeting_with_name(self):
        out = format_email("Body text.", "Carol")
        assert out.startswith("Dear Carol,")

    def test_greeting_without_name(self):
        out = format_email("Body text.")
        assert out.startswith("Dear Customer,")

    def test_signature_present(self):
        out = format_email("Body.", "Dan")
        assert "TechCorp AI Support Team" in out

    def test_body_preserved(self):
        body = "Your integration issue is resolved."
        out  = format_email(body, "Eve")
        assert body in out

    def test_name_stripped(self):
        out = format_email("Hi.", "  Frank  ")
        assert out.startswith("Dear Frank,")


class TestFormatWhatsApp:
    def test_suffix_always_present(self):
        out = format_whatsapp("Short.")
        assert "Type 'human'" in out

    def test_hard_max_enforced(self):
        long = "This is a very long message that exceeds the limit. " * 20
        out  = format_whatsapp(long)
        assert len(out) <= WHATSAPP_HARD_MAX

    def test_short_message_unchanged_body(self):
        msg = "Fixed!"
        out = format_whatsapp(msg)
        assert out.startswith(msg)

    def test_trim_at_sentence_boundary(self):
        # Build a message where trimming at char boundary would cut mid-word
        msg = ("Hello there. " * 25).strip()
        out = format_whatsapp(msg)
        assert len(out) <= WHATSAPP_HARD_MAX
        # Should not end with a partial word before the suffix
        body = out.split("\n\n")[0]
        assert not body[-1].isalpha() or body.endswith(".")


class TestFormatWebForm:
    def test_footer_appended(self):
        out = format_web_form("Here is your answer.")
        assert "support portal" in out.lower()

    def test_original_content_preserved(self):
        body = "Navigate to Settings → Integrations."
        out  = format_web_form(body)
        assert body in out

    def test_no_greeting(self):
        out = format_web_form("Content.")
        assert not out.startswith("Dear ")


# ─────────────────────────────────────────────────────────────
# TOOL: search_knowledge_base
# ─────────────────────────────────────────────────────────────

class TestSearchKnowledgeBase:
    @pytest.mark.asyncio
    async def test_vector_search_success(self, mock_pool, mock_openai, sample_kb_row):
        _set_db_ctx(mock_pool)
        _set_openai_ctx(mock_openai)

        with patch("production.database.queries.vector_search_kb", AsyncMock(return_value=[sample_kb_row])):
            result = await search_knowledge_base(
                KnowledgeSearchInput(query="How do I connect Slack?", top_k=3)
            )

        assert result["total_found"] == 1
        assert result["search_mode"] == "vector"
        assert result["results"][0]["title"] == sample_kb_row["title"]

    @pytest.mark.asyncio
    async def test_fts_fallback_when_no_openai(self, mock_pool, sample_kb_row):
        _set_db_ctx(mock_pool)
        openai_context.set(None)

        with patch("production.database.queries.fulltext_search_kb", AsyncMock(return_value=[sample_kb_row])):
            result = await search_knowledge_base(
                KnowledgeSearchInput(query="slack", top_k=2)
            )

        assert result["search_mode"] == "fulltext"
        assert result["total_found"] == 1

    @pytest.mark.asyncio
    async def test_returns_empty_on_db_error(self, mock_pool):
        _set_db_ctx(mock_pool)
        openai_context.set(None)

        with patch("production.database.queries.fulltext_search_kb", AsyncMock(side_effect=Exception("DB down"))):
            result = await search_knowledge_base(
                KnowledgeSearchInput(query="anything")
            )

        assert result["total_found"] == 0
        assert "error" in result

    @pytest.mark.asyncio
    async def test_top_k_clamped_to_5(self, mock_pool, sample_kb_row):
        _set_db_ctx(mock_pool)
        openai_context.set(None)

        with patch("production.database.queries.fulltext_search_kb", AsyncMock(return_value=[])) as mock_fts:
            await search_knowledge_base(KnowledgeSearchInput(query="x", top_k=99))
            _, kwargs = mock_fts.call_args
            assert kwargs.get("top_k", mock_fts.call_args[0][2] if len(mock_fts.call_args[0]) > 2 else 99) <= 5


# ─────────────────────────────────────────────────────────────
# TOOL: create_ticket
# ─────────────────────────────────────────────────────────────

class TestCreateTicket:
    @pytest.mark.asyncio
    async def test_creates_ticket_successfully(self, mock_pool, sample_ticket):
        _set_db_ctx(mock_pool)

        conv_mock   = {"id": uuid.UUID(CONV_ID)}
        ticket_mock = sample_ticket

        with patch("production.database.queries.get_or_create_conversation", AsyncMock(return_value=conv_mock)), \
             patch("production.database.queries.create_ticket", AsyncMock(return_value=ticket_mock)):

            result = await create_ticket(TicketInput(
                customer_id=CUSTOMER_ID,
                channel="email",
                issue_summary="Slack not connecting",
                original_message="My Slack integration fails.",
            ))

        assert result["ticket_ref"] == "TKT-ABCD1234"
        assert result["status"] == "open"

    @pytest.mark.asyncio
    async def test_returns_error_on_db_failure(self, mock_pool):
        _set_db_ctx(mock_pool)

        with patch("production.database.queries.get_or_create_conversation", AsyncMock(side_effect=Exception("timeout"))):
            result = await create_ticket(TicketInput(
                customer_id=CUSTOMER_ID,
                channel="email",
                issue_summary="Test",
                original_message="Test message",
            ))

        assert result["status"] == "error"
        assert "error" in result

    @pytest.mark.asyncio
    async def test_issue_summary_truncated_at_200(self, mock_pool, sample_ticket):
        _set_db_ctx(mock_pool)
        long_summary = "x" * 300

        conv_mock = {"id": uuid.UUID(CONV_ID)}
        captured  = {}

        async def capture_create(*args, **kwargs):
            captured["summary"] = kwargs.get("issue_summary", args[3] if len(args) > 3 else "")
            return sample_ticket

        with patch("production.database.queries.get_or_create_conversation", AsyncMock(return_value=conv_mock)), \
             patch("production.database.queries.create_ticket", capture_create):

            await create_ticket(TicketInput(
                customer_id=CUSTOMER_ID,
                channel="email",
                issue_summary=long_summary,
                original_message="body",
            ))

        assert len(captured.get("summary", "x" * 300)) <= 200


# ─────────────────────────────────────────────────────────────
# TOOL: get_customer_history
# ─────────────────────────────────────────────────────────────

class TestGetCustomerHistory:
    @pytest.mark.asyncio
    async def test_returns_customer_profile(self, mock_pool, sample_customer):
        _set_db_ctx(mock_pool)

        conv = {
            "id":            uuid.UUID(CONV_ID),
            "channel":       "email",
            "status":        "closed",
            "created_at":    NOW,
            "updated_at":    NOW,
            "message_count": 3,
        }

        with patch("production.database.queries.get_customer_by_id",      AsyncMock(return_value=sample_customer)), \
             patch("production.database.queries.get_customer_conversations", AsyncMock(return_value=[conv])), \
             patch("production.database.queries.get_customer_ticket_counts", AsyncMock(return_value={"total": 5, "open": 1})):

            result = await get_customer_history(CustomerHistoryInput(customer_id=CUSTOMER_ID))

        assert result["display_name"]  == "Alice Smith"
        assert result["total_tickets"] == 5
        assert result["open_tickets"]  == 1
        assert len(result["conversations"]) == 1

    @pytest.mark.asyncio
    async def test_returns_empty_for_unknown_customer(self, mock_pool):
        _set_db_ctx(mock_pool)

        with patch("production.database.queries.get_customer_by_id", AsyncMock(return_value=None)):
            result = await get_customer_history(CustomerHistoryInput(customer_id=CUSTOMER_ID))

        assert result["display_name"]    is None
        assert result["total_tickets"]   == 0
        assert result["conversations"]   == []


# ─────────────────────────────────────────────────────────────
# TOOL: escalate_to_human
# ─────────────────────────────────────────────────────────────

class TestEscalateToHuman:
    @pytest.mark.asyncio
    async def test_escalates_with_correct_routing(self, mock_pool, sample_ticket, sample_escalation):
        _set_db_ctx(mock_pool)

        with patch("production.database.queries.get_ticket",         AsyncMock(return_value=sample_ticket)), \
             patch("production.database.queries.update_ticket_status", AsyncMock()), \
             patch("production.database.queries.create_escalation",   AsyncMock(return_value=sample_escalation)):

            result = await escalate_to_human(EscalationInput(
                ticket_id=TICKET_ID,
                customer_id=CUSTOMER_ID,
                reason="technical_tier2",
                urgency="high",
            ))

        assert result["escalated"]       is True
        assert result["team"]            == "Engineering"
        assert result["routing_email"]   == "bugs@techcorp.io"
        assert result["ticket_ref"]      == "TKT-ABCD1234"
        assert "message_to_use"          in result

    @pytest.mark.asyncio
    async def test_unknown_reason_uses_default_routing(self, mock_pool, sample_ticket, sample_escalation):
        _set_db_ctx(mock_pool)

        with patch("production.database.queries.get_ticket",         AsyncMock(return_value=sample_ticket)), \
             patch("production.database.queries.update_ticket_status", AsyncMock()), \
             patch("production.database.queries.create_escalation",   AsyncMock(return_value=sample_escalation)):

            result = await escalate_to_human(EscalationInput(
                ticket_id=TICKET_ID,
                customer_id=CUSTOMER_ID,
                reason="unknown_reason_xyz",
            ))

        assert result["escalated"] is True
        assert "techcorp.io" in result["routing_email"]

    @pytest.mark.asyncio
    async def test_returns_error_dict_on_failure(self, mock_pool):
        _set_db_ctx(mock_pool)

        with patch("production.database.queries.get_ticket", AsyncMock(side_effect=Exception("DB error"))):
            result = await escalate_to_human(EscalationInput(
                ticket_id=TICKET_ID,
                customer_id=CUSTOMER_ID,
                reason="angry_customer",
            ))

        assert result["escalated"] is False
        assert "error" in result


# ─────────────────────────────────────────────────────────────
# TOOL: send_response
# ─────────────────────────────────────────────────────────────

class TestSendResponse:
    @pytest.mark.asyncio
    async def test_formats_and_persists_email(self, mock_pool, sample_ticket):
        _set_db_ctx(mock_pool)
        msg_mock = {"id": uuid.uuid4()}

        with patch("production.database.queries.get_ticket",              AsyncMock(return_value=sample_ticket)), \
             patch("production.database.queries.get_or_create_conversation", AsyncMock(return_value={"id": uuid.UUID(CONV_ID)})), \
             patch("production.database.queries.insert_message",           AsyncMock(return_value=msg_mock)):

            result = await send_response(ResponseInput(
                ticket_id=TICKET_ID,
                customer_id=CUSTOMER_ID,
                channel="email",
                response_text="Your issue is now resolved.",
                customer_name="Alice",
            ))

        assert result["sent"] is True
        assert result["ticket_ref"] == "TKT-ABCD1234"
        assert "Dear Alice," in result["formatted_response"]

    @pytest.mark.asyncio
    async def test_formats_whatsapp_under_300(self, mock_pool, sample_ticket):
        _set_db_ctx(mock_pool)
        long_text = "Here is a very detailed answer. " * 30
        msg_mock  = {"id": uuid.uuid4()}

        with patch("production.database.queries.get_ticket",              AsyncMock(return_value=sample_ticket)), \
             patch("production.database.queries.get_or_create_conversation", AsyncMock(return_value={"id": uuid.UUID(CONV_ID)})), \
             patch("production.database.queries.insert_message",           AsyncMock(return_value=msg_mock)):

            result = await send_response(ResponseInput(
                ticket_id=TICKET_ID,
                customer_id=CUSTOMER_ID,
                channel="whatsapp",
                response_text=long_text,
            ))

        assert result["sent"] is True
        assert len(result["formatted_response"]) <= 300

    @pytest.mark.asyncio
    async def test_returns_error_dict_on_failure(self, mock_pool):
        _set_db_ctx(mock_pool)

        with patch("production.database.queries.get_ticket", AsyncMock(side_effect=Exception("DB error"))):
            result = await send_response(ResponseInput(
                ticket_id=TICKET_ID,
                customer_id=CUSTOMER_ID,
                channel="web_form",
                response_text="Response.",
            ))

        assert result["sent"]  is False
        assert "error"         in result
