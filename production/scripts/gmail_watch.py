"""Run this once to set up Gmail push notifications via Pub/Sub."""
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parents[2] / ".env")

import asyncio
from production.channels.gmail_handler import GmailHandler


def main():
    h = GmailHandler()
    result = h.setup_push_notifications()
    print("Gmail watch setup result:", result)


main()
