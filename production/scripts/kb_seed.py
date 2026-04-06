"""
production/scripts/kb_seed.py
Populate the knowledge_base table with TechCorp articles + OpenAI embeddings.

Usage:
    cd "C:/Hackathon 5"
    python -m production.scripts.kb_seed

Requires .env with OPENAI_API_KEY and DATABASE_URL set.
"""

import asyncio
import logging
import sys
from pathlib import Path

# ── Load .env before any other imports ───────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(Path(__file__).parents[2] / ".env")

import asyncpg
import openai
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("kb_seed")

EMBED_MODEL = "text-embedding-3-small"

# ─────────────────────────────────────────────────────────────────────────────
# KB ARTICLES — sourced from context/ files
# Format: (kb_ref, category, title, content)
# ─────────────────────────────────────────────────────────────────────────────

KB_ARTICLES = [
    # ── PASSWORD & LOGIN ────────────────────────────────────────────────────
    (
        "KB-LOGIN-001",
        "account",
        "How to Reset Your Password",
        """To reset your TechCorp Workspace password:
1. Go to app.techcorp.io/login
2. Click "Forgot Password"
3. Enter your registered email address
4. Check your inbox for a reset link (check spam if not received)
5. The reset link expires in 24 hours
6. If no email arrives: whitelist noreply@techcorp.io and retry

Important: The reset link is single-use and expires after 24 hours. If you still can't log in after resetting, contact support.""",
    ),
    (
        "KB-LOGIN-002",
        "account",
        "Two-Factor Authentication (2FA) Setup and Troubleshooting",
        """TechCorp Workspace supports two-factor authentication (2FA) for added security.

Setup:
1. Go to Settings → Security → Two-Factor Auth
2. Choose TOTP (authenticator app) or SMS
3. Follow the on-screen instructions
4. Save your backup codes in a safe place

Troubleshooting 2FA:
- TOTP codes not working: ensure your device clock is synced (time drift causes failures)
- Lost access to authenticator: use a backup code from your saved list
- Lost backup codes: contact support with proof of account ownership
- SMS not arriving: check your phone number in Settings → Profile

Supported authenticator apps: Google Authenticator, Authy, Microsoft Authenticator.""",
    ),
    (
        "KB-LOGIN-003",
        "account",
        "Single Sign-On (SSO) Configuration",
        """Single Sign-On (SSO) via SAML 2.0 is available on Business+ and Enterprise plans.

Setup requirements:
- Your IT admin must configure the identity provider (IdP)
- Supported IdPs: Okta, Azure AD, Google Workspace, OneLogin
- TechCorp metadata URL: app.techcorp.io/sso/metadata

Configuration steps:
1. Go to Settings → Security → SSO
2. Enter your IdP metadata URL or upload XML
3. Map attributes: email, name, department (optional)
4. Test the connection before enabling
5. Enable enforcement to require SSO for all team members

Note: SSO is a Business+ feature only. Free and Growth plans use standard email/password login.""",
    ),

    # ── TEAM & MEMBERS ──────────────────────────────────────────────────────
    (
        "KB-TEAM-001",
        "account",
        "How to Add and Invite Team Members",
        """To invite new team members to your TechCorp Workspace:

1. Go to Settings → Team
2. Click "Invite Member"
3. Enter the team member's email address
4. Select their role: Owner, Admin, Member, or Guest
5. Click "Send Invitation"

Role permissions:
- Owner: Full access, billing, can delete workspace
- Admin: Manage members, settings (not billing)
- Member: Create/edit projects and tasks
- Guest: View-only access to shared projects

Invitation details:
- Invitations expire after 7 days
- Resend from: Settings → Team → Pending Invites
- Members count against your plan seat limit
- Guests do not count as seats on Growth+ plans""",
    ),
    (
        "KB-TEAM-002",
        "account",
        "Managing Team Permissions and Roles",
        """TechCorp Workspace uses role-based access control (RBAC).

Changing a member's role:
1. Go to Settings → Team
2. Find the member
3. Click the role dropdown next to their name
4. Select the new role
5. Changes take effect immediately

Removing a member:
1. Settings → Team → find member → click "Remove"
2. Their tasks remain but are unassigned
3. They lose access immediately

Guest access:
- Guests can only see projects explicitly shared with them
- Create guest links: Project → Share → Invite Guest
- Guest links expire based on your plan settings""",
    ),

    # ── INTEGRATIONS ────────────────────────────────────────────────────────
    (
        "KB-INT-001",
        "integration",
        "Slack Integration Setup Guide",
        """Connect TechCorp Workspace with Slack to sync tasks and notifications.

Requirements: Growth plan or higher.

Setup steps:
1. Go to Settings → Integrations → Slack
2. Click "Connect to Slack"
3. You will be redirected to Slack OAuth — authorize TechCorp
4. Select the Slack workspace to connect
5. Choose the default notification channel (e.g., #techcorp-notifications)
6. Configure which events to sync: task creation, due date reminders, comments, completions

Features after connecting:
- Create TechCorp tasks directly from Slack messages (right-click → Create Task)
- Receive task notifications in your chosen Slack channel
- @mention teammates in TechCorp comments → they get a Slack DM
- Bidirectional status sync

Known issue: Slack notifications may have 2–5 minute delays during peak hours. This is being monitored.

Troubleshooting:
- Not receiving notifications: check your Slack notification channel settings
- Re-authorize if you see "connection lost" errors: Settings → Integrations → Slack → Reconnect""",
    ),
    (
        "KB-INT-002",
        "integration",
        "Google Workspace Integration (Drive, Calendar, Gmail)",
        """Connect TechCorp Workspace with Google Workspace for seamless collaboration.

Requirements: Growth plan or higher.

Setup steps:
1. Settings → Integrations → Google Workspace
2. Click "Connect Google Account"
3. Sign in and authorize requested permissions
4. Select which features to enable:
   - Google Drive: Attach Drive files to tasks
   - Google Calendar: Sync task due dates as calendar events
   - Gmail: Create tasks from emails

Google Drive:
- Attach Drive files directly to tasks and comments
- Files open in Drive without downloading

Google Calendar:
- Task due dates appear as calendar events
- Sync is one-way: TechCorp → Calendar

Gmail:
- Forward emails to tasks@techcorp.io to create tasks automatically
- Or use the Gmail sidebar add-on (install from Google Workspace Marketplace)""",
    ),
    (
        "KB-INT-003",
        "integration",
        "Microsoft 365 Integration (Teams, SharePoint, Outlook)",
        """Connect TechCorp Workspace with Microsoft 365.

Requirements: Growth plan or higher.

Setup steps:
1. Settings → Integrations → Microsoft 365
2. Click "Connect Microsoft Account"
3. Sign in with your Microsoft account and authorize
4. Configure:
   - Teams: Choose channel for notifications
   - SharePoint: Connect document libraries
   - Outlook: Enable email-to-task

Microsoft Teams:
- Receive TechCorp notifications in a Teams channel
- Add the TechCorp tab to Teams channels for embedded project view

SharePoint:
- Attach SharePoint documents to tasks
- Browse SharePoint libraries from within TechCorp

Outlook:
- Create tasks from Outlook emails via the TechCorp add-in
- Install the add-in from Microsoft AppSource: search "TechCorp Workspace" """,
    ),
    (
        "KB-INT-004",
        "integration",
        "GitHub and GitLab Integration",
        """Link pull requests and branches to TechCorp tasks.

Requirements: Growth plan or higher.

GitHub setup:
1. Settings → Integrations → GitHub
2. Click "Connect GitHub"
3. Authorize the TechCorp GitHub App for your org/repos
4. In any task, add a GitHub PR link — it will auto-sync status

GitLab setup:
1. Settings → Integrations → GitLab
2. Enter your GitLab URL and Personal Access Token (read_api scope)
3. Select which projects to link

Features:
- Link PRs/MRs to tasks: paste the URL in the task description
- See PR status (open/merged/closed) directly on the task card
- Branch names auto-link if they contain the task ID (e.g., feature/TKT-1234-new-login)
- Merge events can auto-close linked tasks (configurable)""",
    ),
    (
        "KB-INT-005",
        "integration",
        "Zapier Integration and Webhooks",
        """Automate TechCorp with 3,000+ apps via Zapier, or build custom integrations with webhooks.

Zapier (no-code automation):
1. Go to zapier.com and create a free account
2. Search for "TechCorp Workspace" in the app directory
3. Authenticate with your TechCorp API token (Settings → API → New Token)
4. Build Zaps: e.g., "New task in TechCorp → Send Slack message"

Popular Zap templates:
- New TechCorp task → Create Trello card
- New form submission → Create TechCorp task
- TechCorp task completed → Update Google Sheet row

Webhooks (for developers):
1. Settings → Webhooks → Add Endpoint
2. Enter your endpoint URL (must accept POST requests)
3. Select events to subscribe to: task.created, task.completed, comment.added, member.joined
4. Test with "Send Test Event"
5. Payload format: JSON with event type, timestamp, and resource data

API access for custom integrations:
- Docs: docs.techcorp.io/api
- Rate limit: 1,000 req/hour (Business), 10,000 req/hour (Enterprise)
- Authentication: Bearer token in Authorization header""",
    ),
    (
        "KB-INT-006",
        "integration",
        "Troubleshooting Integration Connection Issues",
        """If your integration is not working, follow these steps.

General troubleshooting:
1. Go to Settings → Integrations and check the connection status
2. Look for a red "Disconnected" or "Error" badge
3. Click "Reconnect" to re-authorize
4. If reconnection fails, try revoking access from the external app and re-connecting from scratch

Common causes of integration failures:
- OAuth token expired (especially after password changes)
- Insufficient permissions granted during OAuth
- Workspace plan downgraded (integrations require Growth+)
- Admin disabled third-party integrations in your org settings

Slack-specific: If Slack notifications stopped, check that the TechCorp app is still installed in your Slack workspace (Apps → Manage → TechCorp).

Google-specific: If Drive attachments fail, re-authorize with "See, edit, create, and delete all of your Google Drive files" permission.

If the issue persists after reconnecting, please report it with your integration type and any error messages. Technical issues affecting production systems may require escalation to our engineering team.""",
    ),

    # ── BILLING ─────────────────────────────────────────────────────────────
    (
        "KB-BILL-001",
        "billing",
        "How to Access Your Billing Portal",
        """To view and manage your TechCorp Workspace subscription:

1. Go to app.techcorp.io/settings/billing
2. Or: Settings → Billing

In the billing portal you can:
- View your current plan and pricing
- Download invoices (PDF)
- Update payment method (credit card or ACH)
- View upcoming renewal date
- See seat count and usage

Note: Billing changes such as upgrades, downgrades, or cancellations take effect at the next billing cycle.

For refund requests, invoice adjustments, or payment disputes — these are handled by our billing team directly and cannot be processed through the portal or by support agents. Use the billing portal to find your invoice number, then contact billing@techcorp.io.""",
    ),
    (
        "KB-BILL-002",
        "billing",
        "Plan Tiers, Upgrades, and Downgrades",
        """TechCorp Workspace plan overview:

Plan tiers: Free → Growth → Business → Enterprise

Free plan:
- Up to 5 members, 5 projects, 5GB storage
- Core task management features
- No integrations

Growth plan:
- Unlimited members and projects, 50GB storage per member
- All integrations (Slack, Google, Microsoft, GitHub)
- Advanced views (Gantt, Timeline)

Business plan:
- Everything in Growth
- SSO (SAML 2.0), Audit Logs, Advanced Analytics
- Priority support, custom roles, 99.9% SLA

Enterprise plan:
- Everything in Business
- Dedicated CSM, custom contract, volume pricing
- Custom security reviews

Upgrading: takes effect immediately, prorated billing
Downgrading: takes effect at next billing cycle, data preserved for 30 days then archived

For pricing information, visit techcorp.io/pricing or contact our sales team at sales@techcorp.io. Support agents cannot quote prices or discounts.""",
    ),

    # ── PROJECT MANAGEMENT ──────────────────────────────────────────────────
    (
        "KB-PROJ-001",
        "product",
        "Getting Started with Project Management",
        """Create and manage projects in TechCorp Workspace.

Creating a project:
1. Click "+ New Project" in the left sidebar
2. Enter project name and optional description
3. Choose a template (40+ available) or start blank
4. Set visibility: Private, Team, or Public
5. Add members from your team

Project views:
- List: Traditional task list with columns
- Kanban Board: Drag-and-drop cards by status
- Calendar: Tasks on a monthly/weekly calendar
- Gantt: Timeline view with dependencies (Growth+)
- Table: Spreadsheet-style with custom fields

Task creation:
- Click "+ Add Task" in any view
- Set: title, assignee, due date, priority, labels
- Add subtasks, dependencies, attachments, and comments

Templates:
- 40+ pre-built templates for common workflows
- Create custom templates from existing projects: Project → ⋮ → Save as Template""",
    ),
    (
        "KB-PROJ-002",
        "product",
        "Task Management — Subtasks, Dependencies, and Priorities",
        """Advanced task management features in TechCorp Workspace.

Subtasks:
- Open a task → click "+ Add Subtask"
- Subtasks have their own assignees, due dates, and status
- Parent task shows completion % based on subtask completion

Dependencies:
- Open a task → Dependencies → "+ Add Dependency"
- Types: Blocks, Is blocked by, Relates to
- Gantt view shows dependencies as arrows
- Due date warnings when a dependency blocks the critical path

Task priorities:
- Urgent: Needs immediate attention (shown in red)
- High: Important, do soon (orange)
- Medium: Default (yellow)
- Low: Nice to have (grey)

Time tracking:
- Start/stop timer on any task, or log time manually
- View time reports: Analytics → Time Tracking
- Export timesheet: CSV or Excel

Recurring tasks:
- Open task → Recurrence → Set frequency (daily/weekly/monthly/custom)
- Recurring tasks auto-create the next instance on completion""",
    ),

    # ── NOTIFICATIONS ───────────────────────────────────────────────────────
    (
        "KB-NOTIF-001",
        "product",
        "Configuring Notifications",
        """Customize how and when TechCorp Workspace notifies you.

In-app notifications:
- Click the bell icon (top right) to see all notifications
- Settings → Notifications → In-App: configure which events trigger notifications

Email notifications:
- Settings → Notifications → Email
- Choose: Immediate, Daily Digest, or Off
- You can select specific event types (assigned to me, @mentioned, due date reminders)

Third-party notifications:
- Slack: Settings → Integrations → Slack → Notification Settings
- MS Teams: Settings → Integrations → Microsoft 365 → Teams Notifications

Do Not Disturb:
- Settings → Notifications → Do Not Disturb
- Set quiet hours (e.g., 10pm–8am)
- Emergency notifications from admins bypass DND

If you're not receiving email notifications:
1. Check your spam folder
2. Whitelist noreply@techcorp.io
3. Verify your email address in Settings → Profile
4. Check your email notification setting is not "Off" """,
    ),

    # ── REPORTING & ANALYTICS ───────────────────────────────────────────────
    (
        "KB-REPORT-001",
        "product",
        "Dashboards and Reporting",
        """TechCorp Workspace reporting and analytics features.

Dashboards (Growth+):
- Create custom dashboards: Analytics → New Dashboard
- Available widgets: Task completion, Team workload, Burndown chart, Overdue tasks, Time tracked
- Share dashboards with team members or make public

Built-in reports:
- Workload report: See each member's task load
- Completion rate: Track how many tasks are completed on time
- Overdue tasks: Identify bottlenecks
- Time tracking: Billable hours per project/member

Exporting data:
- Tasks: Project → Export → CSV or Excel
- Time reports: Analytics → Time Tracking → Export
- Full workspace backup: Settings → Data → Export (JSON format)

Advanced analytics (Business+):
- Custom report builder with filters
- Trend analysis over time
- Cross-project reporting
- Integration with BI tools via API or webhook""",
    ),

    # ── SECURITY & COMPLIANCE ───────────────────────────────────────────────
    (
        "KB-SEC-001",
        "product",
        "Security Features and Data Protection",
        """TechCorp Workspace security overview.

Data encryption:
- At rest: AES-256 encryption for all stored data
- In transit: TLS 1.3 for all connections
- Database backups: encrypted and stored in multiple regions

Access controls:
- Role-based access control (RBAC) for all resources
- IP allowlisting available on Enterprise plan
- Session timeout configurable by admins

Compliance certifications:
- SOC 2 Type II (annual audit)
- GDPR compliant (EU data residency available on Enterprise)
- CCPA compliant

Audit logs (Business+):
- Full audit trail of all actions: login, settings changes, data exports
- 1-year log retention (Enterprise: 7 years)
- Export logs: Settings → Security → Audit Logs → Export

GDPR data requests:
- Data export: Settings → Privacy → Export My Data
- Account deletion: Settings → Privacy → Delete Account (or contact support)
- Note: GDPR data deletion requests from enterprise customers require verification and are processed by our legal team.""",
    ),

    # ── API ACCESS ──────────────────────────────────────────────────────────
    (
        "KB-API-001",
        "technical",
        "API Access and Authentication",
        """TechCorp Workspace REST API — available on Business+ plans.

Getting started:
1. Go to Settings → API → New Token
2. Give the token a name and set expiry (optional)
3. Copy the token immediately — it is only shown once
4. Use Bearer token authentication:
   Authorization: Bearer YOUR_TOKEN_HERE

API documentation: docs.techcorp.io/api

Rate limits:
- Business plan: 1,000 requests/hour
- Enterprise plan: 10,000 requests/hour
- Limits are per token, not per user

Common API endpoints:
- GET /api/v1/tasks — list tasks
- POST /api/v1/tasks — create task
- GET /api/v1/projects — list projects
- GET /api/v1/members — list team members

Webhooks:
- Settings → Webhooks → Add Endpoint
- Receive real-time events as POST requests to your URL
- Events: task.created, task.updated, task.completed, member.joined, comment.added

If you encounter API errors or rate limit issues affecting production integrations, our engineering team can assist — this may require escalation.""",
    ),

    # ── KNOWN ISSUES ────────────────────────────────────────────────────────
    (
        "KB-STATUS-001",
        "technical",
        "Known Issues and System Status",
        """Check current system status and known issues.

Status page: status.techcorp.io (bookmark this for real-time updates)

Current known issues (as of latest update):
- Gantt view export to PDF intermittently fails — fix shipping in v3.2.1 (releasing next Tuesday)
- Slack notifications may have 2–5 minute delays during peak hours — being monitored, no action needed

How to report a new bug:
1. Go to Settings → Help → Report a Bug
2. Include: browser/OS version, steps to reproduce, screenshot if possible
3. Our engineering team reviews all reports

If you experience data loss, corruption, or a security vulnerability:
- These require immediate escalation to our engineering team
- Contact: bugs@techcorp.io or use the escalation option in support

For major outages affecting your business, our Enterprise SLA guarantees 99.9% uptime with 15-minute response for critical incidents.""",
    ),

    # ── ONBOARDING ──────────────────────────────────────────────────────────
    (
        "KB-ONBOARD-001",
        "product",
        "Getting Started — Onboarding Checklist",
        """Welcome to TechCorp Workspace! Here's your onboarding checklist.

Week 1 — Setup:
☐ Complete your profile (Settings → Profile → add photo, timezone)
☐ Set up 2FA (Settings → Security → Two-Factor Auth)
☐ Invite your team (Settings → Team → Invite Member)
☐ Create your first project (+ New Project)
☐ Explore views: switch between List, Board, Calendar

Week 2 — Integrations:
☐ Connect Slack or Teams for notifications
☐ Link Google Drive or SharePoint for file attachments
☐ Set up time tracking if needed (project → Time Tracking settings)

Tips for success:
- Use templates to get started quickly (40+ available)
- Keyboard shortcut: 'T' to create a new task anywhere
- Use @mentions in comments to notify teammates
- Set recurring tasks for weekly standups or reports

Resources:
- Video tutorials: techcorp.io/learn
- Community forum: community.techcorp.io
- Support: techcorp.io/support""",
    ),

    # ── ESCALATION GUIDANCE ─────────────────────────────────────────────────
    (
        "KB-ESC-001",
        "escalation",
        "When Support Will Escalate Your Issue",
        """TechCorp Support escalation policy — what gets escalated and to whom.

Issues handled immediately by AI support:
- Password resets and login help
- Integration setup guides
- Feature explanations
- Account settings navigation
- Known issue status checks
- Team member management

Issues escalated to human specialists:
- Pricing, discounts, or plan negotiation → Sales team (sales@techcorp.io)
- Refund requests or billing disputes → Billing team (billing@techcorp.io)
- Legal, GDPR, or compliance questions → Legal team (legal@techcorp.io)
- Very angry customers or requests to speak to a human → Senior CSM (csm@techcorp.io)
- Complex technical issues not resolved after 2 attempts → Engineering (bugs@techcorp.io)
- Enterprise accounts always offered a dedicated CSM

Escalation SLAs:
- Critical (data loss, security breach): 15-minute response
- High (billing, legal): 1-hour response
- Medium (unresolved technical): 4-hour response
- Low (feature requests): 24-hour response

After escalation, you will receive:
- Your ticket reference number
- Name of the team handling your issue
- Expected response time""",
    ),
    (
        "KB-ESC-002",
        "escalation",
        "Refund and Cancellation Policy",
        """TechCorp Workspace refund and cancellation information.

Cancellation:
- You can cancel anytime from the billing portal: app.techcorp.io/settings/billing
- After cancellation: your account stays active until the end of the billing period
- After the period ends: account is frozen (data preserved), not deleted
- Data retention after freeze: 90 days before permanent deletion

Downgrading:
- Downgrades take effect at the next billing cycle
- Features from the higher plan remain available until cycle end
- Data not supported by lower plan is archived for 30 days

Refund requests:
- Refund decisions are made case-by-case by our billing team
- AI support cannot process or approve refunds
- Contact: billing@techcorp.io with your invoice number
- Expected response: within 2 business days

Chargebacks/disputes:
- Please contact us before initiating a chargeback — we can usually resolve it faster directly
- Contact: billing@techcorp.io""",
    ),

    # ── MOBILE ──────────────────────────────────────────────────────────────
    (
        "KB-MOBILE-001",
        "product",
        "Mobile App — Beta Access",
        """TechCorp Workspace mobile app (iOS and Android) is currently in beta.

Current status: Beta — available to invited users only

How to join the beta:
- iOS: Request access at techcorp.io/mobile-beta → receive TestFlight invite
- Android: Request access at techcorp.io/mobile-beta → receive Play Store beta link

Current beta features:
- View and update tasks
- Add comments and @mentions
- Receive push notifications
- Basic project browsing

Not yet available in beta:
- Creating projects
- Gantt/Timeline view
- File attachments
- Time tracking

Feedback: Use the in-app feedback button (shake device) or email mobile-beta@techcorp.io

General release timeline: Not confirmed. We do not promise specific release dates.

In the meantime, TechCorp Workspace is fully responsive in mobile browsers — access app.techcorp.io from Safari or Chrome on your phone.""",
    ),
]


async def get_embedding(client: openai.AsyncOpenAI, text: str) -> list[float]:
    """Generate embedding for a text using OpenAI text-embedding-3-small."""
    response = await client.embeddings.create(
        model=EMBED_MODEL,
        input=text[:8000],  # Safety truncation
    )
    return response.data[0].embedding


async def seed_kb(db_url: str, openai_api_key: str) -> None:
    log.info("Connecting to database...")
    conn = await asyncpg.connect(db_url)
    client = openai.AsyncOpenAI(api_key=openai_api_key)

    log.info(f"Seeding {len(KB_ARTICLES)} KB articles with embeddings...")

    inserted = 0
    updated = 0
    errors = 0

    for kb_ref, category, title, content in KB_ARTICLES:
        try:
            # Generate embedding for title + content combined
            embed_text = f"{title}\n\n{content}"
            embedding = await get_embedding(client, embed_text)

            vec_str = "[" + ",".join(str(x) for x in embedding) + "]"

            result = await conn.fetchrow(
                """
                INSERT INTO knowledge_base (kb_ref, category, title, content, embedding, word_count, is_active)
                VALUES ($1, $2, $3, $4, $5::vector, $6, TRUE)
                ON CONFLICT (kb_ref) DO UPDATE SET
                    category   = EXCLUDED.category,
                    title      = EXCLUDED.title,
                    content    = EXCLUDED.content,
                    embedding  = EXCLUDED.embedding,
                    word_count = EXCLUDED.word_count,
                    version    = knowledge_base.version + 1,
                    updated_at = NOW()
                RETURNING id, version
                """,
                kb_ref, category, title, content, vec_str, len(content.split()),
            )

            if result["version"] == 1:
                log.info(f"  ✅ INSERTED [{category}] {kb_ref}: {title}")
                inserted += 1
            else:
                log.info(f"  🔄 UPDATED  [{category}] {kb_ref}: {title} (v{result['version']})")
                updated += 1

        except Exception as e:
            log.error(f"  ❌ FAILED {kb_ref}: {e}")
            errors += 1

    await conn.close()

    log.info("")
    log.info("=" * 60)
    log.info(f"KB Seeding Complete")
    log.info(f"  Inserted : {inserted}")
    log.info(f"  Updated  : {updated}")
    log.info(f"  Errors   : {errors}")
    log.info(f"  Total    : {len(KB_ARTICLES)}")
    log.info("=" * 60)

    if errors > 0:
        sys.exit(1)


def main():
    db_url = os.getenv("DATABASE_URL")
    openai_api_key = os.getenv("OPENAI_API_KEY")

    if not db_url:
        log.error("DATABASE_URL not set in .env")
        sys.exit(1)
    if not openai_api_key:
        log.error("OPENAI_API_KEY not set in .env")
        sys.exit(1)

    asyncio.run(seed_kb(db_url, openai_api_key))


if __name__ == "__main__":
    main()
