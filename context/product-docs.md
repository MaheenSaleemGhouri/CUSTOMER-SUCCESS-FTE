# TechCorp Workspace — Product Documentation

## Core Features

### 1. Project Management
- **Tasks:** Create, assign, due dates, subtasks, dependencies
- **Boards:** Kanban, List, Calendar, Gantt views
- **Milestones:** Track project phases
- **Templates:** 40+ pre-built project templates
- **Time Tracking:** Built-in timer, manual entry, reports

### 2. Team Collaboration
- **Comments:** @mentions, threaded replies, emoji reactions
- **File Sharing:** Drag-and-drop, 5GB–unlimited storage by plan
- **Notifications:** In-app, email, Slack, MS Teams
- **Activity Feed:** Real-time updates
- **Permissions:** Role-based (Owner, Admin, Member, Guest)

### 3. Integrations (Growth+ plans)
- **Slack** — bidirectional sync, task creation from messages
- **Google Workspace** — Drive, Calendar, Gmail
- **Microsoft 365** — Teams, SharePoint, Outlook
- **Zoom** — Meeting links in tasks
- **GitHub / GitLab** — PR linking, branch tracking
- **Zapier** — 3,000+ app connections
- **Webhooks** — Custom integrations via REST API

### 4. Reporting & Analytics
- **Dashboards:** Custom widgets, team workload, burndown charts
- **Reports:** Time tracking, completion rates, overdue tasks
- **Export:** CSV, PDF, Excel
- **Business+:** Advanced analytics, custom reports

### 5. Security & Compliance
- **SSO:** SAML 2.0 (Business+ only)
- **2FA:** TOTP and SMS
- **Audit Logs:** Business+ only
- **Data Encryption:** AES-256 at rest, TLS 1.3 in transit
- **Compliance:** SOC 2 Type II, GDPR, CCPA

## Common How-To Guides

### Password Reset
1. Go to app.techcorp.io/login
2. Click "Forgot Password"
3. Enter your email → check inbox
4. Link expires in 24 hours
5. If no email: check spam → whitelist noreply@techcorp.io

### Adding Team Members
1. Go to Settings → Team
2. Click "Invite Member"
3. Enter email + select role
4. Invitation expires in 7 days
5. Resend from Settings → Team → Pending Invites

### Slack Integration Setup
1. Settings → Integrations → Slack
2. Click "Connect to Slack"
3. Authorize in Slack OAuth
4. Choose notification channel
5. Configure which events to sync

### Billing & Subscription
- Billing portal: app.techcorp.io/settings/billing
- Changes take effect: next billing cycle
- Downgrade: data preserved 30 days, then archived
- Cancel: account active until period end, then frozen
- Refunds: handled case-by-case by billing team — NOT handled by AI

### API Access (Business+)
- API docs: docs.techcorp.io/api
- Generate token: Settings → API → New Token
- Rate limit: 1,000 req/hour (Business), 10,000 req/hour (Enterprise)
- Webhooks: Settings → Webhooks → Add Endpoint

## Known Issues & Status
- Status page: status.techcorp.io
- Current known issue: Gantt view export to PDF intermittently fails (fix in v3.2.1, releasing next Tuesday)
- Slack notifications may have 2-5 min delay during peak hours (known, being monitored)

## Pricing (Agents must NEVER quote or negotiate — escalate)
- Pricing page: techcorp.io/pricing
- Discounts: handled by sales team only
- Never reveal internal pricing logic or negotiation room

## Upcoming Features (Do NOT promise timelines)
- Mobile app (iOS/Android) — in beta
- AI task suggestions — roadmap
- Custom roles & permissions — roadmap
- Offline mode — under consideration
