"""
production/agent/tools.py
Production @function_tool implementations for the Customer Success FTE agent.

All 5 tools:
  1. search_knowledge_base  — pgvector cosine search + FTS fallback
  2. create_ticket          — insert ticket row, return ticket_ref
  3. get_customer_history   — return prior conversations + sentiment
  4. escalate_to_human      — route to team email, insert escalation row
  5. send_response          — format for channel, persist outbound message

Context injection (avoids global state):
  db_context     = contextvars.ContextVar('db_pool')
  openai_context = contextvars.ContextVar('openai_client')

Usage:
    from production.agent.tools import (
        search_knowledge_base, create_ticket,
        get_customer_history, escalate_to_human,
        send_response, db_context, openai_context,
    )

    # In message_processor, before Runner.run():
    db_context.set(db_pool)
    openai_context.set(openai_client)
"""

import contextvars
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from agents import function_tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# CONTEXT VARS  (set by message_processor before Runner.run())
# ─────────────────────────────────────────────────────────────

db_context:     contextvars.ContextVar = contextvars.ContextVar("db_pool")
openai_context: contextvars.ContextVar = contextvars.ContextVar("openai_client")

# ─────────────────────────────────────────────────────────────
# ESCALATION ROUTING TABLE
# ─────────────────────────────────────────────────────────────

ESCALATION_ROUTING = {
    "pricing_inquiry":          {"team": "Sales",         "email": "sales@techcorp.io",   "sla": "2 hours"},
    "refund_request":           {"team": "Billing",       "email": "billing@techcorp.io", "sla": "4 hours"},
    "legal_escalation":         {"team": "Legal",         "email": "legal@techcorp.io",   "sla": "1 hour"},
    "angry_customer":           {"team": "CSM",           "email": "csm@techcorp.io",     "sla": "30 minutes"},
    "human_requested":          {"team": "CSM",           "email": "csm@techcorp.io",     "sla": "15 minutes"},
    "technical_tier2":          {"team": "Engineering",   "email": "bugs@techcorp.io",    "sla": "1 hour"},
    "anger_spike":              {"team": "CSM",           "email": "csm@techcorp.io",     "sla": "15 minutes"},
    "enterprise_account":       {"team": "Enterprise CSM","email": "csm@techcorp.io",     "sla": "30 minutes"},
    "business_account_unresolved": {"team": "CSM",        "email": "csm@techcorp.io",     "sla": "1 hour"},
}

DEFAULT_ESCALATION = {"team": "Support", "email": "support@techcorp.io", "sla": "4 hours"}


# ─────────────────────────────────────────────────────────────
# PYDANTIC INPUT MODELS
# ─────────────────────────────────────────────────────────────

class KnowledgeSearchInput(BaseModel):
    query:      str           = Field(..., description="Natural-language search query")
    top_k:      int           = Field(3,   description="Number of results to return (1–5)")
    channel:    Optional[str] = Field(None, description="Originating channel for context")


class TicketInput(BaseModel):
    customer_id:     str           = Field(..., description="Canonical customer UUID")
    channel:         str           = Field(..., description="email | whatsapp | web_form")
    issue_summary:   str           = Field(..., description="One-sentence issue description")
    original_message:str           = Field(..., description="Verbatim customer message")
    priority:        str           = Field("medium", description="low | medium | high | critical")
    category:        str           = Field("general", description="Issue category")
    conversation_id: Optional[str] = Field(None, description="Existing conversation UUID if known")


class CustomerHistoryInput(BaseModel):
    customer_id: str = Field(..., description="Canonical customer UUID")
    limit:       int = Field(5,   description="Number of prior conversations to return")


class EscalationInput(BaseModel):
    ticket_id:      str           = Field(..., description="UUID of the current ticket")
    customer_id:    str           = Field(..., description="Canonical customer UUID")
    reason:         str           = Field(..., description="Escalation reason tag (see routing table)")
    urgency:        str           = Field("normal", description="normal | high | critical")
    notes:          Optional[str] = Field(None, description="Internal notes for the receiving team")
    channel:        Optional[str] = Field(None, description="Originating channel")


class ResponseInput(BaseModel):
    ticket_id:       str           = Field(..., description="UUID of the current ticket")
    customer_id:     str           = Field(..., description="Canonical customer UUID")
    channel:         str           = Field(..., description="email | whatsapp | web_form")
    response_text:   str           = Field(..., description="Raw response body (before formatting)")
    customer_name:   Optional[str] = Field(None, description="Customer display name for greeting")
    conversation_id: Optional[str] = Field(None, description="Conversation UUID for message threading")


# ─────────────────────────────────────────────────────────────
# TOOL 1 — search_knowledge_base
# ─────────────────────────────────────────────────────────────

@function_tool
async def search_knowledge_base(params: KnowledgeSearchInput) -> dict:
    """
    Search the TechCorp knowledge base for answers to customer questions.

    Uses pgvector cosine similarity when embeddings are available;
    falls back to PostgreSQL full-text search (tsvector) otherwise.

    Call this THIRD in the required workflow — after create_ticket and
    get_customer_history.

    Returns:
        {
          "results": [{"title", "content", "category", "score"}, ...],
          "search_mode": "vector" | "fulltext",
          "total_found": int
        }
    """
    db = db_context.get()
    top_k = max(1, min(params.top_k, 5))

    try:
        # ── Try vector search first ───────────────────────────
        from production.database.queries import vector_search_kb, fulltext_search_kb

        openai_client = openai_context.get(None)
        results = []
        search_mode = "fulltext"

        if openai_client:
            try:
                emb_response = await openai_client.embeddings.create(
                    model="text-embedding-3-small",
                    input=params.query,
                )
                query_vector = emb_response.data[0].embedding
                rows = await vector_search_kb(db, query_vector, top_k=top_k)
                if rows:
                    results = [
                        {
                            "title":    r["title"],
                            "content":  r["content"],
                            "category": r["category"],
                            "score":    float(r.get("similarity", 0.0)),
                        }
                        for r in rows
                    ]
                    search_mode = "vector"
            except Exception as vec_err:
                logger.warning("Vector search failed, falling back to FTS | error=%s", vec_err)

        if not results:
            rows = await fulltext_search_kb(db, params.query, top_k=top_k)
            results = [
                {
                    "title":    r["title"],
                    "content":  r["content"],
                    "category": r["category"],
                    "score":    float(r.get("rank", 0.0)),
                }
                for r in rows
            ]

        logger.info(
            "KB search | mode=%s | query=%r | results=%d",
            search_mode, params.query[:60], len(results),
        )
        return {"results": results, "search_mode": search_mode, "total_found": len(results)}

    except Exception as e:
        logger.error("search_knowledge_base failed | query=%r | error=%s", params.query, e)
        return {"results": [], "search_mode": "error", "total_found": 0, "error": str(e)}


# ─────────────────────────────────────────────────────────────
# TOOL 2 — create_ticket
# ─────────────────────────────────────────────────────────────

@function_tool
async def create_ticket(params: TicketInput) -> dict:
    """
    Create a support ticket in the database.

    MUST be called FIRST before any other tool in the workflow.
    Every customer message — no exceptions — requires a ticket.

    Returns:
        {
          "ticket_id":  str (UUID),
          "ticket_ref": str (TKT-XXXXXXXX),
          "status":     "open",
          "priority":   str,
          "created_at": ISO str
        }
    """
    db = db_context.get()

    try:
        from production.database.queries import (
            create_ticket as db_create_ticket,
            get_or_create_conversation,
        )

        customer_uuid = uuid.UUID(params.customer_id)
        conv_id = None

        # Resolve or create conversation
        conv_id_str = params.conversation_id
        if not conv_id_str:
            conv = await get_or_create_conversation(
                db,
                customer_id=customer_uuid,
                channel=params.channel,
            )
            conv_id = conv["id"] if conv else None
        else:
            conv_id = uuid.UUID(conv_id_str)

        ticket = await db_create_ticket(
            db,
            customer_id=customer_uuid,
            conversation_id=conv_id,
            source_channel=params.channel,
            priority=params.priority,
            category=params.category,
            issue_summary=params.issue_summary[:200],
            original_message=params.original_message,
        )

        logger.info(
            "Ticket created | ref=%s | customer=%s | channel=%s | priority=%s",
            ticket["ticket_ref"], params.customer_id, params.channel, params.priority,
        )

        return {
            "ticket_id":  str(ticket["id"]),
            "ticket_ref": ticket["ticket_ref"],
            "status":     "open",
            "priority":   ticket["priority"],
            "created_at": ticket["created_at"].isoformat(),
        }

    except Exception as e:
        logger.error(
            "create_ticket failed | customer=%s | channel=%s | error=%s",
            params.customer_id, params.channel, e,
        )
        return {
            "ticket_id":  "",
            "ticket_ref": "TKT-ERROR",
            "status":     "error",
            "priority":   params.priority,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "error":      str(e),
        }


# ─────────────────────────────────────────────────────────────
# TOOL 3 — get_customer_history
# ─────────────────────────────────────────────────────────────

@function_tool
async def get_customer_history(params: CustomerHistoryInput) -> dict:
    """
    Retrieve prior conversation history for a customer.

    Call this SECOND — after create_ticket, before search_knowledge_base.
    Never make customers repeat information they've already provided.

    Returns:
        {
          "customer_id":   str,
          "display_name":  str | None,
          "plan":          str | None,
          "conversations": [{...}],
          "total_tickets": int,
          "open_tickets":  int,
          "sentiment_avg": float | None
        }
    """
    db = db_context.get()

    try:
        from production.database.queries import (
            get_customer_by_id,
            get_customer_conversations,
            get_customer_ticket_counts,
        )

        customer_uuid = uuid.UUID(params.customer_id)
        customer = await get_customer_by_id(db, customer_uuid)

        if not customer:
            logger.warning("get_customer_history: customer not found | id=%s", params.customer_id)
            return {
                "customer_id":   params.customer_id,
                "display_name":  None,
                "plan":          None,
                "conversations": [],
                "total_tickets": 0,
                "open_tickets":  0,
                "sentiment_avg": None,
            }

        conversations = await get_customer_conversations(
            db, customer_uuid, limit=params.limit
        )

        ticket_counts = await get_customer_ticket_counts(db, customer_uuid)

        conv_list = []
        for c in conversations:
            conv_list.append({
                "conversation_id": str(c["id"]),
                "channel":         c["channel"],
                "status":          c["status"],
                "started_at":      c["created_at"].isoformat(),
                "last_message_at": c["updated_at"].isoformat(),
                "message_count":   c.get("message_count", 0),
            })

        logger.info(
            "Customer history retrieved | customer=%s | convs=%d | tickets=%d",
            params.customer_id, len(conv_list), ticket_counts.get("total", 0),
        )

        return {
            "customer_id":   params.customer_id,
            "display_name":  customer.get("display_name"),
            "plan":          customer.get("plan"),
            "conversations": conv_list,
            "total_tickets": ticket_counts.get("total", 0),
            "open_tickets":  ticket_counts.get("open", 0),
            "sentiment_avg": customer.get("sentiment_score"),
        }

    except Exception as e:
        logger.error(
            "get_customer_history failed | customer=%s | error=%s", params.customer_id, e
        )
        return {
            "customer_id":   params.customer_id,
            "display_name":  None,
            "plan":          None,
            "conversations": [],
            "total_tickets": 0,
            "open_tickets":  0,
            "sentiment_avg": None,
            "error":         str(e),
        }


# ─────────────────────────────────────────────────────────────
# TOOL 4 — escalate_to_human
# ─────────────────────────────────────────────────────────────

@function_tool
async def escalate_to_human(params: EscalationInput) -> dict:
    """
    Route the ticket to the appropriate human team.

    Call this FOURTH when ANY escalation trigger fires — before send_response.
    See the system prompt escalation table for trigger conditions.

    Returns:
        {
          "escalated":       bool,
          "escalation_id":   str (UUID),
          "team":            str,
          "routing_email":   str,
          "sla":             str,
          "ticket_ref":      str,
          "message_to_use":  str  ← use as basis for send_response()
        }
    """
    db = db_context.get()

    try:
        from production.database.queries import (
            create_escalation,
            get_ticket,
            update_ticket_status,
        )

        routing = ESCALATION_ROUTING.get(params.reason, DEFAULT_ESCALATION)
        ticket_uuid = uuid.UUID(params.ticket_id)
        ticket = await get_ticket(db, ticket_uuid)
        ticket_ref = ticket["ticket_ref"] if ticket else "TKT-UNKNOWN"

        # Update ticket status to escalated
        await update_ticket_status(db, ticket_uuid, "escalated")

        escalation = await create_escalation(
            db,
            ticket_id=ticket_uuid,
            customer_id=uuid.UUID(params.customer_id),
            reason=params.reason,
            urgency=params.urgency,
            routing_team=routing["team"],
            routing_email=routing["email"],
            notes=params.notes or "",
        )

        message_to_use = (
            f"I've connected you with our {routing['team']} team who are best placed "
            f"to help with this. Your ticket reference is {ticket_ref} and you can "
            f"expect a response {routing['sla']}. Thank you for your patience."
        )

        logger.info(
            "Escalation created | ticket=%s | reason=%s | team=%s | urgency=%s",
            ticket_ref, params.reason, routing["team"], params.urgency,
        )

        return {
            "escalated":      True,
            "escalation_id":  str(escalation["id"]),
            "team":           routing["team"],
            "routing_email":  routing["email"],
            "sla":            routing["sla"],
            "ticket_ref":     ticket_ref,
            "message_to_use": message_to_use,
        }

    except Exception as e:
        logger.error(
            "escalate_to_human failed | ticket=%s | reason=%s | error=%s",
            params.ticket_id, params.reason, e,
        )
        return {
            "escalated":      False,
            "escalation_id":  "",
            "team":           "Support",
            "routing_email":  DEFAULT_ESCALATION["email"],
            "sla":            DEFAULT_ESCALATION["sla"],
            "ticket_ref":     params.ticket_id,
            "message_to_use": (
                "I've flagged your request for our support team and they will be "
                "in touch shortly. Please quote your ticket reference when following up."
            ),
            "error": str(e),
        }


# ─────────────────────────────────────────────────────────────
# TOOL 5 — send_response
# ─────────────────────────────────────────────────────────────

@function_tool
async def send_response(params: ResponseInput) -> dict:
    """
    Format the response for the customer's channel and persist it as an
    outbound message in the database.

    MUST be called LAST — this is the final step in every interaction.
    Never reply to the customer without calling this tool.

    Returns:
        {
          "sent":             bool,
          "message_id":       str (UUID),
          "formatted_response": str,
          "channel":          str,
          "ticket_ref":       str
        }
    """
    db = db_context.get()

    try:
        from production.agent.formatters import format_for_channel
        from production.database.queries import (
            insert_message,
            get_ticket,
            get_or_create_conversation,
        )

        # Format for channel
        formatted = format_for_channel(
            response=params.response_text,
            channel=params.channel,
            customer_name=params.customer_name,
        )

        ticket_uuid = uuid.UUID(params.ticket_id)
        ticket = await get_ticket(db, ticket_uuid)
        ticket_ref = ticket["ticket_ref"] if ticket else "TKT-UNKNOWN"

        # Resolve conversation_id
        conv_id = None
        if params.conversation_id:
            conv_id = uuid.UUID(params.conversation_id)
        elif ticket and ticket.get("conversation_id"):
            conv_id = ticket["conversation_id"]
        else:
            conv = await get_or_create_conversation(
                db,
                customer_id=uuid.UUID(params.customer_id),
                channel=params.channel,
            )
            conv_id = conv["id"] if conv else None

        # Persist outbound message
        message = await insert_message(
            db,
            conversation_id=conv_id,
            direction="outbound",
            channel=params.channel,
            raw_content=params.response_text,
            formatted_content=formatted,
            delivery_status="delivered",
        )

        logger.info(
            "Response sent | ticket=%s | channel=%s | customer=%s | chars=%d",
            ticket_ref, params.channel, params.customer_id, len(formatted),
        )

        return {
            "sent":               True,
            "message_id":         str(message["id"]),
            "formatted_response": formatted,
            "channel":            params.channel,
            "ticket_ref":         ticket_ref,
        }

    except Exception as e:
        logger.error(
            "send_response failed | ticket=%s | channel=%s | error=%s",
            params.ticket_id, params.channel, e,
        )
        return {
            "sent":               False,
            "message_id":         "",
            "formatted_response": params.response_text,
            "channel":            params.channel,
            "ticket_ref":         params.ticket_id,
            "error":              str(e),
        }
