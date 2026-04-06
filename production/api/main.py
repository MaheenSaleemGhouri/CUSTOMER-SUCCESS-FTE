"""
production/api/main.py
FastAPI application — Customer Success FTE platform.

All 13 endpoints:
  ① POST   /support/submit                — web form submission (→ Kafka)
  ② GET    /support/ticket/{ticket_id}    — ticket status + message history
  ③ POST   /webhooks/gmail               — Gmail Pub/Sub push notification
  ④ POST   /webhooks/whatsapp            — Twilio inbound WhatsApp message
  ⑤ GET    /webhooks/whatsapp            — Twilio webhook verification challenge
  ⑥ GET    /health                       — Kafka + DB + OpenAI liveness check
  ⑦ GET    /metrics/daily               — per-channel daily analytics
  ⑧ GET    /customers/{customer_id}      — customer profile + ticket history
  ⑨ POST   /tickets/{ticket_id}/escalate — manual escalation trigger
  ⑩ GET    /dashboard/stats             — summary counts (tickets, KB, sentiment)
  ⑪ GET    /dashboard/tickets           — paginated ticket list with filters
  ⑫ GET    /dashboard/kb               — knowledge base article list
  ⑬ WS    /ws/ticket/{ticket_id}        — WebSocket real-time ticket updates

Startup (lifespan):
  - asyncpg connection pool  (DB_POOL_MIN / DB_POOL_MAX)
  - AIOKafkaProducer          (KAFKA_BOOTSTRAP_SERVERS)
  - KafkaClient.create_topics() — idempotent topic provisioning

Middleware:
  - CORSMiddleware            (CORS_ORIGINS env var, default * in dev)
  - GZipMiddleware            (min_size=1000 bytes)

Environment variables:
  DATABASE_URL               asyncpg DSN
  OPENAI_API_KEY             OpenAI key (for health check ping)
  KAFKA_BOOTSTRAP_SERVERS    default: localhost:9092
  CORS_ORIGINS               comma-separated list, default: * (dev only)
  DB_POOL_MIN                default: 2
  DB_POOL_MAX                default: 10
  GMAIL_CREDENTIALS_FILE     path to Gmail service-account JSON
  TWILIO_ACCOUNT_SID         Twilio credentials
  TWILIO_AUTH_TOKEN
  TWILIO_WHATSAPP_FROM       e.g. whatsapp:+14155238886
"""

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (C:/Hackathon 5/.env)
load_dotenv(Path(__file__).parents[2] / ".env")
from datetime import datetime, timezone
from typing import Optional

import asyncio
import asyncpg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# RATE LIMITER  (Option D)
# ─────────────────────────────────────────────────────────────

import time
import collections

_RATE_STORE: dict[str, collections.deque] = {}   # ip → deque of timestamps
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "30"))   # per window
RATE_LIMIT_WINDOW   = int(os.getenv("RATE_LIMIT_WINDOW_S", "60"))   # seconds
ADMIN_API_KEY       = os.getenv("ADMIN_API_KEY", "")                 # dashboard protection


def _is_rate_limited(ip: str) -> bool:
    now    = time.time()
    window = now - RATE_LIMIT_WINDOW
    dq     = _RATE_STORE.setdefault(ip, collections.deque())
    # Evict timestamps outside the window
    while dq and dq[0] < window:
        dq.popleft()
    if len(dq) >= RATE_LIMIT_REQUESTS:
        return True
    dq.append(now)
    return False


async def rate_limit_check(request: Request):
    """FastAPI dependency — raises 429 if client exceeds rate limit."""
    client_ip = request.client.host if request.client else "unknown"
    if _is_rate_limited(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded: {RATE_LIMIT_REQUESTS} requests per {RATE_LIMIT_WINDOW}s",
            headers={"Retry-After": str(RATE_LIMIT_WINDOW)},
        )


async def require_admin_key(request: Request):
    """FastAPI dependency — requires X-Admin-Key header for dashboard endpoints."""
    if not ADMIN_API_KEY:
        return  # No key configured → allow (dev mode)
    provided = request.headers.get("X-Admin-Key", "")
    if provided != ADMIN_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-Admin-Key header",
        )

# ─────────────────────────────────────────────────────────────
# WEBSOCKET CONNECTION MANAGER
# ─────────────────────────────────────────────────────────────

class _ConnectionManager:
    """Tracks active WebSocket connections per ticket_id."""

    def __init__(self):
        # ticket_id (str) → set of WebSocket connections
        self._connections: dict[str, set[WebSocket]] = {}

    async def connect(self, ticket_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.setdefault(ticket_id, set()).add(ws)

    def disconnect(self, ticket_id: str, ws: WebSocket) -> None:
        sockets = self._connections.get(ticket_id, set())
        sockets.discard(ws)
        if not sockets:
            self._connections.pop(ticket_id, None)

    async def broadcast(self, ticket_id: str, data: dict) -> None:
        """Send JSON to all connections watching this ticket."""
        sockets = list(self._connections.get(ticket_id, set()))
        dead = []
        for ws in sockets:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ticket_id, ws)

    def active_count(self) -> int:
        return sum(len(s) for s in self._connections.values())


ws_manager = _ConnectionManager()

# ─────────────────────────────────────────────────────────────
# ENVIRONMENT
# ─────────────────────────────────────────────────────────────

DATABASE_URL    = os.getenv("DATABASE_URL",            "postgresql://localhost/techcorp")
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY",          "")
CORS_ORIGINS    = os.getenv("CORS_ORIGINS",            "*").split(",")
DB_POOL_MIN     = int(os.getenv("DB_POOL_MIN",         "2"))
DB_POOL_MAX     = int(os.getenv("DB_POOL_MAX",         "10"))

# ─────────────────────────────────────────────────────────────
# LIFESPAN
# ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: provision topics → connect DB pool → start Kafka producer.

    Kafka failures are non-fatal — the API still starts and serves HTTP/WS
    endpoints; only submission/webhook routes that need the producer will fail.
    """
    from production.kafka_client import KafkaClient

    kafka_client = KafkaClient()
    producer     = None

    # ── Kafka (best-effort) ───────────────────────────────────
    try:
        logger.info("Provisioning Kafka topics …")
        await kafka_client.create_topics()
        logger.info("Starting Kafka producer …")
        producer = await kafka_client.producer()
        logger.info("Kafka ready.")
    except Exception as exc:
        logger.warning("Kafka unavailable — running in degraded mode (no Kafka): %s", exc)

    # ── PostgreSQL (required) ─────────────────────────────────
    logger.info("Connecting to PostgreSQL …")
    db_pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=DB_POOL_MIN,
        max_size=DB_POOL_MAX,
    )

    app.state.kafka_client   = kafka_client
    app.state.kafka_producer = producer   # may be None in degraded mode
    app.state.db_pool        = db_pool

    logger.info("Customer Success FTE API ready.")
    yield

    # ── Shutdown ─────────────────────────────────────────────
    logger.info("Shutting down …")
    try:
        await kafka_client.close()
    except Exception:
        pass
    await db_pool.close()
    logger.info("Shutdown complete.")


# ─────────────────────────────────────────────────────────────
# APP
# ─────────────────────────────────────────────────────────────

app = FastAPI(
    title="Customer Success FTE — API",
    description=(
        "24/7 AI-powered customer support platform for TechCorp. "
        "Handles Email, WhatsApp, and Web Form channels."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # open for dev; tighten via CORS_ORIGINS in prod
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)

# ── Rate-limit middleware — pure ASGI, does NOT break WebSockets ──
from starlette.responses import JSONResponse as StarletteJSON
from starlette.types import ASGIApp, Receive, Scope, Send

class RateLimitMiddleware:
    """Pure ASGI middleware — skips WebSocket scopes so WS connections work."""
    RATE_LIMITED_PATHS = {"/support/submit"}

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Only check HTTP requests (not websocket / lifespan)
        if scope["type"] == "http" and scope.get("path") in self.RATE_LIMITED_PATHS:
            client = scope.get("client")
            client_ip = client[0] if client else "unknown"
            if _is_rate_limited(client_ip):
                response = StarletteJSON(
                    {"detail": f"Rate limit exceeded: {RATE_LIMIT_REQUESTS} req/{RATE_LIMIT_WINDOW}s"},
                    status_code=429,
                    headers={"Retry-After": str(RATE_LIMIT_WINDOW)},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)

app.add_middleware(RateLimitMiddleware)

# ─────────────────────────────────────────────────────────────
# MOUNT ROUTERS
# ─────────────────────────────────────────────────────────────

from production.channels.web_form_handler import router as web_form_router

app.include_router(web_form_router)          # ① POST /support/submit
                                             # ② GET  /support/ticket/{ticket_id}

# ─────────────────────────────────────────────────────────────
# SHARED DEPENDENCIES
# ─────────────────────────────────────────────────────────────

async def get_db(request: Request):
    """Yield an asyncpg connection from the pool."""
    async with request.app.state.db_pool.acquire() as conn:
        yield conn


async def get_producer(request: Request):
    """Return the shared Kafka producer; raises 503 if Kafka is unavailable."""
    producer = request.app.state.kafka_producer
    if producer is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Kafka unavailable — message broker is not connected.",
        )
    return producer


# ─────────────────────────────────────────────────────────────
# PYDANTIC — request/response models for inline endpoints
# ─────────────────────────────────────────────────────────────

class ManualEscalationRequest(BaseModel):
    reason:  str           = Field(..., description="Escalation reason tag")
    urgency: str           = Field("normal", description="normal | high | critical")
    notes:   Optional[str] = Field(None,    description="Internal notes for routing team")


class ManualEscalationResponse(BaseModel):
    escalation_id:  str
    ticket_ref:     str
    team:           str
    routing_email:  str
    sla:            str
    escalated_at:   str


class CustomerProfileResponse(BaseModel):
    customer_id:   str
    display_name:  Optional[str]
    plan:          Optional[str]
    channels_used: list[str]
    total_tickets: int
    open_tickets:  int
    last_contact:  Optional[str]
    sentiment_avg: Optional[float]
    tickets:       list[dict]


class DailyMetricsResponse(BaseModel):
    date:     str
    channel:  Optional[str]
    metrics:  dict


# ─────────────────────────────────────────────────────────────
# ③ POST /webhooks/gmail
# ─────────────────────────────────────────────────────────────

@app.post(
    "/webhooks/gmail",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Gmail Pub/Sub push notification",
    tags=["webhooks"],
)
async def gmail_webhook(
    request:  Request,
    producer = Depends(get_producer),
    db       = Depends(get_db),
):
    """
    Receives Gmail Pub/Sub push notifications.
    Decodes the base64 message data, fetches the full email via Gmail API,
    normalises it, and publishes to fte.channels.email.inbound.

    Google sends a POST with:
      { "message": { "data": "<base64>", "messageId": "...", "publishTime": "..." },
        "subscription": "..." }
    """
    from production.channels.gmail_handler import GmailHandler
    from production.kafka_client import TOPIC_EMAIL_INBOUND

    body = await request.json()
    pubsub_message = body.get("message", {})

    if not pubsub_message:
        logger.warning("Gmail webhook: empty Pub/Sub message")
        return

    try:
        handler        = GmailHandler()
        history_id     = await handler.process_notification(pubsub_message)

        if not history_id:
            return  # Not an email notification we need to process

        # Fetch new messages since last history ID
        messages = await handler.get_new_messages(history_id)

        for msg in messages:
            # Resolve customer from DB before publishing
            from production.database.queries import get_customer_by_email, create_customer

            customer = await get_customer_by_email(db, msg["customer_email"])
            if not customer:
                customer = await create_customer(
                    db,
                    canonical_email=msg["customer_email"],
                    display_name=msg.get("customer_name", msg["customer_email"]),
                    first_channel="email",
                )

            payload = {**msg, "customer_id": str(customer["id"])}
            await producer.send_and_wait(
                TOPIC_EMAIL_INBOUND,
                value=payload,
                key=str(customer["id"]).encode("utf-8"),
            )
            logger.info("Email queued | from=%s | subject=%s", msg["customer_email"], msg.get("subject", ""))

    except Exception as e:
        logger.error("Gmail webhook processing failed | error=%s", e)
        # Return 204 regardless — Pub/Sub retries on non-2xx
    # 204 No Content — tells Pub/Sub the message was acknowledged


# ─────────────────────────────────────────────────────────────
# ④ POST /webhooks/whatsapp  — inbound message
# ─────────────────────────────────────────────────────────────

@app.post(
    "/webhooks/whatsapp",
    status_code=status.HTTP_200_OK,
    summary="Twilio WhatsApp inbound message",
    tags=["webhooks"],
)
async def whatsapp_webhook_post(
    request:   Request,
    producer  = Depends(get_producer),
    db        = Depends(get_db),
    x_twilio_signature: Optional[str] = Header(None),
):
    """
    Receives inbound WhatsApp messages from Twilio.
    Validates the X-Twilio-Signature HMAC, normalises the payload,
    and publishes to fte.channels.whatsapp.inbound.

    Twilio sends application/x-www-form-urlencoded.
    Returns plain "OK" (Twilio expects a 200 with no TwiML for async flows).
    """
    from production.channels.whatsapp_handler import WhatsAppHandler
    from production.kafka_client import TOPIC_WHATSAPP_INBOUND
    from production.database.queries import get_customer_by_identifier, create_customer

    handler = WhatsAppHandler()

    # Validate signature
    body_bytes = await request.body()
    url        = str(request.url)

    if not handler.validate_webhook(url, dict(await request.form()), x_twilio_signature or ""):
        logger.warning("WhatsApp webhook: invalid Twilio signature | url=%s", url)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signature")

    form_data = dict(await request.form())
    msg       = await handler.process_webhook(form_data)

    if not msg:
        return "OK"  # Status callback or unsupported message type

    # Resolve customer
    customer = await get_customer_by_identifier(db, "phone", msg["customer_phone"])
    if not customer:
        customer = await create_customer(
            db,
            canonical_email=None,
            display_name=msg.get("metadata", {}).get("profile_name") or msg["customer_phone"],
            first_channel="whatsapp",
        )

    payload = {**msg, "customer_id": str(customer["id"])}
    await producer.send_and_wait(
        TOPIC_WHATSAPP_INBOUND,
        value=payload,
        key=str(customer["id"]).encode("utf-8"),
    )
    logger.info("WhatsApp message queued | from=%s", msg["customer_phone"])
    return "OK"


# ─────────────────────────────────────────────────────────────
# ⑤ GET /webhooks/whatsapp  — Twilio verification challenge
# ─────────────────────────────────────────────────────────────

@app.get(
    "/webhooks/whatsapp",
    status_code=status.HTTP_200_OK,
    summary="Twilio webhook verification",
    tags=["webhooks"],
    include_in_schema=False,   # internal / not user-facing
)
async def whatsapp_webhook_get():
    """
    Twilio may send a GET request when first configuring the webhook URL.
    Responding 200 confirms the endpoint is reachable.
    """
    return {"status": "ok", "service": "Customer Success FTE — WhatsApp Webhook"}


# ─────────────────────────────────────────────────────────────
# ⑥ GET /health
# ─────────────────────────────────────────────────────────────

@app.get(
    "/health",
    summary="Platform liveness check",
    tags=["ops"],
)
async def health_check(request: Request):
    """
    Returns the health status of all platform dependencies:
      - PostgreSQL (asyncpg pool ping)
      - Kafka (broker connectivity + topic presence)
      - OpenAI (API key configured check)

    Returns HTTP 200 if all green, 503 if any dependency is unhealthy.
    Used by Kubernetes liveness and readiness probes.
    """
    results: dict = {}
    overall_ok    = True

    # DB
    try:
        async with request.app.state.db_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        results["database"] = {"status": "ok"}
    except Exception as e:
        results["database"] = {"status": "error", "error": str(e)}
        overall_ok = False

    # Kafka
    try:
        kafka_health = await request.app.state.kafka_client.health_check()
        results["kafka"] = kafka_health
        if kafka_health["status"] != "ok":
            overall_ok = False
    except Exception as e:
        results["kafka"] = {"status": "error", "error": str(e)}
        overall_ok = False

    # OpenAI
    results["openai"] = {
        "status": "ok" if OPENAI_API_KEY else "error",
        "key_configured": bool(OPENAI_API_KEY),
    }
    if not OPENAI_API_KEY:
        overall_ok = False

    http_status = status.HTTP_200_OK if overall_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(
        content={
            "status":     "ok" if overall_ok else "degraded",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "components": results,
        },
        status_code=http_status,
    )


# ─────────────────────────────────────────────────────────────
# ⑦ GET /metrics/daily
# ─────────────────────────────────────────────────────────────

@app.get(
    "/metrics/daily",
    response_model=DailyMetricsResponse,
    summary="Daily per-channel analytics",
    tags=["analytics"],
)
async def get_daily_metrics(
    date:    Optional[str] = Query(None, description="ISO date YYYY-MM-DD, default: today"),
    channel: Optional[str] = Query(None, description="email | whatsapp | web_form | all"),
    db      = Depends(get_db),
):
    """
    Return per-channel daily metrics for the given date.
    Metrics include: messages_processed, escalations, low_sentiment,
    avg_response_time_s, tickets_resolved.

    Used by the management dashboard and Grafana.
    """
    from production.database.queries import get_channel_metrics

    target_date = date or datetime.now(timezone.utc).date().isoformat()

    try:
        rows = await get_channel_metrics(db, target_date, channel=channel)
        metrics_dict: dict = {}
        for row in rows:
            ch = row["channel"]
            metrics_dict[ch] = {
                "total_tickets":          row["total_tickets"],
                "resolved_by_ai":         row["resolved_by_ai"],
                "escalated_to_human":     row["escalated_to_human"],
                "avg_escalation_rate":    float(row["avg_escalation_rate"] or 0),
                "avg_latency_ms":         int(row["avg_latency_ms"] or 0),
                "p95_latency_ms":         int(row["p95_latency_ms"] or 0),
                "sla_breaches":           row["sla_breaches"],
                "cross_channel_sessions": row["cross_channel_sessions"],
            }

        return DailyMetricsResponse(
            date=target_date,
            channel=channel,
            metrics=metrics_dict,
        )
    except Exception as e:
        logger.error("get_daily_metrics failed | date=%s | error=%s", target_date, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve metrics.",
        )


# ─────────────────────────────────────────────────────────────
# ⑧ GET /customers/{customer_id}
# ─────────────────────────────────────────────────────────────

@app.get(
    "/customers/{customer_id}",
    response_model=CustomerProfileResponse,
    summary="Customer profile and ticket history",
    tags=["customers"],
)
async def get_customer_profile(
    customer_id: str,
    ticket_limit: int = Query(10, ge=1, le=50, description="Max tickets to return"),
    db = Depends(get_db),
):
    """
    Return a full customer profile including:
      - Display name, plan tier, channels used
      - Ticket counts (total / open)
      - Last contact timestamp
      - Average sentiment score
      - Recent ticket list

    Used by the agent dashboard and human escalation UI.
    """
    import uuid as _uuid
    from production.database.queries import (
        get_customer_by_id,
        get_customer_ticket_counts,
        get_customer_tickets,
        get_customer_identifiers,
    )

    try:
        cust_uuid = _uuid.UUID(customer_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid customer_id format")

    customer = await get_customer_by_id(db, cust_uuid)
    if not customer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Customer {customer_id} not found")

    counts      = await get_customer_ticket_counts(db, cust_uuid)
    tickets     = await get_customer_tickets(db, cust_uuid, limit=ticket_limit)
    identifiers = await get_customer_identifiers(db, cust_uuid)

    channels_used = list({i["channel"] for i in (identifiers or [])})
    ticket_list   = [
        {
            "ticket_id":  str(t["id"]),
            "ticket_ref": t["ticket_ref"],
            "status":     t["status"],
            "priority":   t["priority"],
            "category":   t["category"],
            "summary":    t["issue_summary"],
            "created_at": t["created_at"].isoformat(),
        }
        for t in (tickets or [])
    ]

    last_contact = customer.get("last_contact_at")

    return CustomerProfileResponse(
        customer_id=str(customer["id"]),
        display_name=customer.get("display_name"),
        plan=customer.get("plan"),
        channels_used=channels_used,
        total_tickets=counts.get("total", 0),
        open_tickets=counts.get("open", 0),
        last_contact=last_contact.isoformat() if last_contact else None,
        sentiment_avg=customer.get("sentiment_score"),
        tickets=ticket_list,
    )


# ─────────────────────────────────────────────────────────────
# ⑨ POST /tickets/{ticket_id}/escalate
# ─────────────────────────────────────────────────────────────

@app.post(
    "/tickets/{ticket_id}/escalate",
    response_model=ManualEscalationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Manually escalate a ticket",
    tags=["tickets"],
)
async def manual_escalate(
    ticket_id: str,
    body:      ManualEscalationRequest,
    producer  = Depends(get_producer),
    db        = Depends(get_db),
):
    """
    Trigger a manual escalation for an existing ticket.

    Used by the agent dashboard when a human agent decides a ticket
    needs routing — bypasses the AI agent workflow entirely.

    Publishes an escalation event to fte.escalations and updates
    the ticket status to 'escalated' in the database.
    """
    import uuid as _uuid
    from production.database.queries import (
        get_ticket,
        create_escalation,
        update_ticket_status,
    )
    from production.kafka_client import TOPIC_ESCALATIONS
    from production.agent.tools import ESCALATION_ROUTING, DEFAULT_ESCALATION

    try:
        ticket_uuid = _uuid.UUID(ticket_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid ticket_id format")

    ticket = await get_ticket(db, ticket_uuid)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ticket {ticket_id} not found")

    routing = ESCALATION_ROUTING.get(body.reason, DEFAULT_ESCALATION)

    try:
        escalation = await create_escalation(
            db,
            ticket_id=ticket_uuid,
            customer_id=ticket["customer_id"],
            reason=body.reason,
            urgency=body.urgency,
            routing_team=routing["team"],
            routing_email=routing["email"],
            notes=body.notes or "",
        )
        await update_ticket_status(db, ticket_uuid, "escalated")

        # Publish escalation event
        await producer.send_and_wait(
            TOPIC_ESCALATIONS,
            value={
                "escalation_id": str(escalation["id"]),
                "ticket_id":     ticket_id,
                "ticket_ref":    ticket["ticket_ref"],
                "customer_id":   str(ticket["customer_id"]),
                "reason":        body.reason,
                "urgency":       body.urgency,
                "team":          routing["team"],
                "routing_email": routing["email"],
                "notes":         body.notes,
                "escalated_at":  datetime.now(timezone.utc).isoformat(),
            },
            key=str(ticket["customer_id"]).encode("utf-8"),
        )

        logger.info(
            "Manual escalation | ticket=%s | reason=%s | team=%s",
            ticket["ticket_ref"], body.reason, routing["team"],
        )

        return ManualEscalationResponse(
            escalation_id=str(escalation["id"]),
            ticket_ref=ticket["ticket_ref"],
            team=routing["team"],
            routing_email=routing["email"],
            sla=routing["sla"],
            escalated_at=datetime.now(timezone.utc).isoformat(),
        )

    except Exception as e:
        logger.error("Manual escalation failed | ticket=%s | error=%s", ticket_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Escalation failed. Please try again.",
        )


# ─────────────────────────────────────────────────────────────
# ⑩ GET /dashboard/stats  — summary counts for the dashboard
# ─────────────────────────────────────────────────────────────

@app.get(
    "/dashboard/stats",
    summary="Dashboard summary statistics",
    tags=["dashboard"],
)
async def dashboard_stats(db = Depends(get_db), _auth = Depends(require_admin_key)):
    """
    Returns top-level counts for the admin dashboard:
    total tickets, open, escalated, resolved, AI-resolved,
    avg sentiment, KB article count.
    """
    try:
        row = await db.fetchrow(
            """
            SELECT
                COUNT(*)                                             AS total_tickets,
                COUNT(*) FILTER (WHERE status = 'open')             AS open_tickets,
                COUNT(*) FILTER (WHERE status = 'in_progress')      AS in_progress_tickets,
                COUNT(*) FILTER (WHERE status = 'escalated')        AS escalated_tickets,
                COUNT(*) FILTER (WHERE status = 'resolved')         AS resolved_tickets,
                COUNT(*) FILTER (WHERE escalated = FALSE
                                  AND status = 'resolved')          AS ai_resolved,
                ROUND(AVG(opening_sentiment)::numeric, 3)           AS avg_sentiment,
                MIN(opened_at)                                       AS oldest_ticket,
                MAX(opened_at)                                       AS newest_ticket
            FROM tickets
            """
        )
        kb_count = await db.fetchval(
            "SELECT COUNT(*) FROM knowledge_base WHERE is_active = TRUE"
        )
        kb_with_embeddings = await db.fetchval(
            "SELECT COUNT(*) FROM knowledge_base WHERE is_active = TRUE AND embedding IS NOT NULL"
        )
        return {
            "tickets": {
                "total":       int(row["total_tickets"] or 0),
                "open":        int(row["open_tickets"] or 0),
                "in_progress": int(row["in_progress_tickets"] or 0),
                "escalated":   int(row["escalated_tickets"] or 0),
                "resolved":    int(row["resolved_tickets"] or 0),
                "ai_resolved": int(row["ai_resolved"] or 0),
            },
            "avg_sentiment": float(row["avg_sentiment"] or 0.5),
            "kb": {
                "total":           int(kb_count or 0),
                "with_embeddings": int(kb_with_embeddings or 0),
            },
        }
    except Exception as e:
        logger.error("dashboard_stats failed | error=%s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────
# ⑪ GET /dashboard/tickets  — paginated ticket list
# ─────────────────────────────────────────────────────────────

@app.get(
    "/dashboard/tickets",
    summary="Paginated ticket list for dashboard",
    tags=["dashboard"],
)
async def dashboard_tickets(
    status_filter: Optional[str] = Query(None, alias="status"),
    channel:       Optional[str] = Query(None),
    limit:         int           = Query(20, ge=1, le=100),
    offset:        int           = Query(0, ge=0),
    db    = Depends(get_db),
    _auth = Depends(require_admin_key),
):
    """
    Returns a paginated list of tickets with customer info,
    optionally filtered by status and/or channel.
    """
    try:
        conditions = []
        params     = []
        p          = 1

        if status_filter and status_filter != "all":
            conditions.append(f"t.status = ${p}::ticket_status")
            params.append(status_filter)
            p += 1

        if channel and channel != "all":
            conditions.append(f"t.source_channel = ${p}::channel_type")
            params.append(channel)
            p += 1

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        rows = await db.fetch(
            f"""
            SELECT
                t.id, t.ticket_ref, t.status, t.priority, t.category,
                t.source_channel, t.issue_summary, t.escalated,
                t.escalation_reason, t.opened_at, t.resolved_at,
                t.first_response_ms, t.opening_sentiment,
                c.display_name AS customer_name,
                c.plan_tier    AS customer_plan
            FROM tickets t
            LEFT JOIN customers c ON c.id = t.customer_id
            {where}
            ORDER BY t.opened_at DESC
            LIMIT ${p} OFFSET ${p+1}
            """,
            *params, limit, offset,
        )

        total = await db.fetchval(
            f"SELECT COUNT(*) FROM tickets t {where}",
            *params,
        )

        tickets = [
            {
                "ticket_id":        str(r["id"]),
                "ticket_ref":       r["ticket_ref"],
                "status":           r["status"],
                "priority":         r["priority"],
                "category":         r["category"],
                "channel":          r["source_channel"],
                "summary":          r["issue_summary"],
                "escalated":        r["escalated"],
                "escalation_reason":r["escalation_reason"],
                "customer_name":    r["customer_name"] or "Unknown",
                "customer_plan":    r["customer_plan"] or "unknown",
                "opened_at":        r["opened_at"].isoformat() if r["opened_at"] else None,
                "resolved_at":      r["resolved_at"].isoformat() if r["resolved_at"] else None,
                "first_response_ms":r["first_response_ms"],
                "sentiment":        float(r["opening_sentiment"]) if r["opening_sentiment"] else None,
            }
            for r in rows
        ]

        return {"tickets": tickets, "total": int(total or 0), "limit": limit, "offset": offset}

    except Exception as e:
        logger.error("dashboard_tickets failed | error=%s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────
# ⑫ GET /dashboard/kb  — knowledge base article list
# ─────────────────────────────────────────────────────────────

@app.get(
    "/dashboard/kb",
    summary="Knowledge base articles for dashboard",
    tags=["dashboard"],
)
async def dashboard_kb(
    category: Optional[str] = Query(None),
    db    = Depends(get_db),
    _auth = Depends(require_admin_key),
):
    """Returns all KB articles, optionally filtered by category."""
    try:
        cond   = "AND category = $1" if category else ""
        params = [category] if category else []
        rows   = await db.fetch(
            f"""
            SELECT id, kb_ref, category, title, word_count, is_active,
                   search_hits, search_used, version, updated_at,
                   (embedding IS NOT NULL) AS has_embedding
            FROM knowledge_base
            WHERE is_active = TRUE {cond}
            ORDER BY category, kb_ref
            """,
            *params,
        )
        return {
            "articles": [
                {
                    "id":           str(r["id"]),
                    "kb_ref":       r["kb_ref"],
                    "category":     r["category"],
                    "title":        r["title"],
                    "word_count":   r["word_count"],
                    "has_embedding":r["has_embedding"],
                    "search_hits":  r["search_hits"],
                    "search_used":  r["search_used"],
                    "version":      r["version"],
                    "updated_at":   r["updated_at"].isoformat() if r["updated_at"] else None,
                }
                for r in rows
            ],
            "total": len(rows),
        }
    except Exception as e:
        logger.error("dashboard_kb failed | error=%s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────
# WS /ws/ping  — connectivity test (no auth, no DB)
# ─────────────────────────────────────────────────────────────

@app.websocket("/ws/ping")
async def ws_ping(websocket: WebSocket):
    """Simple WebSocket ping — tests WS connectivity without DB."""
    await websocket.accept()
    await websocket.send_json({"type": "pong", "ok": True})
    await websocket.close()


# ─────────────────────────────────────────────────────────────
# ⑬ WS /ws/ticket/{ticket_id}  — real-time ticket updates
# ─────────────────────────────────────────────────────────────

@app.websocket("/ws/ticket/{ticket_id}")
async def ticket_websocket(ticket_id: str, websocket: WebSocket):
    """
    WebSocket endpoint for real-time ticket status updates.

    Client connects → receives current ticket state immediately →
    then receives push updates whenever the ticket changes (polled
    every 2s server-side using asyncpg LISTEN/NOTIFY pattern).

    Message types:
      { "type": "init",   "ticket": {...} }   — sent on connect
      { "type": "update", "ticket": {...} }   — sent on state change
      { "type": "ping" }                      — keepalive every 15s
    """
    await ws_manager.connect(ticket_id, websocket)
    logger.info("WS connect | ticket=%s | active=%d", ticket_id, ws_manager.active_count())

    try:
        pool = getattr(websocket.app.state, "db_pool", None)
        if pool is None:
            await websocket.send_json({"type": "error", "detail": "Service starting up, please retry in a moment."})
            return
        last_status = None
        last_msg_count = 0

        async def fetch_ticket_state():
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT t.id, t.ticket_ref, t.status, t.priority, t.category,
                           t.source_channel, t.issue_summary, t.escalated,
                           t.escalation_reason, t.opened_at, t.resolved_at,
                           t.first_response_ms, t.opening_sentiment,
                           (
                               SELECT json_agg(json_build_object(
                                   'content',    m.formatted_content,
                                   'direction',  m.direction,
                                   'sent_at',    m.received_at
                               ) ORDER BY m.received_at)
                               FROM messages m
                               WHERE m.ticket_id = t.id
                                 AND m.direction = 'outbound'
                           ) AS messages
                    FROM tickets t
                    WHERE t.id = $1::uuid
                    """,
                    ticket_id,
                )
            if not row:
                return None
            msgs = row["messages"] or []
            return {
                "ticket_id":   str(row["id"]),
                "ticket_ref":  row["ticket_ref"],
                "status":      row["status"],
                "priority":    row["priority"],
                "category":    row["category"],
                "channel":     row["source_channel"],
                "issue_summary": row["issue_summary"],
                "escalated":   row["escalated"],
                "escalation_reason": row["escalation_reason"],
                "opened_at":   row["opened_at"].isoformat() if row["opened_at"] else None,
                "resolved_at": row["resolved_at"].isoformat() if row["resolved_at"] else None,
                "first_response_ms": row["first_response_ms"],
                "sentiment":   float(row["opening_sentiment"]) if row["opening_sentiment"] else None,
                "messages":    [
                    {
                        "content": m["content"],
                        "direction": m["direction"],
                        "sent_at": m["sent_at"].isoformat() if hasattr(m["sent_at"], "isoformat") else str(m["sent_at"]),
                    }
                    for m in msgs
                ],
            }

        # Send initial state immediately
        state = await fetch_ticket_state()
        if state:
            await websocket.send_json({"type": "init", "ticket": state})
            last_status    = state["status"]
            last_msg_count = len(state["messages"])

        ping_counter = 0
        while True:
            await asyncio.sleep(2)
            ping_counter += 1

            # Keepalive ping every 15s (every 7–8 iterations)
            if ping_counter % 8 == 0:
                await websocket.send_json({"type": "ping"})

            state = await fetch_ticket_state()
            if not state:
                break

            new_status    = state["status"]
            new_msg_count = len(state["messages"])

            if new_status != last_status or new_msg_count != last_msg_count:
                await websocket.send_json({"type": "update", "ticket": state})
                last_status    = new_status
                last_msg_count = new_msg_count
                logger.info("WS push | ticket=%s | status=%s | msgs=%d", ticket_id, new_status, new_msg_count)

            # Stop pushing once resolved or escalated
            if new_status in ("resolved", "escalated") and new_msg_count > 0:
                await asyncio.sleep(1)
                break

    except WebSocketDisconnect:
        logger.info("WS disconnect | ticket=%s", ticket_id)
    except Exception as e:
        logger.error("WS error | ticket=%s | error=%s", ticket_id, e)
    finally:
        ws_manager.disconnect(ticket_id, websocket)
