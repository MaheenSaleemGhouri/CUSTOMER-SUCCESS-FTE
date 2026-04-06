-- =============================================================
-- schema.sql — Exercise 2.1 | Customer Success Digital FTE
-- Stage 2: Specialization | PostgreSQL 16 + pgvector
-- =============================================================
-- Tables (7 required):
--   1. customers            — unified customer record across all channels
--   2. customer_identifiers — cross-channel identity matching
--   3. conversations        — one conversation per support session
--   4. messages             — every inbound/outbound message
--   5. tickets              — support tickets, lifecycle tracking
--   6. knowledge_base       — vector-searchable product docs
--   7. agent_metrics        — per-channel, per-day performance KPIs
-- =============================================================

-- Enable pgvector extension (must run as superuser once per database)
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- for LIKE/ILIKE fast search on text fields


-- =============================================================
-- ENUMS
-- =============================================================

CREATE TYPE channel_type AS ENUM (
    'email',
    'whatsapp',
    'web_form'
);

CREATE TYPE ticket_status AS ENUM (
    'open',
    'in_progress',
    'escalated',
    'resolved',
    'closed',
    'spam'
);

CREATE TYPE ticket_priority AS ENUM (
    'low',
    'medium',
    'high',
    'critical'
);

CREATE TYPE ticket_category AS ENUM (
    'authentication',
    'billing',
    'technical',
    'general',
    'integration',
    'legal',
    'feature_request',
    'account',
    'onboarding'
);

CREATE TYPE message_direction AS ENUM (
    'inbound',
    'outbound'
);

CREATE TYPE message_delivery_status AS ENUM (
    'pending',
    'delivered',
    'failed',
    'queued',
    'bounced'
);

CREATE TYPE conversation_status AS ENUM (
    'active',
    'resolved',
    'escalated',
    'abandoned'
);

CREATE TYPE escalation_reason AS ENUM (
    'pricing_inquiry',
    'refund_request',
    'legal_escalation',
    'angry_customer',
    'human_requested',
    'technical_tier2',
    'enterprise_account',
    'business_account_unresolved',
    'anger_spike',
    'partnership_request'
);

CREATE TYPE identifier_type AS ENUM (
    'email',
    'phone',
    'whatsapp_id',
    'web_session'
);

CREATE TYPE plan_tier AS ENUM (
    'starter',
    'growth',
    'business',
    'enterprise',
    'unknown'
);


-- =============================================================
-- TABLE 1: customers
-- Unified customer record — single row per real person/company.
-- Canonical ID used as FK across all other tables.
-- Cross-channel aliases stored in customer_identifiers.
-- =============================================================

CREATE TABLE customers (
    -- Identity
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    canonical_email     TEXT,                               -- primary email, nullable (WhatsApp-only customers may not have one)
    canonical_phone     TEXT,                               -- E.164 format, e.g. +15125550142
    display_name        TEXT,
    company_name        TEXT,

    -- Plan & Account
    plan_tier           plan_tier NOT NULL DEFAULT 'unknown',
    plan_started_at     TIMESTAMPTZ,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,

    -- Sentiment & Health (aggregated, updated after each interaction)
    lifetime_sentiment_avg  NUMERIC(4,3) CHECK (lifetime_sentiment_avg BETWEEN 0 AND 1),
    last_sentiment_score    NUMERIC(4,3) CHECK (last_sentiment_score BETWEEN 0 AND 1),
    sentiment_trend         TEXT CHECK (sentiment_trend IN ('improving', 'stable', 'worsening', 'unknown')),
    total_escalations       INTEGER NOT NULL DEFAULT 0,
    total_resolved          INTEGER NOT NULL DEFAULT 0,
    churn_risk_score        NUMERIC(4,3) CHECK (churn_risk_score BETWEEN 0 AND 1),

    -- Channel usage summary
    first_channel       channel_type,
    channels_used       channel_type[] NOT NULL DEFAULT '{}',
    total_sessions      INTEGER NOT NULL DEFAULT 0,
    total_messages      INTEGER NOT NULL DEFAULT 0,

    -- Timestamps
    first_contact_at    TIMESTAMPTZ,
    last_contact_at     TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Soft delete
    deleted_at          TIMESTAMPTZ,

    -- Constraints: at least one identifier must be set
    CONSTRAINT chk_customers_has_identifier CHECK (
        canonical_email IS NOT NULL OR canonical_phone IS NOT NULL
    )
);

COMMENT ON TABLE customers IS 'Unified customer record across all support channels. One row per real customer, regardless of how many channels they use.';
COMMENT ON COLUMN customers.canonical_email IS 'Primary email. Used as the default canonical ID for email/web-form customers.';
COMMENT ON COLUMN customers.canonical_phone IS 'E.164 phone number. Primary identifier for WhatsApp-only customers.';
COMMENT ON COLUMN customers.churn_risk_score IS 'ML-computed score 0-1. High escalations + low sentiment = high risk.';


-- =============================================================
-- TABLE 2: customer_identifiers
-- Cross-channel identity matching.
-- Maps any identifier (email, phone, WhatsApp ID, web session)
-- to a canonical customer UUID.
-- =============================================================

CREATE TABLE customer_identifiers (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    customer_id     UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,

    -- The identifier value and its type
    identifier_type identifier_type NOT NULL,
    identifier_value TEXT NOT NULL,

    -- Which channel this identifier is associated with
    channel         channel_type,

    -- Whether this is the primary identifier for this customer
    is_primary      BOOLEAN NOT NULL DEFAULT FALSE,

    -- Source tracking
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_identifier UNIQUE (identifier_type, identifier_value)
);

COMMENT ON TABLE customer_identifiers IS 'One row per unique identifier (email, phone, WhatsApp ID) for cross-channel identity resolution. A single customer may have multiple identifiers.';
COMMENT ON COLUMN customer_identifiers.identifier_value IS 'The raw value: email address, E.164 phone, or WhatsApp profile ID.';
COMMENT ON COLUMN customer_identifiers.is_primary IS 'True for the main identifier used in external communications.';


-- =============================================================
-- TABLE 3: conversations
-- One row per support session. A session groups related messages
-- together, starting from first contact until resolution/timeout.
-- Tracks channel journey, sentiment arc, and session outcome.
-- =============================================================

CREATE TABLE conversations (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    customer_id         UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,

    -- Channel
    initial_channel     channel_type NOT NULL,
    current_channel     channel_type NOT NULL,
    channel_journey     channel_type[] NOT NULL DEFAULT '{}',  -- ordered list of channels used in this session

    -- Status & resolution
    status              conversation_status NOT NULL DEFAULT 'active',
    resolution_summary  TEXT,
    resolved_at         TIMESTAMPTZ,

    -- Sentiment arc for this session
    opening_sentiment   NUMERIC(4,3) CHECK (opening_sentiment BETWEEN 0 AND 1),
    closing_sentiment   NUMERIC(4,3) CHECK (closing_sentiment BETWEEN 0 AND 1),
    min_sentiment       NUMERIC(4,3) CHECK (min_sentiment BETWEEN 0 AND 1),
    sentiment_score     NUMERIC(4,3) CHECK (sentiment_score BETWEEN 0 AND 1),   -- current/latest
    anger_spike_occurred BOOLEAN NOT NULL DEFAULT FALSE,

    -- Topics discussed in this session
    topics              TEXT[] NOT NULL DEFAULT '{}',
    topic_frequency     JSONB NOT NULL DEFAULT '{}',

    -- Performance
    total_messages      INTEGER NOT NULL DEFAULT 0,
    agent_turns         INTEGER NOT NULL DEFAULT 0,
    escalated           BOOLEAN NOT NULL DEFAULT FALSE,
    escalation_reason   escalation_reason,

    -- Timing
    first_message_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_message_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    session_timeout_at  TIMESTAMPTZ,   -- auto-close if no activity by this time
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE conversations IS 'One row per support session. Groups all messages in a session together. A customer may have multiple conversations over time.';
COMMENT ON COLUMN conversations.channel_journey IS 'Ordered array of channels used in this session, e.g. {whatsapp, email}.';
COMMENT ON COLUMN conversations.topic_frequency IS 'JSON map of topic -> count for this session, e.g. {"notifications": 2, "integration_slack": 1}.';
COMMENT ON COLUMN conversations.session_timeout_at IS 'If no activity by this time, conversation is auto-closed as abandoned.';


-- =============================================================
-- TABLE 4: messages
-- Every inbound and outbound message, across all channels.
-- Stores raw content, formatted content, tool call log,
-- delivery status, and performance telemetry.
-- =============================================================

CREATE TABLE messages (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id     UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    ticket_id           UUID,                               -- FK added after tickets table created (below)
    customer_id         UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,

    -- Direction & Channel
    direction           message_direction NOT NULL,
    channel             channel_type NOT NULL,
    channel_message_id  TEXT,                               -- external ID: Gmail message_id, Twilio SID, etc.
    thread_id           TEXT,                               -- Gmail thread_id for email threading

    -- Content
    raw_content         TEXT NOT NULL,                      -- original, unformatted
    formatted_content   TEXT,                               -- after channel formatter applied (outbound only)
    subject             TEXT,                               -- email subject line

    -- Delivery
    delivery_status     message_delivery_status NOT NULL DEFAULT 'pending',
    delivered_at        TIMESTAMPTZ,
    delivery_error      TEXT,

    -- Agent telemetry (outbound messages only)
    model_used          TEXT,                               -- e.g. gpt-4o
    tool_calls          JSONB NOT NULL DEFAULT '[]',        -- ordered list of tools called: [{name, input, output, duration_ms}]
    tool_call_count     INTEGER NOT NULL DEFAULT 0,
    kb_searches         INTEGER NOT NULL DEFAULT 0,         -- how many KB searches in this turn
    kb_search_successful BOOLEAN,                           -- did at least one KB search return results?
    escalated_in_turn   BOOLEAN NOT NULL DEFAULT FALSE,
    escalation_reason   escalation_reason,

    -- Sentiment (inbound messages)
    sentiment_score     NUMERIC(4,3) CHECK (sentiment_score BETWEEN 0 AND 1),
    topics_detected     TEXT[] NOT NULL DEFAULT '{}',

    -- Performance
    processing_started_at  TIMESTAMPTZ,
    processing_completed_at TIMESTAMPTZ,
    latency_ms          INTEGER,                            -- processing_completed - processing_started
    char_count          INTEGER,
    word_count          INTEGER,

    -- Metadata (channel-specific extras)
    metadata            JSONB NOT NULL DEFAULT '{}',

    -- Timestamps
    received_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_messages_latency CHECK (latency_ms IS NULL OR latency_ms >= 0)
);

COMMENT ON TABLE messages IS 'Every inbound and outbound message across all channels. The core event log of the system.';
COMMENT ON COLUMN messages.channel_message_id IS 'External system ID: Gmail message_id, Twilio MessageSid, or web form submission UUID.';
COMMENT ON COLUMN messages.tool_calls IS 'JSON array of tool invocations: [{name, input, output, duration_ms, success}]. Ordered by execution sequence.';
COMMENT ON COLUMN messages.latency_ms IS 'End-to-end agent processing time in ms. Target P95 < 3000ms.';
COMMENT ON COLUMN messages.metadata IS 'Channel-specific extras: email headers, Twilio profile data, web form fields.';


-- =============================================================
-- TABLE 5: tickets
-- One ticket per reported issue. May span multiple conversations
-- (customer comes back). Tracks lifecycle from open to resolved.
-- =============================================================

CREATE TABLE tickets (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticket_ref          TEXT UNIQUE NOT NULL,               -- human-readable: TKT-A3F8C012

    -- Ownership
    customer_id         UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    conversation_id     UUID REFERENCES conversations(id),
    assigned_to         TEXT,                               -- human agent email if escalated

    -- Classification
    source_channel      channel_type NOT NULL,
    priority            ticket_priority NOT NULL DEFAULT 'medium',
    category            ticket_category NOT NULL DEFAULT 'general',
    tags                TEXT[] NOT NULL DEFAULT '{}',

    -- Status lifecycle
    status              ticket_status NOT NULL DEFAULT 'open',
    escalated           BOOLEAN NOT NULL DEFAULT FALSE,
    escalation_reason   escalation_reason,
    escalation_id       TEXT,                               -- ESC-XXXXXX reference
    routed_to           TEXT,                               -- team email

    -- Content
    issue_summary       TEXT NOT NULL,                      -- 1-2 sentence AI-generated summary
    original_message    TEXT,                               -- verbatim first message from customer
    resolution          TEXT,                               -- final resolution description

    -- SLA tracking
    sla_deadline        TIMESTAMPTZ,
    first_response_at   TIMESTAMPTZ,
    first_response_ms   INTEGER,                            -- ms from created_at to first response

    -- Sentiment at time of ticket creation
    opening_sentiment   NUMERIC(4,3) CHECK (opening_sentiment BETWEEN 0 AND 1),

    -- Timestamps
    opened_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    escalated_at        TIMESTAMPTZ,
    resolved_at         TIMESTAMPTZ,
    closed_at           TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_ticket_ref_format CHECK (ticket_ref ~ '^TKT-[A-F0-9]{8}$')
);

COMMENT ON TABLE tickets IS 'One ticket per reported issue. Tracks the full lifecycle from open through resolution or escalation.';
COMMENT ON COLUMN tickets.ticket_ref IS 'Human-readable reference shown to customers: TKT-XXXXXXXX.';
COMMENT ON COLUMN tickets.routed_to IS 'Email of the team that received the escalation: billing@, legal@, bugs@, etc.';
COMMENT ON COLUMN tickets.first_response_ms IS 'Time from ticket creation to first AI response. Target < 30,000ms (30s).';

-- Add FK from messages → tickets (now that tickets exists)
ALTER TABLE messages ADD CONSTRAINT fk_messages_ticket
    FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE SET NULL;


-- =============================================================
-- TABLE 6: knowledge_base
-- Vector-searchable product documentation.
-- Each row is one KB article with a 1536-dim text-embedding-3-small
-- embedding for cosine similarity search via pgvector.
-- =============================================================

CREATE TABLE knowledge_base (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    kb_ref          TEXT UNIQUE NOT NULL,                   -- e.g. kb_password_reset
    category        TEXT NOT NULL,
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,

    -- pgvector embedding (1536 dims = text-embedding-3-small)
    embedding       VECTOR(1536),

    -- Content metadata
    word_count      INTEGER,
    last_updated    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Usage analytics
    search_hits     INTEGER NOT NULL DEFAULT 0,             -- how many times this entry was returned in search
    search_used     INTEGER NOT NULL DEFAULT 0,             -- how many times agent used this entry in a response
    helpful_votes   INTEGER NOT NULL DEFAULT 0,
    unhelpful_votes INTEGER NOT NULL DEFAULT 0,

    -- Versioning
    version         INTEGER NOT NULL DEFAULT 1,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    deprecated_at   TIMESTAMPTZ,
    deprecated_by   TEXT,                                   -- kb_ref of the entry that replaces this one

    -- Timestamps
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE knowledge_base IS 'Vector-searchable product documentation. Each row is one KB article with a text-embedding-3-small (1536-dim) embedding for pgvector cosine similarity search.';
COMMENT ON COLUMN knowledge_base.embedding IS '1536-dimensional vector from OpenAI text-embedding-3-small. Used for semantic search via ivfflat cosine similarity index.';
COMMENT ON COLUMN knowledge_base.search_hits IS 'Incremented each time this entry appears in search results (for relevance analytics).';
COMMENT ON COLUMN knowledge_base.search_used IS 'Incremented when the agent actually uses this entry in a response (vs. just retrieving it).';


-- =============================================================
-- TABLE 7: agent_metrics
-- Per-channel, per-day aggregated performance metrics.
-- Written by the metrics_collector worker after each interaction
-- and rolled up nightly for dashboard reporting.
-- =============================================================

CREATE TABLE agent_metrics (
    id                          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Dimensions
    metric_date                 DATE NOT NULL,
    channel                     channel_type NOT NULL,      -- per-channel breakdown

    -- Volume
    total_tickets               INTEGER NOT NULL DEFAULT 0,
    total_messages_inbound      INTEGER NOT NULL DEFAULT 0,
    total_messages_outbound     INTEGER NOT NULL DEFAULT 0,
    total_conversations         INTEGER NOT NULL DEFAULT 0,
    new_customers               INTEGER NOT NULL DEFAULT 0,
    returning_customers         INTEGER NOT NULL DEFAULT 0,

    -- Resolution
    resolved_by_ai              INTEGER NOT NULL DEFAULT 0,
    escalated_to_human          INTEGER NOT NULL DEFAULT 0,
    escalation_rate             NUMERIC(5,4),               -- escalated / total_tickets
    avg_turns_to_resolve        NUMERIC(6,2),

    -- Escalation breakdown
    escalations_pricing         INTEGER NOT NULL DEFAULT 0,
    escalations_refund          INTEGER NOT NULL DEFAULT 0,
    escalations_legal           INTEGER NOT NULL DEFAULT 0,
    escalations_angry           INTEGER NOT NULL DEFAULT 0,
    escalations_technical       INTEGER NOT NULL DEFAULT 0,
    escalations_human_requested INTEGER NOT NULL DEFAULT 0,
    escalations_other           INTEGER NOT NULL DEFAULT 0,

    -- Performance (latency)
    avg_latency_ms              NUMERIC(10,2),
    p50_latency_ms              INTEGER,
    p95_latency_ms              INTEGER,
    p99_latency_ms              INTEGER,
    max_latency_ms              INTEGER,
    sla_breaches                INTEGER NOT NULL DEFAULT 0,  -- tickets where first response > 30s

    -- Sentiment
    avg_sentiment_opening       NUMERIC(4,3),
    avg_sentiment_closing       NUMERIC(4,3),
    sentiment_improved_count    INTEGER NOT NULL DEFAULT 0,
    sentiment_worsened_count    INTEGER NOT NULL DEFAULT 0,
    anger_spike_count           INTEGER NOT NULL DEFAULT 0,

    -- Knowledge base
    total_kb_searches           INTEGER NOT NULL DEFAULT 0,
    kb_hit_rate                 NUMERIC(5,4),               -- searches_with_results / total_kb_searches
    kb_miss_escalations         INTEGER NOT NULL DEFAULT 0, -- escalations triggered by 2 kb misses

    -- Error tracking
    agent_errors                INTEGER NOT NULL DEFAULT 0,
    delivery_failures           INTEGER NOT NULL DEFAULT 0,

    -- Cross-channel
    cross_channel_sessions      INTEGER NOT NULL DEFAULT 0, -- sessions where customer switched channel

    -- Timestamps
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_metrics_date_channel UNIQUE (metric_date, channel)
);

COMMENT ON TABLE agent_metrics IS 'Per-channel, per-day aggregated KPIs. One row per (date, channel) pair. Written by metrics_collector worker. Used for dashboards and alerting.';
COMMENT ON COLUMN agent_metrics.escalation_rate IS 'Computed as escalated_to_human / total_tickets. Target < 0.20 (20%).';
COMMENT ON COLUMN agent_metrics.p95_latency_ms IS 'P95 end-to-end processing latency. Target < 3000ms.';
COMMENT ON COLUMN agent_metrics.kb_hit_rate IS 'Fraction of KB searches that returned at least one result. Low rate indicates KB gaps.';


-- =============================================================
-- INDEXES
-- =============================================================

-- ── Table 1: customers ───────────────────────────────────────

CREATE INDEX idx_customers_canonical_email
    ON customers (canonical_email)
    WHERE canonical_email IS NOT NULL;

CREATE INDEX idx_customers_canonical_phone
    ON customers (canonical_phone)
    WHERE canonical_phone IS NOT NULL;

CREATE INDEX idx_customers_plan_tier
    ON customers (plan_tier);

CREATE INDEX idx_customers_last_contact
    ON customers (last_contact_at DESC);

CREATE INDEX idx_customers_not_deleted
    ON customers (created_at)
    WHERE deleted_at IS NULL;


-- ── Table 2: customer_identifiers ────────────────────────────

CREATE INDEX idx_identifiers_customer_id
    ON customer_identifiers (customer_id);

-- Core lookup: "give me the customer_id for this email/phone"
CREATE INDEX idx_identifiers_value
    ON customer_identifiers (identifier_type, identifier_value);

CREATE INDEX idx_identifiers_channel
    ON customer_identifiers (channel);


-- ── Table 3: conversations ───────────────────────────────────

CREATE INDEX idx_conversations_customer_id
    ON conversations (customer_id);

CREATE INDEX idx_conversations_status
    ON conversations (status, last_message_at DESC);

CREATE INDEX idx_conversations_initial_channel
    ON conversations (initial_channel);

CREATE INDEX idx_conversations_active
    ON conversations (customer_id, last_message_at DESC)
    WHERE status = 'active';

-- Find conversations within last 24h for a customer (active session check)
CREATE INDEX idx_conversations_recent
    ON conversations (customer_id, created_at DESC)
    WHERE status IN ('active', 'resolved');


-- ── Table 4: messages ────────────────────────────────────────

CREATE INDEX idx_messages_conversation_id
    ON messages (conversation_id, received_at);

CREATE INDEX idx_messages_customer_id
    ON messages (customer_id, received_at DESC);

CREATE INDEX idx_messages_ticket_id
    ON messages (ticket_id)
    WHERE ticket_id IS NOT NULL;

CREATE INDEX idx_messages_channel
    ON messages (channel, direction, received_at DESC);

CREATE INDEX idx_messages_delivery_status
    ON messages (delivery_status)
    WHERE delivery_status IN ('pending', 'failed');

CREATE INDEX idx_messages_channel_message_id
    ON messages (channel, channel_message_id)
    WHERE channel_message_id IS NOT NULL;

-- For latency analytics queries
CREATE INDEX idx_messages_latency
    ON messages (channel, latency_ms)
    WHERE direction = 'outbound' AND latency_ms IS NOT NULL;


-- ── Table 5: tickets ─────────────────────────────────────────

CREATE INDEX idx_tickets_customer_id
    ON tickets (customer_id, opened_at DESC);

CREATE INDEX idx_tickets_status
    ON tickets (status, priority, opened_at);

CREATE INDEX idx_tickets_ref
    ON tickets (ticket_ref);

CREATE INDEX idx_tickets_source_channel
    ON tickets (source_channel, status);

CREATE INDEX idx_tickets_escalated
    ON tickets (escalated, escalation_reason)
    WHERE escalated = TRUE;

CREATE INDEX idx_tickets_open
    ON tickets (opened_at DESC)
    WHERE status IN ('open', 'in_progress');

-- For SLA monitoring (find tickets approaching SLA deadline)
CREATE INDEX idx_tickets_sla
    ON tickets (sla_deadline)
    WHERE status IN ('open', 'in_progress') AND sla_deadline IS NOT NULL;


-- ── Table 6: knowledge_base (CRITICAL INDEX) ─────────────────

-- REQUIRED: pgvector cosine similarity search (constitution mandated)
CREATE INDEX idx_knowledge_embedding
    ON knowledge_base
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- Category filter (used with vector search for scoped queries)
CREATE INDEX idx_knowledge_category
    ON knowledge_base (category, is_active);

-- Full-text search fallback (when embedding not yet generated)
CREATE INDEX idx_knowledge_content_fts
    ON knowledge_base
    USING gin(to_tsvector('english', title || ' ' || content));

-- Analytics
CREATE INDEX idx_knowledge_hits
    ON knowledge_base (search_hits DESC)
    WHERE is_active = TRUE;


-- ── Table 7: agent_metrics ───────────────────────────────────

CREATE INDEX idx_metrics_date
    ON agent_metrics (metric_date DESC, channel);

CREATE INDEX idx_metrics_channel
    ON agent_metrics (channel, metric_date DESC);


-- =============================================================
-- UPDATED_AT TRIGGER
-- Automatically update updated_at on row modification.
-- =============================================================

CREATE OR REPLACE FUNCTION trigger_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER set_updated_at_customers
    BEFORE UPDATE ON customers
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER set_updated_at_conversations
    BEFORE UPDATE ON conversations
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER set_updated_at_tickets
    BEFORE UPDATE ON tickets
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER set_updated_at_knowledge_base
    BEFORE UPDATE ON knowledge_base
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER set_updated_at_agent_metrics
    BEFORE UPDATE ON agent_metrics
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();


-- =============================================================
-- TICKET REF GENERATOR
-- Auto-generate TKT-XXXXXXXX on ticket insert.
-- =============================================================

CREATE OR REPLACE FUNCTION generate_ticket_ref()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.ticket_ref IS NULL OR NEW.ticket_ref = '' THEN
        NEW.ticket_ref := 'TKT-' || UPPER(SUBSTRING(REPLACE(uuid_generate_v4()::TEXT, '-', ''), 1, 8));
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER auto_ticket_ref
    BEFORE INSERT ON tickets
    FOR EACH ROW EXECUTE FUNCTION generate_ticket_ref();


-- =============================================================
-- CUSTOMER STATS UPDATER
-- Keeps customers aggregated columns in sync after each ticket/message.
-- =============================================================

CREATE OR REPLACE FUNCTION update_customer_last_contact()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE customers
    SET
        last_contact_at  = NOW(),
        total_messages   = total_messages + 1,
        updated_at       = NOW()
    WHERE id = NEW.customer_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_update_customer_on_message
    AFTER INSERT ON messages
    FOR EACH ROW EXECUTE FUNCTION update_customer_last_contact();


-- =============================================================
-- INITIAL KNOWLEDGE BASE SEED
-- Seed data for all 23 KB entries from incubation.
-- Embeddings are NULL initially — populate via:
--   python production/database/seed_embeddings.py
-- =============================================================

INSERT INTO knowledge_base (kb_ref, category, title, content, is_active) VALUES

('kb_password_reset', 'authentication', 'Password Reset',
'To reset your password: 1) Go to app.techcorp.io/login 2) Click Forgot Password 3) Enter your email and check inbox 4) Link expires in 24 hours. No email? Check spam, whitelist noreply@techcorp.io. Contact support if missing after 5 min.',
TRUE),

('kb_2fa', 'authentication', 'Two-Factor Authentication',
'Enable 2FA: Settings → Security → Two-Factor Authentication. Supports TOTP (Google Authenticator, Authy) and SMS. Save backup codes immediately. Locked out: use backup codes or contact support for manual verification.',
TRUE),

('kb_team_member', 'team_management', 'Adding Team Members',
'Add member: Settings → Team → Invite Member → enter email + role. Expires 7 days. Resend: Settings → Team → Pending Invites. Roles: Owner (full), Admin (team/settings), Member (projects), Guest (limited). Can''t see projects? Check project-level permissions in project Settings tab.',
TRUE),

('kb_slack', 'integrations', 'Slack Integration',
'Slack requires Growth plan or above (not available on Starter). Setup: Settings → Integrations → Slack → Connect → OAuth → select channel → configure events. Known: 2-5 min delay at peak hours. Not working? Disconnect and reconnect.',
TRUE),

('kb_gantt_pdf', 'known_issues', 'Gantt PDF Export Bug',
'KNOWN ISSUE: Gantt PDF export fails intermittently with error 500. Fix is in v3.2.1, releasing next Tuesday. Workaround: export to CSV — same data, opens in Excel/Sheets.',
TRUE),

('kb_plans', 'plans', 'Plan Features and Limits',
'Starter ($29/mo): 5 users, 5GB, no integrations, email support. Growth ($79/mo): 20 users, 50GB, all integrations, WhatsApp support. Business ($199/mo): 100 users, SSO, audit logs, API 1K req/hr. Enterprise (custom): unlimited, on-prem option, API 10K req/hr, custom SLA. Upgrades: immediate. Downgrades: next billing cycle.',
TRUE),

('kb_storage', 'plans', 'Storage Limits',
'At storage limit: new uploads blocked, existing files unaffected. Free space: Settings → Storage → delete unused files. Upgrade for more: Growth=50GB, Business=100GB, Enterprise=unlimited. Billing: app.techcorp.io/settings/billing.',
TRUE),

('kb_billing', 'billing', 'Billing and Subscriptions',
'Billing portal: app.techcorp.io/settings/billing — view invoices, update payment, change plan. Refunds and invoice adjustments: NOT handled by AI — billing team at billing@techcorp.io. Upgrades: immediate. Cancellations: active until period end, then account frozen.',
TRUE),

('kb_data_retention', 'account', 'Data Retention on Cancellation',
'After cancel: active until period end → archived 30 days → permanently deleted. Export first: Settings → Export → Download Everything (CSV/JSON). Resubscribe within 30 days: full restore. After 30 days: unrecoverable.',
TRUE),

('kb_notifications', 'features', 'Notification Settings',
'Manage: Settings → Notifications. Types: in-app, email, Slack, MS Teams. Not working? Check: notifications enabled in Settings, browser permissions (in-app), and Settings → Integrations for Slack/Teams connectivity.',
TRUE),

('kb_export', 'features', 'Export Options',
'CSV: Reports → select → Export → CSV. Excel: same → Excel (Gantt PDF has known bug). Full account export: Settings → Export → Download Everything.',
TRUE),

('kb_github', 'integrations', 'GitHub PR Linking',
'GitHub (Growth+): Settings → Integrations → GitHub. Link PR to task: include [TASK-ID] or #TASK-ID in PR title/description. Example: Fix login bug [TASK-123]. Appears in task Activity within 2-3 min. Ensure correct repository selected.',
TRUE),

('kb_sso', 'security', 'SSO SAML Setup (Business+)',
'SSO: Business plan+. Settings → Security → Single Sign-On. ACS URL: https://app.techcorp.io/auth/saml/callback (no trailing slash). Entity ID: https://app.techcorp.io/saml/metadata. Azure AD Reply URL = ACS URL. Common error: mismatched URL or trailing slash.',
TRUE),

('kb_api', 'developers', 'API and Webhooks',
'API: Business+. Docs: docs.techcorp.io/api. Token: Settings → API → New Token. Rate limits: Business=1,000/hr, Enterprise=10,000/hr. Webhooks: Settings → Webhooks → Add Endpoint. Signature: HMAC-SHA256. Use raw request body bytes — do not decode before hashing.',
TRUE),

('kb_calendar', 'features', 'Calendar View — Tasks Not Showing',
'Tasks need a due date to appear in Calendar view. Check filters (top-right) — they may hide tasks. Verify date range navigation. Ensure project is not archived.',
TRUE),

('kb_onboarding', 'onboarding', 'Getting Started',
'Quick start: 1) New Project → + New Project. 2) + Add Task → set due dates + assign. 3) Settings → Team → Invite Member. 4) Views: List, Kanban, Calendar, Gantt. Tutorials: techcorp.io/support.',
TRUE),

('kb_security', 'security', 'Security and Compliance',
'Certifications: SOC 2 Type II, GDPR, CCPA. AES-256 at rest, TLS 1.3 in transit. SOC 2 reports / DPA / pen-test results: sales@techcorp.io (NDA required). GDPR data deletion requests: legal@techcorp.io.',
TRUE),

('kb_mobile', 'features', 'Mobile App',
'iOS/Android app is in beta. Join: techcorp.io/beta. No confirmed general release date — users notified at launch.',
TRUE),

('kb_offline', 'features', 'Offline Mode',
'Offline mode is under consideration — not currently available. TechCorp requires an internet connection. No confirmed timeline.',
TRUE),

('kb_zapier', 'integrations', 'Zapier Integration',
'Zapier (Growth+): Settings → Integrations → Zapier. Webhook fires on manual test but not live events? Check event filter settings, verify endpoint URL, reconnect and re-select trigger events.',
TRUE),

('kb_ms_teams', 'integrations', 'Microsoft Teams Integration',
'MS Teams (Growth+): Settings → Integrations → Microsoft 365 → Teams. Not posting? Verify: Connected status, correct channel selected, events checked. Try disconnect → reconnect.',
TRUE),

('kb_workspaces', 'account', 'Multiple Workspaces',
'One workspace per account. For isolation: use separate projects + roles/permissions, or create separate accounts. Multi-workspace support: roadmap, no date.',
TRUE),

('kb_templates', 'features', 'Project Templates',
'40+ built-in templates: New Project → Browse Templates. Workaround for custom templates: build ideal project → use Duplicate Project. Custom template creation from scratch: roadmap, no release date.',
TRUE);


-- =============================================================
-- GRANT PERMISSIONS (adjust role name as needed)
-- =============================================================

-- GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO fte_app;
-- GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO fte_app;


-- =============================================================
-- SCHEMA COMPLETE
-- =============================================================
-- To apply:
--   psql -U postgres -d customer_success_fte -f production/database/schema.sql
--
-- To enable pgvector (once, as superuser):
--   CREATE EXTENSION IF NOT EXISTS vector;
--
-- To seed embeddings after applying schema:
--   python production/database/seed_embeddings.py
-- =============================================================
