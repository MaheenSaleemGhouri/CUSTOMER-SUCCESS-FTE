'use strict';

// ─── Config ───────────────────────────────────────────────
const API_BASE = 'http://localhost:8000';
const WS_BASE  = 'ws://localhost:8000';

const ticketId = new URLSearchParams(window.location.search).get('id');

// ─── Guard ────────────────────────────────────────────────
if (!ticketId) {
  document.getElementById('page-content').innerHTML =
    '<div class="card" style="text-align:center;padding:40px;color:var(--slate-400)">' +
    'No ticket ID provided. <a href="index.html">Submit a request</a></div>';
}

// ─── DOM refs ─────────────────────────────────────────────
const wsConnecting   = document.getElementById('ws-connecting');
const wsConnected    = document.getElementById('ws-connected');
const wsDisconnected = document.getElementById('ws-disconnected');
const wsUrlEl        = document.getElementById('ws-url-display');
const wsRetryBtn     = document.getElementById('ws-retry-btn');

const chatThread     = document.getElementById('chat-thread');
const typingEl       = document.getElementById('typing-indicator');
const escalationEl   = document.getElementById('escalation-notice');
const escalationTeam = document.getElementById('escalation-team');
const ticketError    = document.getElementById('ticket-error');
const ticketErrorTxt = document.getElementById('ticket-error-text');

const headerStatus   = document.getElementById('header-status');
const headerPriority = document.getElementById('header-priority');
const headerChannel  = document.getElementById('header-channel');
const headerRef      = document.getElementById('header-ref');
const headerSubject  = document.getElementById('header-subject');
const headerMeta     = document.getElementById('header-meta');

// ─── WS state ─────────────────────────────────────────────
function setWsState(state) {
  wsConnecting.classList.add('hidden');
  wsConnected.classList.add('hidden');
  wsDisconnected.classList.add('hidden');
  if (state === 'connecting')   wsConnecting.classList.remove('hidden');
  if (state === 'connected')    wsConnected.classList.remove('hidden');
  if (state === 'disconnected') wsDisconnected.classList.remove('hidden');
}

// ─── WebSocket ────────────────────────────────────────────
let ws;
let fallbackInterval;
let lastMessageIds = new Set();

function connectWS() {
  if (!ticketId) return;
  setWsState('connecting');
  const url = `${WS_BASE}/ws/ticket/${ticketId}`;
  if (wsUrlEl) wsUrlEl.textContent = url;

  try {
    ws = new WebSocket(url);
  } catch (e) {
    setWsState('disconnected');
    startFallback();
    return;
  }

  ws.onopen = () => {
    setWsState('connected');
    clearInterval(fallbackInterval);
  };

  ws.onclose = () => {
    setWsState('disconnected');
    startFallback();
  };

  ws.onerror = () => {
    setWsState('disconnected');
    startFallback();
  };

  ws.onmessage = (e) => {
    let d;
    try { d = JSON.parse(e.data); } catch (_) { return; }

    // Backend currently sends { type: 'init'|'update', ticket: {...} } + 'ping'
    if (d.type === 'init' || d.type === 'update') {
      if (d.ticket) applyTicketState(d.ticket);
    } else if (d.type === 'new_message') {
      appendMessage(d.message);
    } else if (d.type === 'status_change') {
      updateStatus(d.status);
    } else if (d.type === 'escalated') {
      showEscalation(d.team);
    } else if (d.type === 'typing_start') {
      showTyping();
    } else if (d.type === 'typing_stop') {
      hideTyping();
    }
  };
}

function startFallback() {
  clearInterval(fallbackInterval);
  fallbackInterval = setInterval(loadTicket, 10000);
}

wsRetryBtn && wsRetryBtn.addEventListener('click', () => {
  if (ws) { try { ws.close(); } catch (_) {} }
  connectWS();
});

// ─── Initial + fallback load ──────────────────────────────
async function loadTicket() {
  if (!ticketId) return;
  try {
    const res = await fetch(`${API_BASE}/support/ticket/${encodeURIComponent(ticketId)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    applyTicketState(data);
    ticketError.classList.add('hidden');
  } catch (err) {
    ticketErrorTxt.textContent = 'Unable to load ticket. ' + (err.message || '');
    ticketError.classList.remove('hidden');
  }
}

// ─── Render state ─────────────────────────────────────────
function applyTicketState(t) {
  if (!t) return;

  // Header
  if (t.status) {
    headerStatus.textContent = formatLabel(t.status);
    headerStatus.className = 'badge badge-status-' + t.status;
  }
  if (t.priority) {
    headerPriority.textContent = formatLabel(t.priority);
    headerPriority.className = 'badge badge-priority-' + t.priority;
  }
  if (t.channel) {
    headerChannel.textContent = formatChannel(t.channel);
    headerChannel.className = 'badge badge-channel-' + t.channel;
  }
  if (t.ticket_ref || t.ticket_id) {
    headerRef.textContent = t.ticket_ref || t.ticket_id;
  }
  if (t.subject) headerSubject.textContent = t.subject;

  const parts = [];
  if (t.created_at) parts.push(`Opened ${timeAgo(t.created_at)}`);
  if (t.customer_email || t.email) parts.push(t.customer_email || t.email);
  if (parts.length) headerMeta.textContent = parts.join(' · ');

  // Escalation
  if (t.status === 'escalated' || t.escalated_to) {
    const team = (t.escalated_to || 'support').replace('@techcorp.io', '').replace(/\./g, ' ');
    showEscalation(team);
  }

  // Messages
  if (Array.isArray(t.messages)) {
    renderMessages(t.messages);
  } else if (Array.isArray(t.conversation)) {
    renderMessages(t.conversation);
  }
}

function renderMessages(messages) {
  if (!messages || !messages.length) {
    chatThread.innerHTML = '<div class="empty-thread">No messages yet. Alex will respond shortly.</div>';
    return;
  }

  chatThread.innerHTML = '';
  lastMessageIds = new Set();

  messages.forEach((m) => {
    chatThread.appendChild(buildMessage(m));
    if (m.id || m.message_id) lastMessageIds.add(m.id || m.message_id);
  });
}

function appendMessage(m) {
  const id = m.id || m.message_id;
  if (id && lastMessageIds.has(id)) return;
  chatThread.appendChild(buildMessage(m));
  if (id) lastMessageIds.add(id);
}

function buildMessage(m) {
  const direction = (m.direction || m.sender || '').toLowerCase();
  const isCustomer = direction === 'inbound' || direction === 'customer' || direction === 'user';

  const row = document.createElement('div');
  row.className = 'msg-row ' + (isCustomer ? 'customer' : 'agent');

  const avatar = document.createElement('div');
  avatar.className = 'avatar ' + (isCustomer ? 'avatar-customer' : 'avatar-agent');
  avatar.textContent = isCustomer ? getInitials(m.sender_name || 'You') : 'AI';

  const body = document.createElement('div');
  body.className = 'msg-body';

  const bubble = document.createElement('div');
  bubble.className = 'bubble ' + (isCustomer ? 'bubble-customer' : 'bubble-agent');
  bubble.textContent = m.content || m.body || m.message || '';

  const meta = document.createElement('div');
  meta.className = 'msg-meta';
  const ts = m.created_at || m.timestamp || m.sent_at;
  if (isCustomer) {
    meta.textContent = ts ? formatTime(ts) : '';
  } else {
    meta.textContent = 'Alex (AI)' + (ts ? ' · ' + formatTime(ts) : '');
  }

  body.appendChild(bubble);
  body.appendChild(meta);
  row.appendChild(avatar);
  row.appendChild(body);
  return row;
}

function showEscalation(team) {
  escalationTeam.textContent = team || 'support';
  escalationEl.classList.remove('hidden');
}
function updateStatus(status) {
  if (!status) return;
  headerStatus.textContent = formatLabel(status);
  headerStatus.className = 'badge badge-status-' + status;
}
function showTyping() { typingEl.style.display = 'flex'; }
function hideTyping() { typingEl.style.display = 'none'; }

// ─── Helpers ──────────────────────────────────────────────
function formatLabel(s) {
  return String(s || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}
function formatChannel(c) {
  if (c === 'web_form') return 'Web Form';
  if (c === 'whatsapp') return 'WhatsApp';
  if (c === 'email')    return 'Email';
  return formatLabel(c);
}
function getInitials(name) {
  return String(name).trim().split(/\s+/).slice(0, 2).map((w) => w[0] || '').join('').toUpperCase() || 'U';
}
function formatTime(iso) {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch (_) { return ''; }
}
function timeAgo(iso) {
  try {
    const d = new Date(iso);
    const diff = Math.floor((Date.now() - d.getTime()) / 1000);
    if (diff < 60)     return 'just now';
    if (diff < 3600)   return Math.floor(diff / 60) + ' min ago';
    if (diff < 86400)  return Math.floor(diff / 3600) + ' hr ago';
    return Math.floor(diff / 86400) + ' day ago';
  } catch (_) { return ''; }
}

// ─── Init ─────────────────────────────────────────────────
if (ticketId) {
  loadTicket();
  connectWS();
}
