"""
production/agent/formatters.py
Channel-specific response formatters for the Customer Success FTE agent.

Rules (verbatim from constitution):
  EMAIL    : Dear [Name],\n\n{response}\n\nBest regards,\nTechCorp AI Support Team
  WHATSAPP : trim to 300 chars hard max + \n\n📱 Type 'human' for live support.
  WEB_FORM : {response}\n\n---\nNeed more help? Reply to this message or visit our support portal.

Usage:
    from production.agent.formatters import format_for_channel
"""

import re

EMAIL_SIGNATURE = "Best regards,\nTechCorp AI Support Team"
WHATSAPP_SUFFIX = "\n\n📱 Type 'human' for live support."
WEB_FORM_FOOTER = "\n\n---\nNeed more help? Reply to this message or visit our support portal."

WHATSAPP_HARD_MAX = 300


def format_email(response: str, customer_name: str | None = None) -> str:
    """
    Wrap response in email format:
      - Greeting: 'Dear {name},' or 'Dear Customer,'
      - Body (as-is — agent already writes email prose)
      - Signature appended
    """
    name = customer_name.strip() if customer_name else "Customer"
    greeting = f"Dear {name},"
    return f"{greeting}\n\n{response}\n\n{EMAIL_SIGNATURE}"


def format_whatsapp(response: str) -> str:
    """
    Enforce WhatsApp hard maximum of 300 characters.

    Strategy:
      1. Strip response to 300 chars minus len(WHATSAPP_SUFFIX).
      2. Trim at last sentence boundary ('. ', '! ', '? ') if possible.
      3. Append WHATSAPP_SUFFIX.

    The suffix itself is 34 chars, so body budget = 266 chars.
    """
    suffix = WHATSAPP_SUFFIX
    budget = WHATSAPP_HARD_MAX - len(suffix)  # 266

    body = response.strip()

    if len(body) > budget:
        # Try to break at a sentence boundary within budget
        trimmed = body[:budget]
        # Find last sentence-ending punctuation followed by space
        match = re.search(r"[.!?](?=\s)", trimmed[::-1])
        if match:
            cut = budget - match.start()
            trimmed = body[:cut].rstrip()
        else:
            # Fall back to last space
            last_space = trimmed.rfind(" ")
            trimmed = trimmed[:last_space] if last_space > 0 else trimmed
        body = trimmed

    return body + suffix


def format_web_form(response: str) -> str:
    """
    Append support portal footer — no greeting required per channel rules.
    """
    return response.strip() + WEB_FORM_FOOTER


def format_for_channel(
    response: str,
    channel: str,
    customer_name: str | None = None,
) -> str:
    """
    Dispatch to the correct formatter based on channel string.

    Args:
        response:      Raw agent response text.
        channel:       'email' | 'whatsapp' | 'web_form'
        customer_name: Customer display name (used by email formatter).

    Returns:
        Formatted response string ready to send.

    Raises:
        ValueError: If channel is not one of the three supported values.
    """
    channel = channel.lower().strip()

    if channel == "email":
        return format_email(response, customer_name)
    elif channel == "whatsapp":
        return format_whatsapp(response)
    elif channel == "web_form":
        return format_web_form(response)
    else:
        raise ValueError(
            f"Unknown channel '{channel}'. Must be one of: email, whatsapp, web_form"
        )
