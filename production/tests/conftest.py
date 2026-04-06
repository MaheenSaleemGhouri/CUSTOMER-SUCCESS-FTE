"""
production/tests/conftest.py
Shared pytest fixtures for all test modules.

Provides:
  - mock_db          : AsyncMock asyncpg connection
  - mock_pool        : AsyncMock asyncpg pool (context-manager compatible)
  - mock_producer    : AsyncMock Kafka producer
  - mock_openai      : AsyncMock OpenAI client
  - sample_customer  : dict matching asyncpg row format
  - sample_ticket    : dict matching asyncpg row format
  - sample_escalation: dict matching asyncpg row format
  - fastapi_client   : async TestClient for the FastAPI app
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# ─────────────────────────────────────────────────────────────
# FIXED TEST IDs
# ─────────────────────────────────────────────────────────────

CUSTOMER_ID    = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
TICKET_ID      = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")
CONV_ID        = uuid.UUID("cccccccc-0000-0000-0000-000000000003")
ESCALATION_ID  = uuid.UUID("dddddddd-0000-0000-0000-000000000004")
NOW            = datetime(2026, 3, 9, 10, 0, 0, tzinfo=timezone.utc)


# ─────────────────────────────────────────────────────────────
# SAMPLE DATA
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def sample_customer() -> dict:
    return {
        "id":              CUSTOMER_ID,
        "display_name":    "Alice Smith",
        "canonical_email": "alice@example.com",
        "plan":            "growth",
        "sentiment_score": 0.65,
        "last_contact_at": NOW,
        "created_at":      NOW,
        "updated_at":      NOW,
    }


@pytest.fixture
def sample_ticket() -> dict:
    return {
        "id":               TICKET_ID,
        "ticket_ref":       "TKT-ABCD1234",
        "customer_id":      CUSTOMER_ID,
        "conversation_id":  CONV_ID,
        "source_channel":   "email",
        "status":           "open",
        "priority":         "medium",
        "category":         "technical",
        "issue_summary":    "Slack integration not connecting",
        "original_message": "Hi, my Slack integration keeps failing.",
        "created_at":       NOW,
        "updated_at":       NOW,
        "resolved_at":      None,
    }


@pytest.fixture
def sample_escalation() -> dict:
    return {
        "id":            ESCALATION_ID,
        "ticket_id":     TICKET_ID,
        "customer_id":   CUSTOMER_ID,
        "reason":        "technical_tier2",
        "urgency":       "high",
        "routing_team":  "Engineering",
        "routing_email": "bugs@techcorp.io",
        "notes":         "Data loss reported",
        "created_at":    NOW,
    }


@pytest.fixture
def sample_kb_row() -> dict:
    return {
        "id":         uuid.uuid4(),
        "title":      "How to connect Slack integration",
        "content":    "Go to Settings → Integrations → Slack and click Connect.",
        "category":   "integrations",
        "similarity": 0.92,
        "rank":       0.85,
    }


# ─────────────────────────────────────────────────────────────
# MOCK DB
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def mock_db(sample_customer, sample_ticket, sample_escalation):
    """AsyncMock asyncpg connection with sensible defaults."""
    db = AsyncMock()
    db.fetchrow.return_value  = sample_customer
    db.fetch.return_value     = [sample_customer]
    db.fetchval.return_value  = 1
    db.execute.return_value   = "INSERT 0 1"
    return db


@pytest.fixture
def mock_pool(mock_db):
    """AsyncMock asyncpg Pool — supports `async with pool.acquire() as conn`."""
    pool = AsyncMock()
    acquire_cm = MagicMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=mock_db)
    acquire_cm.__aexit__  = AsyncMock(return_value=False)
    pool.acquire.return_value = acquire_cm
    return pool


# ─────────────────────────────────────────────────────────────
# MOCK KAFKA PRODUCER
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def mock_producer():
    producer = AsyncMock()
    producer.send_and_wait = AsyncMock(return_value=None)
    return producer


# ─────────────────────────────────────────────────────────────
# MOCK OPENAI
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def mock_openai():
    client = AsyncMock()
    emb_data        = MagicMock()
    emb_data.embedding = [0.1] * 1536
    emb_response    = MagicMock()
    emb_response.data = [emb_data]
    client.embeddings.create = AsyncMock(return_value=emb_response)
    return client


# ─────────────────────────────────────────────────────────────
# FASTAPI TEST CLIENT
# ─────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def fastapi_client(mock_pool, mock_producer):
    """Async HTTPX client wired to the FastAPI app with mocked state."""
    from production.api.main import app

    app.state.db_pool        = mock_pool
    app.state.kafka_producer = mock_producer
    app.state.kafka_client   = AsyncMock()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client
