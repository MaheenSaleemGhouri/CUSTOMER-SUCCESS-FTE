"""
production/kafka_client.py
Kafka topic registry, admin client, and producer/consumer factories.

All 10 topics used by the Customer Success FTE platform are defined here
as typed constants so every module imports from one source of truth.

Topics
------
Inbound (channel → processor):
  TOPIC_EMAIL_INBOUND        fte.channels.email.inbound
  TOPIC_WHATSAPP_INBOUND     fte.channels.whatsapp.inbound
  TOPIC_WEBFORM_INBOUND      fte.channels.webform.inbound

Outbound (processor → channel sender):
  TOPIC_EMAIL_OUTBOUND       fte.channels.email.outbound
  TOPIC_WHATSAPP_OUTBOUND    fte.channels.whatsapp.outbound
  TOPIC_WEBFORM_OUTBOUND     fte.channels.webform.outbound

Platform events:
  TOPIC_TICKETS_INCOMING     fte.tickets.incoming
  TOPIC_ESCALATIONS          fte.escalations
  TOPIC_METRICS              fte.metrics

Error handling:
  TOPIC_DLQ                  fte.dlq

Usage
-----
    from production.kafka_client import (
        KafkaClient,
        TOPIC_EMAIL_INBOUND,
        TOPIC_ESCALATIONS,
        ALL_TOPICS,
    )

    client = KafkaClient()
    await client.create_topics()            # idempotent — skips existing
    producer = await client.producer()
    consumer = await client.consumer("fte-message-processor", TOPIC_EMAIL_INBOUND)
    await client.close()

Environment variables:
  KAFKA_BOOTSTRAP_SERVERS    default: localhost:9092
  KAFKA_REPLICATION_FACTOR   default: 1  (set to 3 in production clusters)
"""

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Sequence

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# ENVIRONMENT
# ─────────────────────────────────────────────────────────────

KAFKA_BOOTSTRAP        = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_REPLICATION      = int(os.getenv("KAFKA_REPLICATION_FACTOR", "1"))

# ─────────────────────────────────────────────────────────────
# TOPIC NAME CONSTANTS
# ─────────────────────────────────────────────────────────────

# Inbound — raw customer messages arriving from each channel
TOPIC_EMAIL_INBOUND     = "fte.channels.email.inbound"
TOPIC_WHATSAPP_INBOUND  = "fte.channels.whatsapp.inbound"
TOPIC_WEBFORM_INBOUND   = "fte.channels.webform.inbound"

# Outbound — formatted responses ready to send back to customers
TOPIC_EMAIL_OUTBOUND    = "fte.channels.email.outbound"
TOPIC_WHATSAPP_OUTBOUND = "fte.channels.whatsapp.outbound"
TOPIC_WEBFORM_OUTBOUND  = "fte.channels.webform.outbound"

# Platform events
TOPIC_TICKETS_INCOMING  = "fte.tickets.incoming"   # new ticket created events
TOPIC_ESCALATIONS       = "fte.escalations"         # escalation routing events
TOPIC_METRICS           = "fte.metrics"             # per-message analytics

# Error handling
TOPIC_DLQ               = "fte.dlq"                # dead-letter queue

# ─────────────────────────────────────────────────────────────
# CONVENIENCE GROUPINGS
# ─────────────────────────────────────────────────────────────

INBOUND_TOPICS: tuple[str, ...] = (
    TOPIC_EMAIL_INBOUND,
    TOPIC_WHATSAPP_INBOUND,
    TOPIC_WEBFORM_INBOUND,
)

OUTBOUND_TOPICS: tuple[str, ...] = (
    TOPIC_EMAIL_OUTBOUND,
    TOPIC_WHATSAPP_OUTBOUND,
    TOPIC_WEBFORM_OUTBOUND,
)

CHANNEL_TOPIC_MAP: dict[str, dict[str, str]] = {
    "email":    {"inbound": TOPIC_EMAIL_INBOUND,    "outbound": TOPIC_EMAIL_OUTBOUND},
    "whatsapp": {"inbound": TOPIC_WHATSAPP_INBOUND, "outbound": TOPIC_WHATSAPP_OUTBOUND},
    "web_form": {"inbound": TOPIC_WEBFORM_INBOUND,  "outbound": TOPIC_WEBFORM_OUTBOUND},
}

ALL_TOPICS: tuple[str, ...] = (
    *INBOUND_TOPICS,
    *OUTBOUND_TOPICS,
    TOPIC_TICKETS_INCOMING,
    TOPIC_ESCALATIONS,
    TOPIC_METRICS,
    TOPIC_DLQ,
)

# ─────────────────────────────────────────────────────────────
# TOPIC CONFIGURATION
# ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TopicConfig:
    name:               str
    num_partitions:     int
    replication_factor: int
    retention_ms:       int   # -1 = forever
    description:        str


TOPIC_CONFIGS: dict[str, TopicConfig] = {
    TOPIC_EMAIL_INBOUND: TopicConfig(
        name=TOPIC_EMAIL_INBOUND,
        num_partitions=4,
        replication_factor=KAFKA_REPLICATION,
        retention_ms=7 * 24 * 3600 * 1000,   # 7 days
        description="Inbound emails from Gmail Pub/Sub push notifications",
    ),
    TOPIC_WHATSAPP_INBOUND: TopicConfig(
        name=TOPIC_WHATSAPP_INBOUND,
        num_partitions=4,
        replication_factor=KAFKA_REPLICATION,
        retention_ms=7 * 24 * 3600 * 1000,
        description="Inbound WhatsApp messages from Twilio webhooks",
    ),
    TOPIC_WEBFORM_INBOUND: TopicConfig(
        name=TOPIC_WEBFORM_INBOUND,
        num_partitions=2,
        replication_factor=KAFKA_REPLICATION,
        retention_ms=7 * 24 * 3600 * 1000,
        description="Inbound web form submissions from FastAPI /support/submit",
    ),
    TOPIC_EMAIL_OUTBOUND: TopicConfig(
        name=TOPIC_EMAIL_OUTBOUND,
        num_partitions=4,
        replication_factor=KAFKA_REPLICATION,
        retention_ms=3 * 24 * 3600 * 1000,   # 3 days
        description="Outbound email responses queued for Gmail API delivery",
    ),
    TOPIC_WHATSAPP_OUTBOUND: TopicConfig(
        name=TOPIC_WHATSAPP_OUTBOUND,
        num_partitions=4,
        replication_factor=KAFKA_REPLICATION,
        retention_ms=3 * 24 * 3600 * 1000,
        description="Outbound WhatsApp messages queued for Twilio API delivery",
    ),
    TOPIC_WEBFORM_OUTBOUND: TopicConfig(
        name=TOPIC_WEBFORM_OUTBOUND,
        num_partitions=2,
        replication_factor=KAFKA_REPLICATION,
        retention_ms=3 * 24 * 3600 * 1000,
        description="Outbound web form responses (stored; polled by ticket status API)",
    ),
    TOPIC_TICKETS_INCOMING: TopicConfig(
        name=TOPIC_TICKETS_INCOMING,
        num_partitions=2,
        replication_factor=KAFKA_REPLICATION,
        retention_ms=30 * 24 * 3600 * 1000,  # 30 days
        description="New ticket creation events for downstream consumers (analytics, CRM sync)",
    ),
    TOPIC_ESCALATIONS: TopicConfig(
        name=TOPIC_ESCALATIONS,
        num_partitions=2,
        replication_factor=KAFKA_REPLICATION,
        retention_ms=30 * 24 * 3600 * 1000,
        description="Escalation routing events — consumed by notification service",
    ),
    TOPIC_METRICS: TopicConfig(
        name=TOPIC_METRICS,
        num_partitions=2,
        replication_factor=KAFKA_REPLICATION,
        retention_ms=90 * 24 * 3600 * 1000,  # 90 days
        description="Per-message analytics events — consumed by metrics_collector worker",
    ),
    TOPIC_DLQ: TopicConfig(
        name=TOPIC_DLQ,
        num_partitions=1,
        replication_factor=KAFKA_REPLICATION,
        retention_ms=-1,                      # retain forever for inspection
        description="Dead-letter queue — messages that failed all retry attempts",
    ),
}


# ─────────────────────────────────────────────────────────────
# KAFKA CLIENT
# ─────────────────────────────────────────────────────────────

class KafkaClient:
    """
    Thin wrapper around aiokafka that manages producer/consumer lifecycle
    and provides idempotent topic creation via the admin client.

    Typical usage in FastAPI lifespan:

        client = KafkaClient()
        app.state.kafka_client = client

        @asynccontextmanager
        async def lifespan(app):
            await client.create_topics()
            app.state.kafka_producer = await client.producer()
            yield
            await client.close()
    """

    def __init__(self, bootstrap_servers: str = KAFKA_BOOTSTRAP) -> None:
        self.bootstrap_servers = bootstrap_servers
        self._producer: AIOKafkaProducer | None = None
        self._consumers: list[AIOKafkaConsumer] = []

    # ── Topic provisioning ────────────────────────────────────

    async def create_topics(
        self,
        topics: Sequence[str] = ALL_TOPICS,
        *,
        timeout_ms: int = 10_000,
    ) -> None:
        """
        Create all platform topics if they do not already exist.
        Safe to call multiple times — existing topics are skipped.
        """
        admin = AIOKafkaAdminClient(bootstrap_servers=self.bootstrap_servers)
        await admin.start()
        try:
            existing = set(await admin.list_topics())
            to_create = [
                NewTopic(
                    name=t,
                    num_partitions=TOPIC_CONFIGS[t].num_partitions,
                    replication_factor=TOPIC_CONFIGS[t].replication_factor,
                    topic_configs={
                        "retention.ms": str(TOPIC_CONFIGS[t].retention_ms),
                    },
                )
                for t in topics
                if t not in existing
            ]

            if to_create:
                await admin.create_topics(to_create, timeout_ms=timeout_ms)
                for nt in to_create:
                    logger.info("Topic created | name=%s | partitions=%d", nt.name, nt.num_partitions)
            else:
                logger.info("All %d topics already exist — nothing to create.", len(topics))

        finally:
            await admin.close()

    # ── Producer factory ──────────────────────────────────────

    async def producer(
        self,
        *,
        compression_type: str = "gzip",
        acks: str = "all",
        max_batch_size: int = 16_384,
    ) -> AIOKafkaProducer:
        """
        Return a started AIOKafkaProducer with production-safe defaults.

        acks='all'  — wait for all in-sync replicas (no data loss)
        gzip        — compress payloads (JSON is very compressible)
        """
        prod = AIOKafkaProducer(
            bootstrap_servers=self.bootstrap_servers,
            value_serializer=lambda v: (
                v if isinstance(v, bytes) else __import__("json").dumps(v).encode("utf-8")
            ),
            key_serializer=lambda k: (
                k if isinstance(k, bytes) else str(k).encode("utf-8") if k else None
            ),
            compression_type=compression_type,
            acks=acks,
            max_batch_size=max_batch_size,
        )
        await prod.start()
        self._producer = prod
        logger.info("Kafka producer started | brokers=%s", self.bootstrap_servers)
        return prod

    # ── Consumer factory ──────────────────────────────────────

    async def consumer(
        self,
        group_id: str,
        *topics: str,
        auto_offset_reset: str = "earliest",
        enable_auto_commit: bool = False,
        max_poll_records: int = 10,
    ) -> AIOKafkaConsumer:
        """
        Return a started AIOKafkaConsumer subscribed to the given topics.

        enable_auto_commit=False — manual commit after successful processing
        (prevents message loss if worker crashes mid-flight).
        """
        cons = AIOKafkaConsumer(
            *topics,
            bootstrap_servers=self.bootstrap_servers,
            group_id=group_id,
            value_deserializer=lambda b: __import__("json").loads(b.decode("utf-8")),
            enable_auto_commit=enable_auto_commit,
            auto_offset_reset=auto_offset_reset,
            max_poll_records=max_poll_records,
        )
        await cons.start()
        self._consumers.append(cons)
        logger.info(
            "Kafka consumer started | group=%s | topics=%s", group_id, list(topics)
        )
        return cons

    # ── Health check ──────────────────────────────────────────

    async def health_check(self) -> dict:
        """
        Ping the Kafka cluster and return connectivity + topic status.
        Used by GET /health in main.py.
        """
        admin = AIOKafkaAdminClient(bootstrap_servers=self.bootstrap_servers)
        try:
            await admin.start()
            existing = set(await admin.list_topics())
            missing  = [t for t in ALL_TOPICS if t not in existing]
            return {
                "status":         "ok" if not missing else "degraded",
                "broker":         self.bootstrap_servers,
                "topics_ok":      len(ALL_TOPICS) - len(missing),
                "topics_missing": missing,
            }
        except Exception as e:
            return {"status": "error", "broker": self.bootstrap_servers, "error": str(e)}
        finally:
            await admin.close()

    # ── Teardown ──────────────────────────────────────────────

    async def close(self) -> None:
        """Stop all managed producers and consumers."""
        if self._producer:
            await self._producer.stop()
            logger.info("Kafka producer stopped.")
        for cons in self._consumers:
            await cons.stop()
        if self._consumers:
            logger.info("Kafka consumers stopped | count=%d", len(self._consumers))
        self._consumers.clear()


# ─────────────────────────────────────────────────────────────
# CLI — provision topics from the terminal
# ─────────────────────────────────────────────────────────────

async def _provision_cli() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    client = KafkaClient()
    await client.create_topics()
    health = await client.health_check()
    print(f"\nHealth: {health}")


if __name__ == "__main__":
    asyncio.run(_provision_cli())
