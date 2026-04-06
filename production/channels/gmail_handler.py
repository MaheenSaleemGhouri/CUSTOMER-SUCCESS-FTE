"""
production/channels/gmail_handler.py
Gmail channel handler — Gmail API + Google Pub/Sub push notifications.

Responsibilities:
  - setup_push_notifications()  — register Pub/Sub push subscription on a Gmail label
  - process_notification()      — decode incoming Pub/Sub push payload
  - get_message()               — fetch full email via Gmail API
  - send_reply()                — send reply preserving thread
  - _extract_body()             — parse multipart MIME to plain text
  - _extract_email()            — parse email address from "Name <email>" header

Normalized output (every inbound message):
  {
    "channel": "email",
    "channel_message_id": str,
    "customer_email": str,
    "subject": str,
    "content": str,
    "received_at": ISO str,
    "thread_id": str,
    "metadata": {"headers": {}, "labels": []}
  }
"""

import base64
import email
import json
import logging
import os
import re
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

# Gmail OAuth scopes required
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

# Label to watch for new support emails
SUPPORT_LABEL = os.getenv("GMAIL_SUPPORT_LABEL", "INBOX")

# Pub/Sub topic for Gmail push notifications
PUBSUB_TOPIC = os.getenv("GMAIL_PUBSUB_TOPIC", "projects/{project_id}/topics/gmail-support")


class GmailHandler:
    """
    Handles all Gmail channel operations.
    Connects via Gmail API with OAuth2 service account credentials.
    Receives new emails via Google Pub/Sub push notifications.
    """

    def __init__(self) -> None:
        self._service = None
        self._credentials_path = os.getenv("GMAIL_CREDENTIALS_PATH", "/secrets/gmail-credentials.json")
        self._token_path        = os.getenv("GMAIL_TOKEN_PATH", "/secrets/gmail-token.json")
        self._support_email     = os.getenv("GMAIL_SUPPORT_EMAIL", "support@techcorp.io")
        self._project_id        = os.getenv("GCP_PROJECT_ID", "techcorp-prod")
        self._pubsub_topic      = PUBSUB_TOPIC.format(project_id=self._project_id)

    # ── Service initialisation ───────────────────────────────

    def _get_service(self):
        """Lazily initialise the Gmail API service client."""
        if self._service:
            return self._service

        creds = None

        # Load saved token
        if os.path.exists(self._token_path):
            creds = Credentials.from_authorized_user_file(self._token_path, SCOPES)

        # Refresh or re-authorise
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(self._credentials_path, SCOPES)
                creds = flow.run_local_server(port=0)
            with open(self._token_path, "w") as token_file:
                token_file.write(creds.to_json())

        self._service = build("gmail", "v1", credentials=creds)
        logger.info("Gmail API service initialised for %s", self._support_email)
        return self._service

    # ── Pub/Sub setup ────────────────────────────────────────

    def setup_push_notifications(self, topic_name: Optional[str] = None) -> dict:
        """
        Register a Gmail push notification subscription to a Pub/Sub topic.
        Must be called once at startup and renewed every 7 days (Gmail API limit).

        Args:
            topic_name: Full Pub/Sub topic name. Defaults to GMAIL_PUBSUB_TOPIC env var.

        Returns:
            Watch response dict with historyId and expiration timestamp.
        """
        topic = topic_name or self._pubsub_topic
        service = self._get_service()
        try:
            body = {
                "labelIds":        [SUPPORT_LABEL],
                "topicName":       topic,
                "labelFilterAction": "include",
            }
            response = service.users().watch(userId="me", body=body).execute()
            logger.info(
                "Gmail push notifications registered | topic=%s | historyId=%s | expires=%s",
                topic, response.get("historyId"), response.get("expiration"),
            )
            return response
        except HttpError as e:
            logger.error("Failed to setup Gmail push notifications: %s", e)
            raise

    # ── Pub/Sub notification processing ──────────────────────

    def process_notification(self, pubsub_message: dict) -> Optional[dict]:
        """
        Decode a Google Pub/Sub push notification payload and return
        the normalised inbound message dict, or None if not actionable.

        Args:
            pubsub_message: The raw Pub/Sub push message body:
                {
                  "message": {
                    "data": "<base64-encoded JSON>",
                    "messageId": "...",
                    "publishTime": "..."
                  },
                  "subscription": "projects/.../subscriptions/..."
                }

        Returns:
            Normalised message dict or None.
        """
        try:
            msg_data = pubsub_message.get("message", {}).get("data", "")
            decoded  = json.loads(base64.b64decode(msg_data).decode("utf-8"))

            email_address = decoded.get("emailAddress")
            history_id    = decoded.get("historyId")

            if not email_address or not history_id:
                logger.warning("Pub/Sub notification missing emailAddress or historyId")
                return None

            # Fetch the new message IDs from history
            service = self._get_service()
            history_response = service.users().history().list(
                userId="me",
                startHistoryId=history_id,
                historyTypes=["messageAdded"],
            ).execute()

            messages = []
            for history_item in history_response.get("history", []):
                for added in history_item.get("messagesAdded", []):
                    msg = added.get("message", {})
                    # Only process INBOX messages (not sent by us)
                    if "INBOX" in msg.get("labelIds", []):
                        messages.append(msg["id"])

            if not messages:
                return None

            # Return the first new message (process one at a time)
            return self.get_message(messages[0])

        except Exception as e:
            logger.error("Error processing Pub/Sub notification: %s", e)
            return None

    # ── Fetch full email ─────────────────────────────────────

    def get_message(self, message_id: str) -> Optional[dict]:
        """
        Fetch a full Gmail message by ID and return normalised dict.

        Args:
            message_id: Gmail message ID string.

        Returns:
            Normalised inbound message dict or None on error.
        """
        try:
            service = self._get_service()
            raw = service.users().messages().get(
                userId="me",
                id=message_id,
                format="full",
            ).execute()

            payload = raw.get("payload", {})
            headers = {h["name"]: h["value"] for h in payload.get("headers", [])}

            subject    = headers.get("Subject", "(no subject)")
            from_header = headers.get("From", "")
            date_header = headers.get("Date", "")
            thread_id  = raw.get("threadId", "")
            label_ids  = raw.get("labelIds", [])

            customer_email = self._extract_email(from_header)
            content        = self._extract_body(payload)

            # Parse received_at from Date header, fallback to now
            try:
                from email.utils import parsedate_to_datetime
                received_at = parsedate_to_datetime(date_header).isoformat()
            except Exception:
                received_at = datetime.now(timezone.utc).isoformat()

            normalised = {
                "channel":            "email",
                "channel_message_id": message_id,
                "customer_email":     customer_email,
                "subject":            subject,
                "content":            content,
                "received_at":        received_at,
                "thread_id":          thread_id,
                "metadata": {
                    "headers": {
                        k: headers.get(k, "")
                        for k in ("From", "To", "Reply-To", "Message-ID", "Date")
                    },
                    "labels": label_ids,
                },
            }

            logger.info(
                "Gmail message fetched | id=%s | from=%s | subject=%s",
                message_id, customer_email, subject[:60],
            )
            return normalised

        except HttpError as e:
            logger.error("Gmail get_message failed | id=%s | error=%s", message_id, e)
            return None

    # ── Send reply ───────────────────────────────────────────

    def send_reply(
        self,
        to_email: str,
        subject: str,
        body: str,
        thread_id: Optional[str] = None,
        in_reply_to: Optional[str] = None,
    ) -> dict:
        """
        Send a reply email via the Gmail API.

        Args:
            to_email:    Recipient email address.
            subject:     Email subject (auto-prefixed with "Re: " if not already).
            body:        Plain-text email body (channel formatter has already applied greeting/signature).
            thread_id:   Gmail thread ID to keep reply in same thread.
            in_reply_to: Message-ID header value of the email being replied to.

        Returns:
            Gmail API send response dict with 'id' and 'threadId'.
        """
        try:
            # Ensure Re: prefix for replies
            if thread_id and not subject.lower().startswith("re:"):
                subject = f"Re: {subject}"

            mime_msg = MIMEMultipart("alternative")
            mime_msg["To"]      = to_email
            mime_msg["From"]    = self._support_email
            mime_msg["Subject"] = subject

            if in_reply_to:
                mime_msg["In-Reply-To"] = in_reply_to
                mime_msg["References"]  = in_reply_to

            mime_msg.attach(MIMEText(body, "plain"))

            raw_bytes  = mime_msg.as_bytes()
            raw_b64    = base64.urlsafe_b64encode(raw_bytes).decode("utf-8")
            send_body  = {"raw": raw_b64}
            if thread_id:
                send_body["threadId"] = thread_id

            service  = self._get_service()
            response = service.users().messages().send(
                userId="me", body=send_body
            ).execute()

            logger.info(
                "Gmail reply sent | to=%s | subject=%s | msgId=%s",
                to_email, subject[:60], response.get("id"),
            )
            return response

        except HttpError as e:
            logger.error("Gmail send_reply failed | to=%s | error=%s", to_email, e)
            raise

    # ── Private helpers ──────────────────────────────────────

    def _extract_body(self, payload: dict) -> str:
        """
        Recursively extract plain-text body from a Gmail message payload.
        Handles multipart MIME — prefers text/plain, falls back to text/html stripped.

        Args:
            payload: Gmail message payload dict.

        Returns:
            Decoded plain-text string.
        """
        mime_type = payload.get("mimeType", "")
        body      = payload.get("body", {})
        parts     = payload.get("parts", [])

        if mime_type == "text/plain":
            data = body.get("data", "")
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace").strip()

        if mime_type == "text/html":
            data = body.get("data", "")
            html = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            # Strip HTML tags for plain text fallback
            return re.sub(r"<[^>]+>", " ", html).strip()

        if mime_type.startswith("multipart/"):
            # Prefer text/plain part first
            for part in parts:
                if part.get("mimeType") == "text/plain":
                    return self._extract_body(part)
            # Fallback: try any part
            for part in parts:
                text = self._extract_body(part)
                if text:
                    return text

        return ""

    def _extract_email(self, from_header: str) -> str:
        """
        Parse the email address from a From header.
        Handles formats:
          - "John Smith <john@example.com>"
          - "<john@example.com>"
          - "john@example.com"

        Args:
            from_header: Raw From header string.

        Returns:
            Lowercase email address string.
        """
        match = re.search(r"<([^>]+)>", from_header)
        if match:
            return match.group(1).lower().strip()
        # Plain email without angle brackets
        plain = re.search(r"[\w.+-]+@[\w.-]+\.\w+", from_header)
        if plain:
            return plain.group(0).lower().strip()
        return from_header.lower().strip()
