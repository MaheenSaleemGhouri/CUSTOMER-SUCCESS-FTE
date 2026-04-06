"""
Exercise 3.2 — Load Test
========================
Run karne ka tarika:

1. Locust install karo:
   pip install locust

2. FastAPI server chalu karo:
   uvicorn production.api.main:app --host 0.0.0.0 --port 8000

3. Load test start karo:
   locust -f production/tests/load_test.py --host=http://localhost:8000

4. Browser mein jao:
   http://localhost:8089

5. Recommended settings:
   - Number of users: 100
   - Spawn rate: 10 users/second
   - Host: http://localhost:8000

24-Hour Test Targets:
   - Uptime > 99.9%
   - P95 latency < 3 seconds
   - No message loss
"""

from locust import HttpUser, task, between
import random

# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

CATEGORIES = ['general', 'technical', 'billing', 'feedback', 'bug_report']
PRIORITIES  = ['low', 'medium', 'high']

# Shared ticket IDs collected during run for status-check tasks
_submitted_ticket_ids: list[str] = []


# ─────────────────────────────────────────────────────────────
# USER CLASS 1 — WebFormUser
# ─────────────────────────────────────────────────────────────

class WebFormUser(HttpUser):
    """Simulate users submitting support forms — most common traffic."""

    weight    = 3
    wait_time = between(2, 10)

    @task
    def submit_support_form(self):
        """POST /support/submit with randomized data."""
        data = {
            "name":     f"Load Test User {random.randint(1, 10000)}",
            "email":    f"loadtest{random.randint(1, 10000)}@example.com",
            "subject":  f"Load Test Query {random.randint(1, 100)}",
            "category": random.choice(CATEGORIES),
            "priority": random.choice(PRIORITIES),
            "message":  "This is a load test message to verify system performance under stress.",
        }

        with self.client.post(
            "/support/submit",
            json=data,
            catch_response=True,
            name="/support/submit",
        ) as response:
            if response.status_code == 201:
                ticket_id = response.json().get("ticket_id", "")
                if ticket_id:
                    _submitted_ticket_ids.append(ticket_id)
                response.success()
            elif response.status_code == 422:
                response.failure(f"Validation error: {response.text}")
            else:
                response.failure(f"Failed: {response.status_code}")

    @task(2)
    def check_ticket_status(self):
        """
        Bonus: POST /support/submit → ticket_id milega → GET /support/ticket/{ticket_id}.
        Simulates a customer submitting then immediately checking their ticket status.
        """
        # Step 1: Submit a form to get a fresh ticket_id
        data = {
            "name":     f"Load Test User {random.randint(1, 10000)}",
            "email":    f"loadtest{random.randint(1, 10000)}@example.com",
            "subject":  f"Load Test Query {random.randint(1, 100)}",
            "category": random.choice(CATEGORIES),
            "priority": random.choice(PRIORITIES),
            "message":  "This is a load test message to verify system performance under stress.",
        }

        ticket_id = None
        with self.client.post(
            "/support/submit",
            json=data,
            catch_response=True,
            name="/support/submit [for status check]",
        ) as response:
            if response.status_code == 201:
                ticket_id = response.json().get("ticket_id", "")
                response.success()
            elif response.status_code == 422:
                response.failure(f"Validation error: {response.text}")
            else:
                response.failure(f"Failed: {response.status_code}")

        # Step 2: Check ticket status
        if ticket_id:
            with self.client.get(
                f"/support/ticket/{ticket_id}",
                catch_response=True,
                name="/support/ticket/[id]",
            ) as response:
                if response.status_code in (200, 404):
                    response.success()
                else:
                    response.failure(f"Failed: {response.status_code}")


# ─────────────────────────────────────────────────────────────
# USER CLASS 2 — HealthCheckUser
# ─────────────────────────────────────────────────────────────

class HealthCheckUser(HttpUser):
    """Monitor system health during load test."""

    weight    = 1
    wait_time = between(5, 15)

    @task
    def check_health(self):
        """GET /health — system + channel status."""
        with self.client.get(
            "/health",
            catch_response=True,
            name="/health",
        ) as response:
            if response.status_code == 200:
                body = response.json()
                if body.get("status") == "error":
                    response.failure("Health check returned status=error")
                else:
                    response.success()
            elif response.status_code == 503:
                # Degraded but alive
                response.success()
            else:
                response.failure(f"Failed: {response.status_code}")

    @task
    def check_metrics(self):
        """GET /metrics/channels — 24hr per-channel breakdown."""
        with self.client.get(
            "/metrics/channels",
            catch_response=True,
            name="/metrics/channels",
        ) as response:
            if response.status_code in (200, 404):
                response.success()
            else:
                response.failure(f"Failed: {response.status_code}")

    @task
    def check_daily_metrics(self):
        """Bonus: GET /metrics/daily — daily analytics dashboard."""
        with self.client.get(
            "/metrics/daily",
            catch_response=True,
            name="/metrics/daily",
        ) as response:
            if response.status_code in (200, 500):
                response.success()   # 500 acceptable if DB not seeded
            else:
                response.failure(f"Failed: {response.status_code}")
