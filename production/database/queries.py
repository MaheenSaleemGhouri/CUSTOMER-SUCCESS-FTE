"""
production/database/queries.py
Typed async query functions for all 7 DB tables.
Used by production/agent/tools.py and workers/message_processor.py.

Connection is injected — callers pass an asyncpg Connection or Pool.
No ORM: raw asyncpg for performance.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg


# ─────────────────────────────────────────────────────────────
# TABLE 1 — customers
# ─────────────────────────────────────────────────────────────

async def get_customer_by_email(conn: asyncpg.Connection, email: str) -> Optional[asyncpg.Record]:
    return await conn.fetchrow(
        "SELECT * FROM customers WHERE canonical_email = $1 AND deleted_at IS NULL",
        email.lower().strip(),
    )


async def get_customer_by_phone(conn: asyncpg.Connection, phone: str) -> Optional[asyncpg.Record]:
    return await conn.fetchrow(
        "SELECT * FROM customers WHERE canonical_phone = $1 AND deleted_at IS NULL",
        phone.strip(),
    )


async def get_customer_by_identifier(
    conn: asyncpg.Connection,
    identifier_type: str,
    identifier_value: str,
) -> Optional[asyncpg.Record]:
    """Resolve any identifier (email/phone/alias) to a customer record."""
    return await conn.fetchrow(
        """
        SELECT c.*
        FROM customers c
        JOIN customer_identifiers ci ON ci.customer_id = c.id
        WHERE ci.identifier_type = $1::identifier_type
          AND ci.identifier_value = $2
          AND c.deleted_at IS NULL
        LIMIT 1
        """,
        identifier_type,
        identifier_value.lower().strip(),
    )


async def create_customer(
    conn: asyncpg.Connection,
    *,
    canonical_email: Optional[str] = None,
    canonical_phone: Optional[str] = None,
    display_name: Optional[str] = None,
    plan_tier: str = "unknown",
    first_channel: str,
) -> asyncpg.Record:
    return await conn.fetchrow(
        """
        INSERT INTO customers (
            canonical_email, canonical_phone, display_name,
            plan_tier, first_channel, channels_used, first_contact_at, last_contact_at
        )
        VALUES ($1, $2, $3, $4::plan_tier, $5::channel_type, ARRAY[$5::channel_type], NOW(), NOW())
        RETURNING *
        """,
        canonical_email.lower().strip() if canonical_email else None,
        canonical_phone,
        display_name,
        plan_tier,
        first_channel,
    )


async def update_customer_sentiment(
    conn: asyncpg.Connection,
    customer_id: uuid.UUID,
    sentiment_score: float,
    trend: str,
) -> None:
    await conn.execute(
        """
        UPDATE customers SET
            last_sentiment_score  = $2,
            sentiment_trend       = $3,
            lifetime_sentiment_avg = CASE
                WHEN lifetime_sentiment_avg IS NULL THEN $2
                ELSE (lifetime_sentiment_avg * 0.8 + $2 * 0.2)
            END,
            updated_at = NOW()
        WHERE id = $1
        """,
        customer_id, sentiment_score, trend,
    )


async def increment_customer_escalation(conn: asyncpg.Connection, customer_id: uuid.UUID) -> None:
    await conn.execute(
        "UPDATE customers SET total_escalations = total_escalations + 1, updated_at = NOW() WHERE id = $1",
        customer_id,
    )


async def increment_customer_resolved(conn: asyncpg.Connection, customer_id: uuid.UUID) -> None:
    await conn.execute(
        "UPDATE customers SET total_resolved = total_resolved + 1, updated_at = NOW() WHERE id = $1",
        customer_id,
    )


# ─────────────────────────────────────────────────────────────
# TABLE 2 — customer_identifiers
# ─────────────────────────────────────────────────────────────

async def register_identifier(
    conn: asyncpg.Connection,
    customer_id: uuid.UUID,
    identifier_type: str,
    identifier_value: str,
    channel: Optional[str] = None,
    is_primary: bool = False,
) -> asyncpg.Record:
    return await conn.fetchrow(
        """
        INSERT INTO customer_identifiers
            (customer_id, identifier_type, identifier_value, channel, is_primary)
        VALUES ($1, $2::identifier_type, $3, $4::channel_type, $5)
        ON CONFLICT (identifier_type, identifier_value)
            DO UPDATE SET last_seen_at = NOW()
        RETURNING *
        """,
        customer_id,
        identifier_type,
        identifier_value.lower().strip(),
        channel,
        is_primary,
    )


async def lookup_customer_id_by_identifier(
    conn: asyncpg.Connection, identifier_value: str
) -> Optional[uuid.UUID]:
    row = await conn.fetchrow(
        "SELECT customer_id FROM customer_identifiers WHERE identifier_value = $1",
        identifier_value.lower().strip(),
    )
    return row["customer_id"] if row else None


# ─────────────────────────────────────────────────────────────
# TABLE 3 — conversations
# ─────────────────────────────────────────────────────────────

async def get_active_conversation(
    conn: asyncpg.Connection,
    customer_id: uuid.UUID,
    within_minutes: int = 30,
) -> Optional[asyncpg.Record]:
    """Return the most recent active conversation if within timeout window."""
    return await conn.fetchrow(
        """
        SELECT * FROM conversations
        WHERE customer_id = $1
          AND status = 'active'
          AND last_message_at > NOW() - ($2 || ' minutes')::INTERVAL
        ORDER BY last_message_at DESC
        LIMIT 1
        """,
        customer_id, str(within_minutes),
    )


async def create_conversation(
    conn: asyncpg.Connection,
    customer_id: uuid.UUID,
    channel: str,
    opening_sentiment: Optional[float] = None,
    timeout_minutes: int = 30,
) -> asyncpg.Record:
    timeout = datetime.now(timezone.utc) + timedelta(minutes=timeout_minutes)
    return await conn.fetchrow(
        """
        INSERT INTO conversations (
            customer_id, initial_channel, current_channel,
            channel_journey, opening_sentiment, sentiment_score,
            session_timeout_at
        )
        VALUES ($1, $2::channel_type, $2::channel_type, ARRAY[$2::channel_type], $3, $3, $4)
        RETURNING *
        """,
        customer_id, channel, opening_sentiment, timeout,
    )


async def update_conversation_sentiment(
    conn: asyncpg.Connection,
    conversation_id: uuid.UUID,
    sentiment_score: float,
    anger_spike: bool = False,
) -> None:
    await conn.execute(
        """
        UPDATE conversations SET
            sentiment_score       = $2,
            min_sentiment         = LEAST(COALESCE(min_sentiment, $2), $2),
            anger_spike_occurred  = anger_spike_occurred OR $3,
            last_message_at       = NOW(),
            updated_at            = NOW()
        WHERE id = $1
        """,
        conversation_id, sentiment_score, anger_spike,
    )


async def update_conversation_channel(
    conn: asyncpg.Connection,
    conversation_id: uuid.UUID,
    new_channel: str,
) -> None:
    await conn.execute(
        """
        UPDATE conversations SET
            current_channel  = $2::channel_type,
            channel_journey  = array_append(channel_journey, $2::channel_type),
            last_message_at  = NOW(),
            updated_at       = NOW()
        WHERE id = $1
        """,
        conversation_id, new_channel,
    )


async def update_conversation_topics(
    conn: asyncpg.Connection,
    conversation_id: uuid.UUID,
    new_topics: list[str],
) -> None:
    await conn.execute(
        """
        UPDATE conversations SET
            topics     = array(SELECT DISTINCT unnest(topics || $2::text[])),
            updated_at = NOW()
        WHERE id = $1
        """,
        conversation_id, new_topics,
    )


async def close_conversation(
    conn: asyncpg.Connection,
    conversation_id: uuid.UUID,
    status: str,
    closing_sentiment: Optional[float] = None,
    resolution_summary: Optional[str] = None,
) -> None:
    await conn.execute(
        """
        UPDATE conversations SET
            status             = $2::conversation_status,
            closing_sentiment  = $3,
            resolution_summary = $4,
            resolved_at        = CASE WHEN $2 = 'resolved' THEN NOW() ELSE NULL END,
            updated_at         = NOW()
        WHERE id = $1
        """,
        conversation_id, status, closing_sentiment, resolution_summary,
    )


async def get_conversation_history(
    conn: asyncpg.Connection,
    customer_id: uuid.UUID,
    limit: int = 5,
) -> list[asyncpg.Record]:
    """Return recent conversations with their last few messages."""
    return await conn.fetch(
        """
        SELECT c.id, c.initial_channel, c.status, c.sentiment_score,
               c.topics, c.created_at, c.resolved_at
        FROM conversations c
        WHERE c.customer_id = $1
        ORDER BY c.created_at DESC
        LIMIT $2
        """,
        customer_id, limit,
    )


# ─────────────────────────────────────────────────────────────
# TABLE 4 — messages
# ─────────────────────────────────────────────────────────────

async def store_inbound_message(
    conn: asyncpg.Connection,
    *,
    conversation_id: uuid.UUID,
    customer_id: uuid.UUID,
    channel: str,
    raw_content: str,
    subject: Optional[str] = None,
    channel_message_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    sentiment_score: Optional[float] = None,
    topics_detected: Optional[list[str]] = None,
    metadata: Optional[dict] = None,
) -> asyncpg.Record:
    return await conn.fetchrow(
        """
        INSERT INTO messages (
            conversation_id, customer_id, direction, channel,
            raw_content, subject, channel_message_id, thread_id,
            sentiment_score, topics_detected, metadata,
            delivery_status, processing_started_at, received_at
        )
        VALUES (
            $1, $2, 'inbound'::message_direction, $3::channel_type,
            $4, $5, $6, $7, $8, $9::text[], $10::jsonb,
            'delivered'::message_delivery_status, NOW(), NOW()
        )
        RETURNING *
        """,
        conversation_id, customer_id, channel,
        raw_content, subject, channel_message_id, thread_id,
        sentiment_score, topics_detected or [], json.dumps(metadata or {}),
    )


async def store_outbound_message(
    conn: asyncpg.Connection,
    *,
    conversation_id: uuid.UUID,
    ticket_id: uuid.UUID,
    customer_id: uuid.UUID,
    channel: str,
    raw_content: str,
    formatted_content: str,
    model_used: str = "gpt-4o",
    tool_calls: Optional[list[dict]] = None,
    escalated_in_turn: bool = False,
    escalation_reason: Optional[str] = None,
    kb_searches: int = 0,
    kb_search_successful: Optional[bool] = None,
    latency_ms: Optional[int] = None,
    processing_started_at: Optional[datetime] = None,
) -> asyncpg.Record:
    tool_calls_json = json.dumps(tool_calls or [])
    tool_count      = len(tool_calls) if tool_calls else 0

    return await conn.fetchrow(
        """
        INSERT INTO messages (
            conversation_id, ticket_id, customer_id, direction, channel,
            raw_content, formatted_content, model_used,
            tool_calls, tool_call_count, kb_searches, kb_search_successful,
            escalated_in_turn, escalation_reason,
            latency_ms, processing_started_at, processing_completed_at,
            char_count, word_count,
            delivery_status, received_at
        )
        VALUES (
            $1, $2, $3, 'outbound'::message_direction, $4::channel_type,
            $5, $6, $7,
            $8::jsonb, $9, $10, $11,
            $12, $13::escalation_reason,
            $14, $15, NOW(),
            $16, $17,
            'pending'::message_delivery_status, NOW()
        )
        RETURNING *
        """,
        conversation_id, ticket_id, customer_id, channel,
        raw_content, formatted_content, model_used,
        tool_calls_json, tool_count, kb_searches, kb_search_successful,
        escalated_in_turn, escalation_reason,
        latency_ms, processing_started_at,
        len(formatted_content), len(formatted_content.split()),
    )


async def mark_message_delivered(
    conn: asyncpg.Connection,
    message_id: uuid.UUID,
    channel_message_id: Optional[str] = None,
) -> None:
    await conn.execute(
        """
        UPDATE messages SET
            delivery_status    = 'delivered',
            delivered_at       = NOW(),
            channel_message_id = COALESCE($2, channel_message_id)
        WHERE id = $1
        """,
        message_id, channel_message_id,
    )


async def mark_message_failed(
    conn: asyncpg.Connection,
    message_id: uuid.UUID,
    error: str,
) -> None:
    await conn.execute(
        """
        UPDATE messages SET
            delivery_status = 'failed',
            delivery_error  = $2
        WHERE id = $1
        """,
        message_id, error,
    )


async def get_recent_messages(
    conn: asyncpg.Connection,
    conversation_id: uuid.UUID,
    limit: int = 10,
) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT id, direction, channel, raw_content, formatted_content,
               sentiment_score, topics_detected, tool_calls, latency_ms,
               escalated_in_turn, escalation_reason, received_at
        FROM messages
        WHERE conversation_id = $1
        ORDER BY received_at
        LIMIT $2
        """,
        conversation_id, limit,
    )


# ─────────────────────────────────────────────────────────────
# TABLE 5 — tickets
# ─────────────────────────────────────────────────────────────

async def create_ticket(
    conn: asyncpg.Connection,
    *,
    customer_id: uuid.UUID,
    conversation_id: Optional[uuid.UUID],
    source_channel: str,
    priority: str,
    category: str,
    issue_summary: str,
    original_message: Optional[str] = None,
    opening_sentiment: Optional[float] = None,
    sla_hours: Optional[int] = None,
) -> asyncpg.Record:
    sla_deadline = (
        datetime.now(timezone.utc) + timedelta(hours=sla_hours)
        if sla_hours else None
    )
    return await conn.fetchrow(
        """
        INSERT INTO tickets (
            customer_id, conversation_id, source_channel,
            priority, category, issue_summary,
            original_message, opening_sentiment, sla_deadline
        )
        VALUES (
            $1, $2, $3::channel_type,
            $4::ticket_priority, $5::ticket_category, $6,
            $7, $8, $9
        )
        RETURNING *
        """,
        customer_id, conversation_id, source_channel,
        priority, category, issue_summary,
        original_message, opening_sentiment, sla_deadline,
    )


async def get_ticket(conn: asyncpg.Connection, ticket_id: uuid.UUID) -> Optional[asyncpg.Record]:
    return await conn.fetchrow("SELECT * FROM tickets WHERE id = $1", ticket_id)


async def get_ticket_by_ref(conn: asyncpg.Connection, ticket_ref: str) -> Optional[asyncpg.Record]:
    return await conn.fetchrow("SELECT * FROM tickets WHERE ticket_ref = $1", ticket_ref)


async def escalate_ticket(
    conn: asyncpg.Connection,
    ticket_id: uuid.UUID,
    reason: str,
    escalation_id: str,
    routed_to: str,
    assigned_to: Optional[str] = None,
) -> None:
    await conn.execute(
        """
        UPDATE tickets SET
            status           = 'escalated'::ticket_status,
            escalated        = TRUE,
            escalation_reason = $2::escalation_reason,
            escalation_id    = $3,
            routed_to        = $4,
            assigned_to      = $5,
            escalated_at     = NOW(),
            updated_at       = NOW()
        WHERE id = $1
        """,
        ticket_id, reason, escalation_id, routed_to, assigned_to,
    )


async def resolve_ticket(
    conn: asyncpg.Connection,
    ticket_id: uuid.UUID,
    resolution: str,
) -> None:
    await conn.execute(
        """
        UPDATE tickets SET
            status       = 'resolved'::ticket_status,
            resolution   = $2,
            resolved_at  = NOW(),
            updated_at   = NOW()
        WHERE id = $1
        """,
        ticket_id, resolution,
    )


async def set_first_response(
    conn: asyncpg.Connection,
    ticket_id: uuid.UUID,
    latency_ms: int,
) -> None:
    await conn.execute(
        """
        UPDATE tickets SET
            first_response_at = NOW(),
            first_response_ms = $2,
            status            = 'in_progress'::ticket_status,
            updated_at        = NOW()
        WHERE id = $1 AND first_response_at IS NULL
        """,
        ticket_id, latency_ms,
    )


async def get_customer_tickets(
    conn: asyncpg.Connection,
    customer_id: uuid.UUID,
    limit: int = 10,
) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT id, ticket_ref, source_channel, priority, category,
               status, escalated, escalation_reason, issue_summary,
               opened_at, resolved_at
        FROM tickets
        WHERE customer_id = $1
        ORDER BY opened_at DESC
        LIMIT $2
        """,
        customer_id, limit,
    )


# ─────────────────────────────────────────────────────────────
# TABLE 6 — knowledge_base
# ─────────────────────────────────────────────────────────────

async def vector_search_kb(
    conn: asyncpg.Connection,
    query_embedding: list[float],
    max_results: int = 5,
    top_k: Optional[int] = None,
    category: Optional[str] = None,
) -> list[asyncpg.Record]:
    max_results = top_k or max_results
    """Semantic search using pgvector cosine similarity."""
    # asyncpg doesn't know vector type — pass as string '[x, y, ...]'
    vec_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
    if category:
        return await conn.fetch(
            """
            SELECT id, kb_ref, category, title, content,
                   1 - (embedding <=> $1::vector) AS similarity
            FROM knowledge_base
            WHERE is_active = TRUE
              AND category = $3
              AND embedding IS NOT NULL
            ORDER BY embedding <=> $1::vector
            LIMIT $2
            """,
            vec_str, max_results, category,
        )
    return await conn.fetch(
        """
        SELECT id, kb_ref, category, title, content,
               1 - (embedding <=> $1::vector) AS similarity
        FROM knowledge_base
        WHERE is_active = TRUE
          AND embedding IS NOT NULL
        ORDER BY embedding <=> $1::vector
        LIMIT $2
        """,
        vec_str, max_results,
    )


async def fulltext_search_kb(
    conn: asyncpg.Connection,
    query: str,
    max_results: int = 5,
    top_k: Optional[int] = None,
    category: Optional[str] = None,
) -> list[asyncpg.Record]:
    max_results = top_k or max_results
    """Full-text search fallback (used when embeddings not yet generated)."""
    if category:
        return await conn.fetch(
            """
            SELECT id, kb_ref, category, title, content,
                   ts_rank(to_tsvector('english', title || ' ' || content),
                           plainto_tsquery('english', $1)) AS rank
            FROM knowledge_base
            WHERE is_active = TRUE
              AND category = $3
              AND to_tsvector('english', title || ' ' || content)
                  @@ plainto_tsquery('english', $1)
            ORDER BY rank DESC
            LIMIT $2
            """,
            query, max_results, category,
        )
    return await conn.fetch(
        """
        SELECT id, kb_ref, category, title, content,
               ts_rank(to_tsvector('english', title || ' ' || content),
                       plainto_tsquery('english', $1)) AS rank
        FROM knowledge_base
        WHERE is_active = TRUE
          AND to_tsvector('english', title || ' ' || content)
              @@ plainto_tsquery('english', $1)
        ORDER BY rank DESC
        LIMIT $2
        """,
        query, max_results,
    )


async def increment_kb_hit(conn: asyncpg.Connection, kb_id: uuid.UUID) -> None:
    await conn.execute(
        "UPDATE knowledge_base SET search_hits = search_hits + 1 WHERE id = $1",
        kb_id,
    )


async def increment_kb_used(conn: asyncpg.Connection, kb_id: uuid.UUID) -> None:
    await conn.execute(
        "UPDATE knowledge_base SET search_used = search_used + 1, updated_at = NOW() WHERE id = $1",
        kb_id,
    )


async def upsert_kb_entry(
    conn: asyncpg.Connection,
    *,
    kb_ref: str,
    category: str,
    title: str,
    content: str,
    embedding: Optional[list[float]] = None,
) -> asyncpg.Record:
    return await conn.fetchrow(
        """
        INSERT INTO knowledge_base (kb_ref, category, title, content, embedding, word_count)
        VALUES ($1, $2, $3, $4, $5::vector, $6)
        ON CONFLICT (kb_ref) DO UPDATE SET
            category   = EXCLUDED.category,
            title      = EXCLUDED.title,
            content    = EXCLUDED.content,
            embedding  = EXCLUDED.embedding,
            word_count = EXCLUDED.word_count,
            version    = knowledge_base.version + 1,
            updated_at = NOW()
        RETURNING *
        """,
        kb_ref, category, title, content,
        ("[" + ",".join(str(x) for x in embedding) + "]") if embedding else None,
        len(content.split()),
    )


# ─────────────────────────────────────────────────────────────
# TABLE 7 — agent_metrics
# ─────────────────────────────────────────────────────────────

async def upsert_daily_metric(
    conn: asyncpg.Connection,
    metric_date: datetime,
    channel: str,
    *,
    resolved: int = 0,
    escalated: int = 0,
    latency_ms: Optional[int] = None,
    sentiment_opening: Optional[float] = None,
    sentiment_closing: Optional[float] = None,
    anger_spike: bool = False,
    kb_search: bool = False,
    kb_hit: bool = False,
    sla_breach: bool = False,
    cross_channel: bool = False,
    escalation_reason: Optional[str] = None,
    delivery_failed: bool = False,
) -> None:
    """Upsert one metric event into the daily rollup row."""
    # Map escalation_reason to its column
    reason_col_map = {
        "pricing_inquiry":  "escalations_pricing",
        "refund_request":   "escalations_refund",
        "legal_escalation": "escalations_legal",
        "angry_customer":   "escalations_angry",
        "anger_spike":      "escalations_angry",
        "technical_tier2":  "escalations_technical",
        "human_requested":  "escalations_human_requested",
    }
    esc_col = reason_col_map.get(escalation_reason or "", "escalations_other")
    esc_inc = 1 if escalated else 0

    await conn.execute(
        f"""
        INSERT INTO agent_metrics (metric_date, channel,
            total_tickets, resolved_by_ai, escalated_to_human,
            {esc_col},
            sla_breaches, anger_spike_count,
            total_kb_searches, cross_channel_sessions, delivery_failures)
        VALUES (
            $1::date, $2::channel_type,
            1, $3, $4,
            {esc_inc},
            $5::int, $6::int,
            $7::int, $8::int, $9::int)
        ON CONFLICT (metric_date, channel) DO UPDATE SET
            total_tickets          = agent_metrics.total_tickets + 1,
            resolved_by_ai         = agent_metrics.resolved_by_ai + $3,
            escalated_to_human     = agent_metrics.escalated_to_human + $4,
            {esc_col}              = agent_metrics.{esc_col} + {esc_inc},
            sla_breaches           = agent_metrics.sla_breaches + $5::int,
            anger_spike_count      = agent_metrics.anger_spike_count + $6::int,
            total_kb_searches      = agent_metrics.total_kb_searches + $7::int,
            cross_channel_sessions = agent_metrics.cross_channel_sessions + $8::int,
            delivery_failures      = agent_metrics.delivery_failures + $9::int,
            updated_at             = NOW()
        """,
        metric_date.date(), channel,
        int(resolved > 0), escalated,
        int(sla_breach), int(anger_spike),
        int(kb_search), int(cross_channel), int(delivery_failed),
    )


async def get_channel_metrics(
    conn: asyncpg.Connection,
    date: Optional[str] = None,
    channel: Optional[str] = None,
    hours: int = 24,
) -> list[asyncpg.Record]:
    """Return per-channel metrics for the last N hours (for /metrics/channels endpoint)."""
    return await conn.fetch(
        """
        SELECT
            channel,
            SUM(total_tickets)            AS total_tickets,
            SUM(resolved_by_ai)           AS resolved_by_ai,
            SUM(escalated_to_human)       AS escalated_to_human,
            ROUND(AVG(escalation_rate), 4) AS avg_escalation_rate,
            ROUND(AVG(avg_latency_ms), 0)  AS avg_latency_ms,
            MAX(p95_latency_ms)            AS p95_latency_ms,
            ROUND(AVG(avg_sentiment_opening), 3) AS avg_sentiment_opening,
            ROUND(AVG(avg_sentiment_closing), 3) AS avg_sentiment_closing,
            SUM(sla_breaches)              AS sla_breaches,
            SUM(cross_channel_sessions)    AS cross_channel_sessions
        FROM agent_metrics
        WHERE metric_date >= NOW() - ($1 || ' hours')::INTERVAL
        GROUP BY channel
        ORDER BY channel
        """,
        str(hours),
    )


# ─────────────────────────────────────────────────────────────
# MISSING FUNCTIONS — added for tools.py / message_processor compatibility
# ─────────────────────────────────────────────────────────────

async def get_customer_by_id(
    conn: asyncpg.Connection,
    customer_id: uuid.UUID,
) -> Optional[asyncpg.Record]:
    """Fetch a customer row directly by primary key UUID."""
    return await conn.fetchrow(
        "SELECT * FROM customers WHERE id = $1 AND deleted_at IS NULL",
        customer_id,
    )


async def get_or_create_conversation(
    conn: asyncpg.Connection,
    *,
    customer_id: uuid.UUID,
    channel: str,
    timeout_minutes: int = 30,
) -> asyncpg.Record:
    """Return the active conversation for this customer/channel, or create one."""
    existing = await get_active_conversation(conn, customer_id, within_minutes=timeout_minutes)
    if existing:
        return existing
    return await create_conversation(conn, customer_id, channel)


async def get_customer_conversations(
    conn: asyncpg.Connection,
    customer_id: uuid.UUID,
    limit: int = 5,
) -> list[asyncpg.Record]:
    """Return recent conversations for a customer (alias for get_conversation_history)."""
    return await conn.fetch(
        """
        SELECT id, initial_channel AS channel, status,
               created_at, updated_at, topics,
               (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count
        FROM conversations c
        WHERE c.customer_id = $1
        ORDER BY c.created_at DESC
        LIMIT $2
        """,
        customer_id, limit,
    )


async def get_customer_ticket_counts(
    conn: asyncpg.Connection,
    customer_id: uuid.UUID,
) -> dict:
    """Return total and open ticket counts for a customer."""
    row = await conn.fetchrow(
        """
        SELECT
            COUNT(*)                                          AS total,
            COUNT(*) FILTER (WHERE status = 'open')          AS open,
            COUNT(*) FILTER (WHERE status = 'escalated')     AS escalated,
            COUNT(*) FILTER (WHERE status = 'resolved')      AS resolved
        FROM tickets
        WHERE customer_id = $1
        """,
        customer_id,
    )
    return dict(row) if row else {"total": 0, "open": 0, "escalated": 0, "resolved": 0}


async def get_customer_identifiers(
    conn: asyncpg.Connection,
    customer_id: uuid.UUID,
) -> list[asyncpg.Record]:
    """Return all identifiers (email, phone, etc.) registered for a customer."""
    return await conn.fetch(
        """
        SELECT identifier_type, identifier_value, channel, is_primary, last_seen_at
        FROM customer_identifiers
        WHERE customer_id = $1
        ORDER BY is_primary DESC, last_seen_at DESC
        """,
        customer_id,
    )


async def update_ticket_status(
    conn: asyncpg.Connection,
    ticket_id: uuid.UUID,
    new_status: str,
) -> None:
    """Generic ticket status update — used by escalate_to_human and manual escalation."""
    await conn.execute(
        """
        UPDATE tickets SET status = $2::ticket_status, updated_at = NOW()
        WHERE id = $1
        """,
        ticket_id, new_status,
    )


async def update_ticket_conversation(
    conn: asyncpg.Connection,
    ticket_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> None:
    """Attach a conversation_id to an existing ticket (set by message_processor)."""
    await conn.execute(
        """
        UPDATE tickets SET conversation_id = $2, updated_at = NOW()
        WHERE id = $1 AND conversation_id IS NULL
        """,
        ticket_id, conversation_id,
    )


async def create_escalation(
    conn: asyncpg.Connection,
    *,
    ticket_id: uuid.UUID,
    customer_id: uuid.UUID,
    reason: str,
    urgency: str = "normal",
    routing_team: str,
    routing_email: str,
    notes: str = "",
) -> asyncpg.Record:
    """
    Insert a row into the escalations table and return it.
    Also updates the ticket's escalation fields via escalate_ticket().
    """
    esc = await conn.fetchrow(
        """
        INSERT INTO escalations (
            ticket_id, customer_id, reason, urgency,
            routing_team, routing_email, notes, escalated_at
        )
        VALUES ($1, $2, $3::escalation_reason, $4, $5, $6, $7, NOW())
        RETURNING *
        """,
        ticket_id, customer_id, reason, urgency,
        routing_team, routing_email, notes,
    )
    # Keep ticket table in sync
    await escalate_ticket(
        conn,
        ticket_id=ticket_id,
        reason=reason,
        escalation_id=str(esc["id"]),
        routed_to=routing_email,
    )
    return esc


async def insert_message(
    conn: asyncpg.Connection,
    *,
    conversation_id: uuid.UUID,
    direction: str,
    channel: str,
    raw_content: str,
    formatted_content: str = "",
    delivery_status: str = "delivered",
) -> asyncpg.Record:
    """
    Unified message insert — routes to store_inbound_message or a simple
    outbound insert depending on direction.
    """
    if direction == "inbound":
        return await store_inbound_message(
            conn,
            conversation_id=conversation_id,
            customer_id=await conn.fetchval(
                "SELECT customer_id FROM conversations WHERE id = $1", conversation_id
            ),
            channel=channel,
            raw_content=raw_content,
        )
    # outbound — lightweight insert (tools.py doesn't have ticket_id at this point)
    return await conn.fetchrow(
        """
        INSERT INTO messages (
            conversation_id, customer_id, direction, channel,
            raw_content, formatted_content, delivery_status, received_at
        )
        SELECT $1, customer_id, 'outbound'::message_direction, $2::channel_type,
               $3, $4, $5::message_delivery_status, NOW()
        FROM conversations WHERE id = $1
        RETURNING *
        """,
        conversation_id, channel, raw_content, formatted_content, delivery_status,
    )


async def upsert_daily_metric_simple(
    conn: asyncpg.Connection,
    *,
    date: str,
    channel: str,
    metric: str,
    increment: int = 1,
) -> None:
    """
    Simple metric upsert used by message_processor.
    Maps metric name strings to the correct agent_metrics column.
    """
    col_map = {
        "messages_processed": "total_tickets",
        "escalations":        "escalated_to_human",
        "low_sentiment":      "anger_spike_count",
        "resolved":           "resolved_by_ai",
        "sla_breach":         "sla_breaches",
        "kb_search":          "total_kb_searches",
    }
    col = col_map.get(metric, "total_tickets")
    await conn.execute(
        f"""
        INSERT INTO agent_metrics (metric_date, channel, {col})
        VALUES ($1::date, $2::channel_type, $3)
        ON CONFLICT (metric_date, channel) DO UPDATE
            SET {col} = agent_metrics.{col} + $3,
                updated_at = NOW()
        """,
        date, channel, increment,
    )


async def upsert_latency_metric(
    conn: asyncpg.Connection,
    *,
    date: str,
    channel: str,
    latency_ms: int,
) -> None:
    """
    Update avg_latency_ms and p95_latency_ms in agent_metrics.
    Uses running average: new_avg = (old_avg * (n-1) + new_value) / n
    p95 approximated by tracking max latency seen.
    """
    await conn.execute(
        """
        INSERT INTO agent_metrics (metric_date, channel, avg_latency_ms, p95_latency_ms, max_latency_ms, total_tickets)
        VALUES ($1::date, $2::channel_type, $3::numeric, $4::integer, $4::integer, 0)
        ON CONFLICT (metric_date, channel) DO UPDATE SET
            avg_latency_ms = CASE
                WHEN agent_metrics.avg_latency_ms IS NULL OR agent_metrics.avg_latency_ms = 0
                THEN $3::numeric
                ELSE ROUND((agent_metrics.avg_latency_ms * (agent_metrics.total_tickets - 1) + $3::numeric) / agent_metrics.total_tickets, 2)
            END,
            p95_latency_ms = GREATEST(COALESCE(agent_metrics.p95_latency_ms, 0), $4::integer),
            max_latency_ms = GREATEST(COALESCE(agent_metrics.max_latency_ms, 0), $4::integer),
            updated_at = NOW()
        """,
        date, channel, float(latency_ms), latency_ms,
    )
