"""
production/channels/web_form_handler.py
Web Form channel handler — FastAPI router with Pydantic validation.

Endpoints (mounted at /support in main.py):
  POST /support/submit             — validate → create ticket → publish to Kafka → return ticket_id
  GET  /support/ticket/{ticket_id} — return ticket status + message history

Normalized output (every inbound submission):
  {
    "channel": "web_form",
    "channel_message_id": str,      # submission UUID
    "customer_email": str,
    "customer_name": str,
    "subject": str,
    "content": str,
    "received_at": ISO str,
    "metadata": {
      "category": str,
      "priority": str,
      "form_version": str
    }
  }
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field, field_validator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/support", tags=["web-form"])

# Valid category values — must match ticket_category enum in DB
VALID_CATEGORIES = [
    "general", "technical", "billing", "account",
    "integration", "feature_request", "onboarding",
    "authentication", "legal",
]
VALID_PRIORITIES = ["low", "medium", "high"]


# ─────────────────────────────────────────────────────────────
# PYDANTIC MODELS
# ─────────────────────────────────────────────────────────────

class SupportFormSubmission(BaseModel):
    """
    Validated input model for web form submissions.
    All validation errors return 422 with field-level detail.
    """

    name: str = Field(..., description="Customer's full name")
    email: EmailStr = Field(..., description="Customer's email address")
    subject: str = Field(..., description="Brief description of the issue")
    category: str = Field(..., description="Issue category")
    priority: str = Field("medium", description="Issue urgency level")
    message: str = Field(..., description="Full message body")

    @field_validator("name")
    @classmethod
    def name_min_length(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2:
            raise ValueError("Name must be at least 2 characters")
        return v

    @field_validator("subject")
    @classmethod
    def subject_min_length(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 5:
            raise ValueError("Subject must be at least 5 characters")
        return v

    @field_validator("message")
    @classmethod
    def message_min_length(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 10:
            raise ValueError("Message must be at least 10 characters")
        return v

    @field_validator("category")
    @classmethod
    def category_valid(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in VALID_CATEGORIES:
            raise ValueError(f"Category must be one of: {', '.join(VALID_CATEGORIES)}")
        return v

    @field_validator("priority")
    @classmethod
    def priority_valid(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in VALID_PRIORITIES:
            raise ValueError(f"Priority must be one of: {', '.join(VALID_PRIORITIES)}")
        return v


class SubmissionResponse(BaseModel):
    ticket_id:    str
    ticket_ref:   str
    status:       str
    message:      str
    submitted_at: str
    estimated_response: str


class TicketStatusResponse(BaseModel):
    ticket_id:   str
    ticket_ref:  str
    status:      str
    priority:    str
    category:    str
    subject:     str
    created_at:  str
    updated_at:  str
    resolved_at: Optional[str] = None
    messages:    list[dict] = []


# ─────────────────────────────────────────────────────────────
# KAFKA PUBLISHER (injected dependency)
# ─────────────────────────────────────────────────────────────

async def get_kafka_producer():
    """Dependency: yields the Kafka producer from app state."""
    from fastapi import Request
    # Imported lazily to avoid circular import — producer set in main.py lifespan
    from production.api.main import app as fastapi_app
    return fastapi_app.state.kafka_producer


# ─────────────────────────────────────────────────────────────
# DB CONNECTION (injected dependency)
# ─────────────────────────────────────────────────────────────

async def get_db():
    """Dependency: yields an asyncpg connection from the pool."""
    from production.api.main import app as fastapi_app
    async with fastapi_app.state.db_pool.acquire() as conn:
        yield conn


# ─────────────────────────────────────────────────────────────
# HELPER — normalise submission
# ─────────────────────────────────────────────────────────────

def normalise_submission(submission: SupportFormSubmission, submission_id: str) -> dict:
    """
    Convert a validated Pydantic submission into the normalised inbound
    message format used by the Kafka message processor.
    """
    return {
        "channel":            "web_form",
        "channel_message_id": submission_id,
        "customer_email":     str(submission.email).lower(),
        "customer_name":      submission.name,
        "subject":            submission.subject,
        "content":            submission.message,
        "received_at":        datetime.now(timezone.utc).isoformat(),
        "metadata": {
            "category":     submission.category,
            "priority":     submission.priority,
            "form_version": "1.0",
        },
    }


def map_priority_to_ticket(category: str, priority: str) -> str:
    """
    Escalate ticket priority when high priority + sensitive category.
    Follows web_form_handler rules from transition-checklist.md:
      - billing + high  → critical
      - bug_report + high → critical
    """
    if priority == "high" and category in ("billing", "bug_report"):
        return "critical"
    return priority


# ─────────────────────────────────────────────────────────────
# DIRECT AGENT PROCESSING (no Kafka)
# ─────────────────────────────────────────────────────────────

async def _process_directly(
    customer_id: str,
    customer_name: str,
    customer_email: str,
    content: str,
    subject: str,
    ticket_id: str,
) -> None:
    """
    Runs the AI agent directly (no Kafka) for cloud deployments.
    Replicates the essential steps of message_processor._process_message.
    """
    from agents import Runner
    from production.agent import customer_success_agent, db_context, openai_context
    from production.api.main import app as fastapi_app
    from production.database.queries import (
        get_or_create_conversation,
        get_recent_messages,
        get_customer_conversations,
        insert_message,
        update_ticket_conversation,
        store_outbound_message,
    )
    from production.workers.message_processor import (
        estimate_sentiment,
        build_context_block,
        extract_topics,
    )

    _process_start = datetime.now(timezone.utc)
    channel = "web_form"

    try:
        db_pool = fastapi_app.state.db_pool
        if not db_pool:
            logger.error("Direct processing failed — no DB pool | ticket=%s", ticket_id)
            return

        async with db_pool.acquire() as db:
            # ── 1. Get or create conversation ──────────────────
            cust_uuid = uuid.UUID(customer_id)
            conversation = await get_or_create_conversation(
                db, customer_id=cust_uuid, channel=channel,
            )
            conv_id = conversation["id"]

            # ── 2. Store inbound message ───────────────────────
            await insert_message(
                db,
                conversation_id=conv_id,
                direction="inbound",
                channel=channel,
                raw_content=content,
                formatted_content=content,
                delivery_status="delivered",
            )

            # ── 3. Link ticket to conversation ─────────────────
            try:
                await update_ticket_conversation(db, uuid.UUID(ticket_id), conv_id)
            except Exception:
                pass

            # ── 4. Build context ───────────────────────────────
            prior_msgs = await get_recent_messages(db, conv_id, limit=10)
            history_turns = [
                {
                    "role":      m["direction"],
                    "channel":   m["channel"],
                    "content":   m["raw_content"],
                    "timestamp": m["received_at"].strftime("%Y-%m-%d %H:%M"),
                }
                for m in prior_msgs
            ]

            prior_convs = await get_customer_conversations(db, cust_uuid, limit=5)
            sentiment_score = estimate_sentiment(content)
            current_topics = extract_topics(content)
            channel_journey = [c["channel"] for c in prior_convs] + [channel]
            channel_journey = list(dict.fromkeys(channel_journey))

            context_block = build_context_block(
                channel=channel,
                customer_id=customer_id,
                customer_name=customer_name,
                canonical_id=customer_id,
                sentiment=sentiment_score,
                sentiment_history=[sentiment_score],
                session_number=len(prior_convs) + 1,
                session_id=str(conv_id),
                channel_journey=channel_journey,
                topics_discussed=[],
                top_topics={},
                current_topics=current_topics,
                conversation_history=history_turns,
                subject=subject,
                new_message=content,
            )

        # ── 5. Inject context vars & run agent ─────────────
        db_context.set(db_pool)

        from openai import AsyncOpenAI
        openai_client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
        openai_context.set(openai_client)

        logger.info("Running agent directly | ticket=%s | sentiment=%.2f", ticket_id, sentiment_score)

        result = await Runner.run(customer_success_agent, input=context_block)
        agent_output = result.final_output if hasattr(result, "final_output") else str(result)

        # ── 6. Safety net — ensure response stored ─────────
        if agent_output:
            async with db_pool.acquire() as db:
                tid = uuid.UUID(ticket_id)
                existing = await db.fetchval(
                    "SELECT COUNT(*) FROM messages WHERE conversation_id = $1 AND direction = 'outbound'",
                    conv_id,
                )
                if existing == 0:
                    logger.info("Safety net: storing agent reply | ticket=%s", ticket_id)
                    _latency_ms = int((datetime.now(timezone.utc) - _process_start).total_seconds() * 1000)
                    await store_outbound_message(
                        db,
                        conversation_id=conv_id,
                        ticket_id=tid,
                        customer_id=uuid.UUID(customer_id),
                        channel=channel,
                        raw_content=agent_output,
                        formatted_content=agent_output,
                        model_used="gpt-4o",
                        latency_ms=_latency_ms,
                        processing_started_at=_process_start,
                    )
                    await db.execute(
                        "UPDATE messages SET delivery_status = 'delivered', delivered_at = NOW() "
                        "WHERE ticket_id = $1 AND direction = 'outbound' AND delivery_status = 'pending'",
                        tid,
                    )

        _latency = int((datetime.now(timezone.utc) - _process_start).total_seconds() * 1000)
        logger.info("Direct processing complete | ticket=%s | latency=%dms", ticket_id, _latency)

    except Exception as e:
        logger.error("Direct processing failed | ticket=%s | error=%s", ticket_id, e, exc_info=True)


# ─────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────

@router.post(
    "/submit",
    response_model=SubmissionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a support request",
    description=(
        "Accepts a web form support submission, validates all fields, "
        "creates a ticket in the database, and publishes the message to Kafka "
        "for processing by the agent worker. Returns a ticket ID for status tracking."
    ),
)
async def submit_support_form(
    submission: SupportFormSubmission,
    db=Depends(get_db),
    kafka=Depends(get_kafka_producer),
) -> SubmissionResponse:
    """
    POST /support/submit
    Validate → normalise → create ticket → publish to Kafka → return ticket_ref.
    """
    submission_id = str(uuid.uuid4())
    now           = datetime.now(timezone.utc)
    ticket_priority = map_priority_to_ticket(submission.category, submission.priority)

    try:
        # ── 1. Resolve or create customer ────────────────────
        from production.database.queries import (
            get_customer_by_email,
            create_customer,
            register_identifier,
        )

        customer = await get_customer_by_email(db, str(submission.email))
        if not customer:
            customer = await create_customer(
                db,
                canonical_email=str(submission.email),
                display_name=submission.name,
                first_channel="web_form",
            )
            logger.info("New customer created | email=%s", submission.email)
        else:
            logger.info("Returning customer | email=%s", submission.email)

        # Ensure identifier registered
        await register_identifier(
            db,
            customer_id=customer["id"],
            identifier_type="email",
            identifier_value=str(submission.email),
            channel="web_form",
            is_primary=True,
        )

        # ── 2. Create ticket ──────────────────────────────────
        from production.database.queries import create_ticket

        ticket = await create_ticket(
            db,
            customer_id=customer["id"],
            conversation_id=None,       # conversation created by message_processor
            source_channel="web_form",
            priority=ticket_priority,
            category=submission.category,
            issue_summary=f"{submission.subject[:200]}",
            original_message=submission.message,
        )
        ticket_id  = str(ticket["id"])
        ticket_ref = ticket["ticket_ref"]

        # ── 3. Publish to Kafka ───────────────────────────────
        normalised_msg = normalise_submission(submission, submission_id)
        normalised_msg["ticket_id"] = ticket_id

        kafka_payload = json.dumps({
            **normalised_msg,
            "customer_id": str(customer["id"]),
        }).encode("utf-8")

        if kafka:
            await kafka.send_and_wait(
                "fte.channels.webform.inbound",
                value=kafka_payload,
                key=str(customer["id"]).encode("utf-8"),
            )
        else:
            # Direct agent processing — no Kafka needed
            logger.info("Kafka unavailable — processing directly for ticket=%s", ticket_ref)
            import asyncio
            asyncio.create_task(
                _process_directly(
                    customer_id=str(customer["id"]),
                    customer_name=submission.name,
                    customer_email=str(submission.email),
                    content=submission.message,
                    subject=submission.subject,
                    ticket_id=ticket_id,
                )
            )

        logger.info(
            "Web form submitted | ticket=%s | customer=%s | priority=%s | category=%s",
            ticket_ref, submission.email, ticket_priority, submission.category,
        )

        # Estimated response time based on priority
        eta_map = {
            "critical": "within 15 minutes",
            "high":     "within 1 hour",
            "medium":   "within a few hours",
            "low":      "within 24 hours",
        }

        return SubmissionResponse(
            ticket_id=ticket_id,
            ticket_ref=ticket_ref,
            status="open",
            message=(
                f"Your support request has been received. "
                f"Our team will respond {eta_map.get(ticket_priority, 'soon')}."
            ),
            submitted_at=now.isoformat(),
            estimated_response=eta_map.get(ticket_priority, "within 24 hours"),
        )

    except Exception as e:
        logger.error("Web form submission failed | email=%s | error=%s", submission.email, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit support request. Please try again.",
        )


@router.get(
    "/ticket/{ticket_id}",
    response_model=TicketStatusResponse,
    summary="Get ticket status",
    description="Return the current status and message history for a support ticket.",
)
async def get_ticket_status(ticket_id: str, db=Depends(get_db)) -> TicketStatusResponse:
    """
    GET /support/ticket/{ticket_id}
    Return ticket status + message history for display in the web form success screen.
    """
    try:
        ticket_uuid = uuid.UUID(ticket_id)
    except ValueError:
        # Also try looking up by ticket_ref (TKT-XXXXXXXX)
        ticket_uuid = None

    from production.database.queries import get_ticket, get_ticket_by_ref, get_recent_messages

    if ticket_uuid:
        ticket = await get_ticket(db, ticket_uuid)
    else:
        ticket = await get_ticket_by_ref(db, ticket_id)

    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ticket {ticket_id} not found",
        )

    # Fetch last 10 outbound messages (what the customer received)
    messages = []
    if ticket.get("conversation_id"):
        raw_msgs = await get_recent_messages(db, ticket["conversation_id"], limit=10)
        messages = [
            {
                "direction":  r["direction"],
                "channel":    r["channel"],
                "content":    r["formatted_content"] or r["raw_content"],
                "received_at": r["received_at"].isoformat(),
            }
            for r in raw_msgs
            if r["direction"] == "outbound"
        ]

    return TicketStatusResponse(
        ticket_id=str(ticket["id"]),
        ticket_ref=ticket["ticket_ref"],
        status=ticket["status"],
        priority=ticket["priority"],
        category=ticket["category"],
        subject=ticket["issue_summary"],
        created_at=ticket["created_at"].isoformat(),
        updated_at=ticket["updated_at"].isoformat(),
        resolved_at=ticket["resolved_at"].isoformat() if ticket.get("resolved_at") else None,
        messages=messages,
    )
