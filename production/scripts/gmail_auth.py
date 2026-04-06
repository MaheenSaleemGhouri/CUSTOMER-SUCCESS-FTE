"""Run this once to generate Gmail OAuth token."""
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parents[2] / ".env")

from production.channels.gmail_handler import GmailHandler

h = GmailHandler()
h._get_service()
print("Token saved successfully!")
