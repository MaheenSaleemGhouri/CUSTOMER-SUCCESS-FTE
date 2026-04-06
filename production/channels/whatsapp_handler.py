"""
production/channels/whatsapp_handler.py
WhatsApp channel handler — Twilio WhatsApp API.

Responsibilities:
  - validate_webhook()   — verify Twilio X-Twilio-Signature HMAC
  - process_webhook()    — parse incoming Twilio form POST
  - send_message()       — send WhatsApp message via Twilio client
  - format_response()    — split long responses into ≤1600 char parts

Normalized output (every inbound message):
  {
    "channel": "whatsapp",
    "channel_message_id": str,          # Twilio MessageSid
    "customer_phone": str,              # E.164, e.g. +15125550142
    "content": str,
    "received_at": ISO str,
    "metadata": {
      "profile_name": str,
      "wa_id": str,
      "num_media": int,
      "media_urls": list[str]
    }
  }
"""

import hashlib
import hmac
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import Request
from twilio.request_validator import RequestValidator
from twilio.rest import Client

logger = logging.getLogger(__name__)

WHATSAPP_MAX_CHARS     = 300    # per-message soft limit (brand voice)
WHATSAPP_TWILIO_MAX    = 1600   # Twilio hard limit per message
WHATSAPP_NUMBER_PREFIX = "whatsapp:"


class WhatsAppHandler:
    """
    Handles all WhatsApp channel operations via Twilio.
    Verifies webhook signatures, parses inbound messages,
    and sends outbound messages through the Twilio WhatsApp API.
    """

    def __init__(self) -> None:
        self._account_sid  = os.getenv("TWILIO_ACCOUNT_SID", "")
        self._auth_token   = os.getenv("TWILIO_AUTH_TOKEN", "")
        self._from_number  = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+15005550006")
        self._webhook_url  = os.getenv("TWILIO_WEBHOOK_URL", "https://api.techcorp.io/webhooks/whatsapp")
        self._client: Optional[Client] = None
        self._validator: Optional[RequestValidator] = None

    def _get_client(self) -> Client:
        if not self._client:
            if not self._account_sid or not self._auth_token:
                raise RuntimeError("TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be set")
            self._client = Client(self._account_sid, self._auth_token)
        return self._client

    def _get_validator(self) -> RequestValidator:
        if not self._validator:
            self._validator = RequestValidator(self._auth_token)
        return self._validator

    # ── Webhook validation ───────────────────────────────────

    async def validate_webhook(self, request: Request) -> bool:
        """
        Verify the Twilio X-Twilio-Signature header to authenticate the webhook.
        Rejects requests that cannot be verified — prevents spoofed webhooks.

        Args:
            request: The incoming FastAPI Request object.

        Returns:
            True if signature is valid, False otherwise.
        """
        try:
            twilio_signature = request.headers.get("X-Twilio-Signature", "")
            if not twilio_signature:
                logger.warning("WhatsApp webhook missing X-Twilio-Signature")
                return False

            # Reconstruct the URL exactly as Twilio signed it
            url       = self._webhook_url
            form_data = dict(await request.form())

            validator = self._get_validator()
            is_valid  = validator.validate(url, form_data, twilio_signature)

            if not is_valid:
                logger.warning(
                    "WhatsApp webhook signature validation failed | sig=%s",
                    twilio_signature[:20],
                )
            return is_valid

        except Exception as e:
            logger.error("WhatsApp validate_webhook error: %s", e)
            return False

    # ── Inbound message processing ───────────────────────────

    def process_webhook(self, form_data: dict) -> Optional[dict]:
        """
        Parse a validated Twilio WhatsApp webhook form POST into a
        normalised inbound message dict.

        Args:
            form_data: Parsed form fields from the Twilio POST body.

        Returns:
            Normalised message dict or None if not a valid message.
        """
        message_sid = form_data.get("MessageSid", "")
        body        = form_data.get("Body", "").strip()
        from_raw    = form_data.get("From", "")
        num_media   = int(form_data.get("NumMedia", "0"))

        if not from_raw:
            logger.warning("WhatsApp webhook missing From field")
            return None

        # Strip "whatsapp:" prefix to get clean E.164 phone number
        customer_phone = from_raw.replace(WHATSAPP_NUMBER_PREFIX, "").strip()

        # Collect media URLs if any
        media_urls = [
            form_data.get(f"MediaUrl{i}", "")
            for i in range(num_media)
            if form_data.get(f"MediaUrl{i}")
        ]

        normalised = {
            "channel":            "whatsapp",
            "channel_message_id": message_sid,
            "customer_phone":     customer_phone,
            "content":            body,
            "received_at":        datetime.now(timezone.utc).isoformat(),
            "metadata": {
                "profile_name": form_data.get("ProfileName", ""),
                "wa_id":        form_data.get("WaId", ""),
                "num_media":    num_media,
                "media_urls":   media_urls,
                "account_sid":  form_data.get("AccountSid", ""),
                "to":           form_data.get("To", "").replace(WHATSAPP_NUMBER_PREFIX, ""),
            },
        }

        logger.info(
            "WhatsApp inbound | from=%s | sid=%s | len=%d | media=%d",
            customer_phone, message_sid, len(body), num_media,
        )
        return normalised

    # ── Send message ─────────────────────────────────────────

    def send_message(self, to_phone: str, body: str) -> dict:
        """
        Send a WhatsApp message via Twilio.
        Automatically splits messages exceeding WHATSAPP_TWILIO_MAX (1600 chars)
        into multiple parts.

        Args:
            to_phone: Recipient E.164 phone number (e.g. "+15125550142").
            body:     Message text. Will be split if > 1600 chars.

        Returns:
            Twilio message SID dict (or list of SIDs for multi-part).
        """
        client  = self._get_client()
        to_addr = f"{WHATSAPP_NUMBER_PREFIX}{to_phone}"
        parts   = self.format_response(body, max_length=WHATSAPP_TWILIO_MAX)
        results = []

        for i, part in enumerate(parts):
            try:
                msg = client.messages.create(
                    from_=self._from_number,
                    to=to_addr,
                    body=part,
                )
                results.append({"sid": msg.sid, "status": msg.status, "part": i + 1})
                logger.info(
                    "WhatsApp sent | to=%s | sid=%s | part=%d/%d | len=%d",
                    to_phone, msg.sid, i + 1, len(parts), len(part),
                )
            except Exception as e:
                logger.error("WhatsApp send_message failed | to=%s | part=%d | error=%s", to_phone, i + 1, e)
                raise

        return results[0] if len(results) == 1 else {"parts": results}

    # ── Format / split response ──────────────────────────────

    def format_response(self, response: str, max_length: int = 1600) -> list[str]:
        """
        Split a response string into a list of parts each ≤ max_length chars.
        Splits at sentence boundaries when possible to preserve readability.

        Args:
            response:   The full message text.
            max_length: Maximum characters per part (Twilio hard limit = 1600).

        Returns:
            List of message parts, each ≤ max_length chars.
        """
        if len(response) <= max_length:
            return [response]

        parts = []
        remaining = response

        while len(remaining) > max_length:
            chunk = remaining[:max_length]

            # Try to split at sentence boundary
            for sep in (". ", "! ", "? ", "\n\n", "\n", " "):
                idx = chunk.rfind(sep)
                if idx > int(max_length * 0.5):
                    chunk     = remaining[: idx + len(sep)].rstrip()
                    remaining = remaining[idx + len(sep):].lstrip()
                    break
            else:
                # Hard split at max_length
                remaining = remaining[max_length:]

            parts.append(chunk)

        if remaining:
            parts.append(remaining)

        logger.debug("WhatsApp split %d chars into %d parts", len(response), len(parts))
        return parts

    # ── Delivery status webhook ──────────────────────────────

    def process_status_webhook(self, form_data: dict) -> dict:
        """
        Parse a Twilio delivery status callback (sent to /webhooks/whatsapp/status).

        Args:
            form_data: Twilio status POST fields.

        Returns:
            Dict with message_sid, status, and error_code if any.
        """
        return {
            "channel_message_id": form_data.get("MessageSid", ""),
            "status":             form_data.get("MessageStatus", "unknown"),
            "error_code":         form_data.get("ErrorCode"),
            "error_message":      form_data.get("ErrorMessage"),
            "to":                 form_data.get("To", "").replace(WHATSAPP_NUMBER_PREFIX, ""),
            "timestamp":          datetime.now(timezone.utc).isoformat(),
        }
