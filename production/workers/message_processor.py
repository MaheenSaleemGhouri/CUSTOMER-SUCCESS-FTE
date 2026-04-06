"""
production/workers/message_processor.py
Unified Kafka message processor — Customer Success FTE.

Consumes from all three inbound channel topics, runs the 9-step pipeline
for each message, and publishes the formatted response to the correct
outbound topic (or calls the channel handler directly for email/WhatsApp).

Topics consumed:
  fte.channels.email.inbound
  fte.channels.whatsapp.inbound
  fte.channels.webform.inbound

Topics produced:
  fte.channels.email.outbound
  fte.channels.whatsapp.outbound
  fte.channels.webform.outbound
  fte.tickets.incoming          (new ticket events)
  fte.escalations               (escalation events)
  fte.metrics                   (per-message metric updates)
  fte.dlq                       (dead-letter queue for failed messages)

9-Step Processing Pipeline (per message):
  1. Deserialize & validate inbound payload
  2. Resolve customer identity (cross-channel lookup / create)
  3. Get or create conversation record
  4. Compute sentiment (score, trend, anger-spike detection)
  5. Build agent context block (structured prompt variables)
  6. Inject db_context and openai_context for tool access
  7. Run agent  (Runner.run → customer_success_agent)
  8. Route outbound response to channel
  9. Update metrics + emit metric event

Error handling:
  - Any step failure → publish to fte.dlq with error detail
  - Transient errors (DB timeout) → retry up to MAX_RETRIES with backoff
  - Poison-pill messages → skip after MAX_RETRIES, send to DLQ

Usage:
    python -m production.workers.message_processor

Environment variables (all required unless noted):
  KAFKA_BOOTSTRAP_SERVERS   e.g. localhost:9092
  KAFKA_GROUP_ID            e.g. fte-message-processor
  DATABASE_URL              asyncpg DSN
  OPENAI_API_KEY            OpenAI API key
  TWILIO_ACCOUNT_SID        Twilio SID (optional — WhatsApp send)
  TWILIO_AUTH_TOKEN         Twilio auth token (optional)
  TWILIO_WHATSAPP_FROM      Twilio WhatsApp sender number (optional)
  GMAIL_CREDENTIALS_FILE    Path to Gmail service-account JSON (optional)
"""

import asyncio
import json
import logging
import os
import signal
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
load_dotenv(Path(__file__).parents[2] / ".env")

import asyncpg
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from openai import AsyncOpenAI

from agents import Runner

from production.agent import customer_success_agent, db_context, openai_context

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────

KAFKA_BOOTSTRAP    = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_GROUP_ID     = os.getenv("KAFKA_GROUP_ID", "fte-message-processor")
DATABASE_URL       = os.getenv("DATABASE_URL", "postgresql://localhost/techcorp")
OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY", "")

MAX_RETRIES        = 3
RETRY_BACKOFF_S    = 2.0   # seconds, doubled each retry
MAX_CONCURRENT     = 10    # asyncio semaphore limit

INBOUND_TOPICS = [
    "fte.channels.email.inbound",
    "fte.channels.whatsapp.inbound",
    "fte.channels.webform.inbound",
]

OUTBOUND_TOPIC_MAP = {
    "email":     "fte.channels.email.outbound",
    "whatsapp":  "fte.channels.whatsapp.outbound",
    "web_form":  "fte.channels.webform.outbound",
}

# ─────────────────────────────────────────────────────────────
# SENTIMENT HELPERS
# ─────────────────────────────────────────────────────────────

ANGRY_WORDS = {
    "angry", "furious", "terrible", "awful", "horrible", "useless", "broken",
    "disgusting", "outraged", "unacceptable", "stupid", "idiot", "trash",
    "scam", "fraud", "worst", "hate", "pathetic", "rubbish", "joke",
    "sue", "lawyer", "attorney", "refund", "cancel", "quit",
}
POSITIVE_WORDS = {
    "great", "excellent", "perfect", "amazing", "wonderful", "fantastic",
    "happy", "satisfied", "love", "brilliant", "superb", "thank", "thanks",
    "appreciate", "helpful", "resolved", "fixed", "working",
}


def estimate_sentiment(text: str) -> float:
    """Simple word-count heuristic — returns float 0.0 (angry) → 1.0 (positive)."""
    tokens = set(text.lower().split())
    angry_score    = len(tokens & ANGRY_WORDS)
    positive_score = len(tokens & POSITIVE_WORDS)
    total = angry_score + positive_score
    if total == 0:
        return 0.5
    return round(positive_score / total, 2)


def sentiment_label(score: float) -> str:
    if score < 0.2:  return "VERY ANGRY"
    if score < 0.4:  return "Frustrated"
    if score < 0.6:  return "Neutral"
    if score < 0.8:  return "Positive"
    return "Very Positive"


def get_trend(history: list[float]) -> str:
    if len(history) < 2:
        return "unknown"
    delta = history[-1] - history[-2]
    if delta > 0.15:   return "improving"
    if delta < -0.15:  return "worsening"
    return "stable"


def detect_anger_spike(history: list[float]) -> bool:
    if len(history) < 2:
        return False
    return (history[-2] >= 0.4) and (history[-1] < 0.2)


# ─────────────────────────────────────────────────────────────
# CONTEXT BLOCK BUILDER  (Step 5)
# ─────────────────────────────────────────────────────────────

def build_context_block(
    *,
    channel: str,
    customer_id: str,
    customer_name: Optional[str],
    canonical_id: str,
    sentiment: float,
    sentiment_history: list[float],
    session_number: int,
    session_id: str,
    channel_journey: list[str],
    topics_discussed: list[str],
    top_topics: dict[str, int],
    current_topics: list[str],
    conversation_history: list[dict],
    subject: Optional[str],
    new_message: str,
) -> str:
    """
    Build the structured context block injected as the agent's user input.
    Variable names match the prompts.py ## Context Variables Available section.
    """
    trend           = get_trend(sentiment_history)
    anger_spike     = detect_anger_spike(sentiment_history)
    label           = sentiment_label(sentiment)

    top_topics_str  = ", ".join(f"{k}({v})" for k, v in top_topics.items()) or "none"
    cur_topics_str  = ", ".join(current_topics) or "none"
    journey_str     = " → ".join(channel_journey) or channel

    history_lines = []
    for turn in conversation_history[-10:]:    # last 10 turns
        ts  = turn.get("timestamp", "")
        rol = turn.get("role", "unknown")
        ch  = turn.get("channel", channel)
        msg = turn.get("content", "")[:300]
        history_lines.append(f"  [{ts}] {rol.upper()} ({ch}): {msg}")
    history_str = "\n".join(history_lines) or "  (no prior history)"

    subject_line = f"[SUBJECT]: {subject}" if subject else ""

    return f"""[CHANNEL]: {channel}
[CUSTOMER_ID]: {customer_id}
[CUSTOMER_NAME]: {customer_name or 'Unknown'}
[CANONICAL_ID]: {canonical_id}
[SENTIMENT]: {sentiment:.2f} — {label}
[SENTIMENT_TREND]: {trend}
[ANGER_SPIKE_DETECTED]: {anger_spike}
[SESSION]: session {session_number} | id={session_id}
[CHANNEL_JOURNEY]: {journey_str}
[TOPICS_DISCUSSED]: {', '.join(topics_discussed) or 'none'}
[TOP_TOPICS_BY_FREQUENCY]: {top_topics_str}
[CURRENT_MESSAGE_TOPICS]: {cur_topics_str}
[CONVERSATION HISTORY]:
{history_str}
{subject_line}
[NEW MESSAGE]: {new_message}"""


# ─────────────────────────────────────────────────────────────
# TOPIC EXTRACTOR  (lightweight, no LLM cost)
# ─────────────────────────────────────────────────────────────

TOPIC_KEYWORDS: dict[str, list[str]] = {
    "integration":       ["integration", "slack", "zapier", "api", "connect", "webhook"],
    "billing":           ["billing", "invoice", "charge", "payment", "refund", "credit"],
    "login":             ["login", "sign in", "password", "2fa", "mfa", "access", "locked"],
    "performance":       ["slow", "lag", "freeze", "crash", "performance", "loading"],
    "export":            ["export", "pdf", "csv", "download", "gantt"],
    "notification":      ["notification", "email alert", "reminder", "bell"],
    "permissions":       ["permission", "role", "admin", "member", "owner"],
    "mobile":            ["mobile", "app", "ios", "android", "phone"],
    "data_loss":         ["lost", "missing", "deleted", "disappeared", "gone"],
    "feature_request":   ["feature", "request", "suggest", "would be nice", "add"],
    "onboarding":        ["setup", "onboard", "getting started", "tutorial", "new"],
    "upgrade":           ["upgrade", "plan", "tier", "enterprise", "business"],
    "cancellation":      ["cancel", "cancellation", "close account", "terminate"],
    "legal":             ["legal", "gdpr", "compliance", "data breach", "lawsuit", "sue"],
}


def extract_topics(text: str) -> list[str]:
    text_lower = text.lower()
    return [topic for topic, kws in TOPIC_KEYWORDS.items() if any(k in text_lower for k in kws)]


# ─────────────────────────────────────────────────────────────
# UNIFIED MESSAGE PROCESSOR
# ─────────────────────────────────────────────────────────────

class UnifiedMessageProcessor:
    """
    Kafka consumer → 9-step pipeline → Kafka producer.

    Lifecycle:
      start()  — connect to Kafka + DB + OpenAI, start consumer loop
      stop()   — graceful shutdown (drain in-flight messages)
    """

    def __init__(self) -> None:
        self.consumer:    Optional[AIOKafkaConsumer] = None
        self.producer:    Optional[AIOKafkaProducer] = None
        self.db_pool:     Optional[asyncpg.Pool]     = None
        self.openai:      Optional[AsyncOpenAI]      = None
        self._semaphore   = asyncio.Semaphore(MAX_CONCURRENT)
        self._running     = False
        self._tasks:      set[asyncio.Task]          = set()

    # ── Lifecycle ─────────────────────────────────────────────

    async def start(self) -> None:
        logger.info("UnifiedMessageProcessor starting …")

        self.db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
        self.openai  = AsyncOpenAI(api_key=OPENAI_API_KEY)

        self.consumer = AIOKafkaConsumer(
            *INBOUND_TOPICS,
            bootstrap_servers=KAFKA_BOOTSTRAP,
            group_id=KAFKA_GROUP_ID,
            value_deserializer=lambda b: json.loads(b.decode("utf-8")),
            enable_auto_commit=False,
            auto_offset_reset="earliest",
        )
        self.producer = AIOKafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        )

        await self.consumer.start()
        await self.producer.start()
        self._running = True
        logger.info("UnifiedMessageProcessor ready | topics=%s", INBOUND_TOPICS)

        try:
            await self._consume_loop()
        finally:
            await self.stop()

    async def stop(self) -> None:
        self._running = False
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self.consumer:
            await self.consumer.stop()
        if self.producer:
            await self.producer.stop()
        if self.db_pool:
            await self.db_pool.close()
        logger.info("UnifiedMessageProcessor stopped.")

    # ── Consumer loop ─────────────────────────────────────────

    async def _consume_loop(self) -> None:
        async for msg in self.consumer:
            if not self._running:
                break
            task = asyncio.create_task(self._handle_with_semaphore(msg))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _handle_with_semaphore(self, msg: Any) -> None:
        async with self._semaphore:
            await self._handle_message_with_retry(msg)
        await self.consumer.commit()

    # ── Retry wrapper ─────────────────────────────────────────

    async def _handle_message_with_retry(self, msg: Any) -> None:
        backoff = RETRY_BACKOFF_S
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                await self._process_message(msg.value, msg.topic)
                return
            except Exception as e:
                logger.warning(
                    "Processing failed (attempt %d/%d) | topic=%s | error=%s",
                    attempt, MAX_RETRIES, msg.topic, e,
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(backoff)
                    backoff *= 2
                else:
                    await self._send_to_dlq(msg.value, msg.topic, str(e))

    # ── 9-STEP PIPELINE ───────────────────────────────────────

    async def _process_message(self, payload: dict, topic: str) -> None:
        proc_id = str(uuid.uuid4())[:8]
        _process_start = datetime.now(timezone.utc)
        logger.info("Processing | id=%s | topic=%s", proc_id, topic)

        # ── STEP 1: Deserialize & validate ───────────────────
        channel         = payload.get("channel", "")
        customer_email  = payload.get("customer_email", "")
        customer_phone  = payload.get("customer_phone", "")
        customer_name   = payload.get("customer_name", "")
        content         = payload.get("content", "")
        subject         = payload.get("subject")
        ticket_id       = payload.get("ticket_id")     # pre-created by web_form_handler
        metadata        = payload.get("metadata", {})

        if not channel or not content:
            logger.error("Invalid payload: missing channel or content | id=%s", proc_id)
            return

        identifier = customer_email or customer_phone
        if not identifier:
            logger.error("No customer identifier | id=%s", proc_id)
            return

        # ── STEP 2: Resolve customer identity ────────────────
        async with self.db_pool.acquire() as db:
            from production.database.queries import (
                get_customer_by_identifier,
                create_customer,
                register_identifier,
                get_customer_by_id,
            )

            id_type  = "email" if customer_email else "phone"
            customer = await get_customer_by_identifier(db, id_type, identifier)

            if not customer:
                customer = await create_customer(
                    db,
                    canonical_email=customer_email or None,
                    display_name=customer_name or identifier,
                    first_channel=channel,
                )
                logger.info("New customer | id=%s", customer["id"])

            customer_id  = str(customer["id"])
            canonical_id = customer_id

            # Register channel identifier if new
            await register_identifier(
                db,
                customer_id=customer["id"],
                identifier_type=id_type,
                identifier_value=identifier,
                channel=channel,
                is_primary=(id_type == "email"),
            )

            # ── STEP 3: Get or create conversation ───────────
            from production.database.queries import (
                get_or_create_conversation,
                get_recent_messages,
                get_customer_conversations,
                insert_message,
            )

            conversation = await get_or_create_conversation(
                db,
                customer_id=customer["id"],
                channel=channel,
            )
            conv_id = conversation["id"]

            # Store inbound message
            await insert_message(
                db,
                conversation_id=conv_id,
                direction="inbound",
                channel=channel,
                raw_content=content,
                formatted_content=content,
                delivery_status="delivered",
            )

            # Fetch prior history for context
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

            # Prior conversations for topic tracking
            prior_convs = await get_customer_conversations(db, customer["id"], limit=5)

            # ── STEP 4: Compute sentiment ─────────────────────
            sentiment_score = estimate_sentiment(content)

            # Build running history from prior outbound messages
            sentiment_history: list[float] = []
            for msg in prior_msgs:
                if msg["direction"] == "inbound":
                    sentiment_history.append(estimate_sentiment(msg["raw_content"] or ""))
            sentiment_history.append(sentiment_score)

            # ── STEP 5: Build context block ───────────────────
            current_topics = extract_topics(content)
            topics_discussed: list[str] = []
            top_topics: dict[str, int] = {}
            for conv in prior_convs:
                # Reconstruct topics from prior messages (lightweight)
                pass  # DB-stored topic tracking is seeded here in production

            session_id     = str(conv_id)
            session_number = len(prior_convs) + 1
            channel_journey = [c["channel"] for c in prior_convs] + [channel]
            channel_journey = list(dict.fromkeys(channel_journey))  # dedupe, preserve order

            context_block = build_context_block(
                channel=channel,
                customer_id=customer_id,
                customer_name=customer.get("display_name") or customer_name or None,
                canonical_id=canonical_id,
                sentiment=sentiment_score,
                sentiment_history=sentiment_history,
                session_number=session_number,
                session_id=session_id,
                channel_journey=channel_journey,
                topics_discussed=topics_discussed,
                top_topics=top_topics,
                current_topics=current_topics,
                conversation_history=history_turns,
                subject=subject,
                new_message=content,
            )

            # ── STEP 6: Inject context vars ───────────────────
            db_context.set(self.db_pool)
            openai_context.set(self.openai)

            # Provide conversation_id hint to tools via payload augmentation
            # (tools resolve conv_id from ticket; this is belt-and-suspenders)
            if ticket_id:
                # Attach conv_id to ticket row so tools can resolve it
                from production.database.queries import update_ticket_conversation
                try:
                    await update_ticket_conversation(
                        db, uuid.UUID(ticket_id), conv_id
                    )
                except Exception:
                    pass  # non-critical

        # ── STEP 7: Run agent ─────────────────────────────────
        logger.info(
            "Running agent | customer=%s | channel=%s | sentiment=%.2f",
            customer_id, channel, sentiment_score,
        )

        result = await Runner.run(
            customer_success_agent,
            input=context_block,
        )

        agent_output = result.final_output if hasattr(result, "final_output") else str(result)

        # ── STEP 7b: Safety net — ensure response stored in DB ──
        # If agent didn't call send_response tool, store the reply manually
        if agent_output and ticket_id:
            try:
                async with self.db_pool.acquire() as db:
                    from production.database.queries import store_outbound_message
                    # Check if an outbound message was already stored for this ticket
                    existing = await db.fetchval(
                        "SELECT COUNT(*) FROM messages WHERE ticket_id = $1 AND direction = 'outbound'",
                        uuid.UUID(ticket_id) if isinstance(ticket_id, str) else ticket_id,
                    )
                    if existing == 0:
                        logger.info("Safety net: storing agent reply for ticket=%s", ticket_id)
                        tid = uuid.UUID(ticket_id) if isinstance(ticket_id, str) else ticket_id
                        await store_outbound_message(
                            db,
                            conversation_id=conv_id,
                            ticket_id=tid,
                            customer_id=uuid.UUID(customer_id) if isinstance(customer_id, str) else customer_id,
                            channel=channel,
                            raw_content=agent_output,
                            formatted_content=agent_output,
                            model_used="gpt-4o",
                            latency_ms=int((datetime.now(timezone.utc) - _process_start).total_seconds() * 1000),
                            processing_started_at=_process_start,
                        )
                        # Mark as delivered
                        await db.execute(
                            "UPDATE messages SET delivery_status = 'delivered', delivered_at = NOW() WHERE ticket_id = $1 AND direction = 'outbound' AND delivery_status = 'pending'",
                            tid,
                        )
            except Exception as e:
                logger.warning("Safety net store failed | error=%s", e)

        # ── STEP 8: Route outbound response ──────────────────
        outbound_topic = OUTBOUND_TOPIC_MAP.get(channel)
        if outbound_topic:
            await self.producer.send_and_wait(
                outbound_topic,
                value={
                    "customer_id":    customer_id,
                    "channel":        channel,
                    "customer_email": customer_email,
                    "customer_phone": customer_phone,
                    "customer_name":  customer_name,
                    "response":       agent_output,
                    "ticket_id":      ticket_id,
                    "conversation_id": str(conv_id),
                    "processed_at":   datetime.now(timezone.utc).isoformat(),
                },
                key=customer_id.encode("utf-8"),
            )
            logger.info("Outbound published | topic=%s | customer=%s", outbound_topic, customer_id)

        # ── STEP 9: Update metrics ────────────────────────────
        _process_end = datetime.now(timezone.utc)
        _latency_ms = int((_process_end - _process_start).total_seconds() * 1000)
        logger.info("Latency | id=%s | latency_ms=%d", proc_id, _latency_ms)

        await self._emit_metric(
            customer_id=customer_id,
            channel=channel,
            sentiment=sentiment_score,
            escalated=("escalate" in agent_output.lower()),
            ticket_id=ticket_id,
            latency_ms=_latency_ms,
        )

    # ── METRIC EMISSION ───────────────────────────────────────

    async def _emit_metric(
        self,
        customer_id: str,
        channel: str,
        sentiment: float,
        escalated: bool,
        ticket_id: Optional[str],
        latency_ms: int = 0,
    ) -> None:
        try:
            async with self.db_pool.acquire() as db:
                from production.database.queries import upsert_daily_metric_simple, upsert_latency_metric

                today = datetime.now(timezone.utc).date()
                await upsert_daily_metric_simple(db, date=today, channel=channel, metric="messages_processed", increment=1)
                if escalated:
                    await upsert_daily_metric_simple(db, date=today, channel=channel, metric="escalations", increment=1)
                if sentiment < 0.3:
                    await upsert_daily_metric_simple(db, date=today, channel=channel, metric="low_sentiment", increment=1)
                if latency_ms > 0:
                    await upsert_latency_metric(db, date=today, channel=channel, latency_ms=latency_ms)

            await self.producer.send_and_wait(
                "fte.metrics",
                value={
                    "customer_id": customer_id,
                    "channel":     channel,
                    "sentiment":   sentiment,
                    "escalated":   escalated,
                    "ticket_id":   ticket_id,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception as e:
            logger.warning("Metric emission failed | error=%s", e)

    # ── DEAD LETTER QUEUE ─────────────────────────────────────

    async def _send_to_dlq(self, payload: dict, topic: str, error: str) -> None:
        try:
            await self.producer.send_and_wait(
                "fte.dlq",
                value={
                    "original_topic": topic,
                    "payload":        payload,
                    "error":          error,
                    "failed_at":      datetime.now(timezone.utc).isoformat(),
                },
            )
            logger.error("Message sent to DLQ | topic=%s | error=%s", topic, error)
        except Exception as e:
            logger.critical("DLQ send failed | error=%s", e)


# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────

async def _main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    processor = UnifiedMessageProcessor()

    loop = asyncio.get_running_loop()

    def _shutdown(sig: signal.Signals) -> None:
        logger.info("Signal %s received — shutting down …", sig.name)
        processor._running = False

    try:
        # Unix only — Windows raises NotImplementedError
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _shutdown, sig)
    except NotImplementedError:
        # Windows fallback
        signal.signal(signal.SIGINT,  lambda s, f: _shutdown(signal.SIGINT))
        signal.signal(signal.SIGTERM, lambda s, f: _shutdown(signal.SIGTERM))

    await processor.start()


if __name__ == "__main__":
    asyncio.run(_main())
