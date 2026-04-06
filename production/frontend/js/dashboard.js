'use strict';

// ─── Config ───────────────────────────────────────────────
const API_BASE = 'http://localhost:8000';
const PAGE_SIZE = 15;

let currentPage    = 1;
let currentStatus  = '';
let currentChannel = '';
let currentSearch  = '';
let totalTickets   = 0;
let countdown      = 30;

// ─── Auth ─────────────────────────────────────────────────
const authOverlay = document.getElementById('auth-overlay');
const authInput   = document.getElementById('auth-key');
const authSubmit  = document.getElementById('auth-submit');
const authErr     = document.getElementById('auth-err');

function apiHeaders() {
  return {
    'Content-Type': 'application/json',
    'X-Admin-Key': localStorage.getItem('admin_key') || '',
  };
}

async function testAuth(key) {
  try {
    const res = await fetch(`${API_BASE}/dashboard/stats`, {
      headers: { 'X-Admin-Key': key },
    });
    return res.ok || res.status === 404; // 404 = endpoint missing, auth OK
  } catch (_) { return false; }
}

async function initAuth() {
  const stored = localStorage.getItem('admin_key');
  if (stored) {
    const ok = await testAuth(stored);
    if (ok) {
      authOverlay.classList.add('hidden');
      boot();
      return;
    }
    localStorage.removeItem('admin_key');
  }
  // Try with no key — dev mode may allow
  const devOk = await testAuth('');
  if (devOk) {
    localStorage.setItem('admin_key', '');
    authOverlay.classList.add('hidden');
    boot();
    return;
  }
  authOverlay.classList.remove('hidden');
  authInput.focus();
}

authSubmit.addEventListener('click', async () => {
  const key = authInput.value.trim();
  authErr.classList.add('hidden');
  const ok = await testAuth(key);
  if (ok) {
    localStorage.setItem('admin_key', key);
    authOverlay.classList.add('hidden');
    boot();
  } else {
    authErr.classList.remove('hidden');
  }
});
authInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') authSubmit.click(); });

// ─── Boot ─────────────────────────────────────────────────
function boot() {
  fetchAll();
  startCountdown();
  wireFilters();
}

// ─── Fetch all ────────────────────────────────────────────
async function fetchAll() {
  await Promise.all([
    loadStats(),
    loadTickets(),
    loadEscalations(),
    loadKB(),
    loadMetrics(),
  ]);
}

// ─── Stats ────────────────────────────────────────────────
async function loadStats() {
  try {
    const r = await fetch(`${API_BASE}/dashboard/stats`, { headers: apiHeaders() });
    if (!r.ok) return;
    const d = await r.json();

    setText('stat-total',      d.total_tickets ?? '—');
    setText('stat-total-sub',  d.today_new != null ? `+${d.today_new} today` : '—');
    setText('stat-open',       d.open_tickets ?? '—');
    setText('stat-open-sub',   d.open_percentage != null ? `${d.open_percentage}% of total` : '—');
    setText('stat-progress',   d.in_progress_tickets ?? '—');
    setText('stat-progress-sub', d.avg_response_time || '—');
    setText('stat-escalated',  d.escalated_tickets ?? '—');
    setText('stat-escalated-sub', d.escalation_rate != null ? `${d.escalation_rate}% rate` : '—');
    setText('stat-resolved',   d.resolved_tickets ?? '—');
    setText('stat-resolved-sub', d.resolution_rate != null ? `${d.resolution_rate}% rate` : '—');
    setText('stat-ai',         d.ai_resolved ?? d.auto_resolved ?? '—');
    setText('stat-ai-sub',     d.auto_percentage != null ? `${d.auto_percentage}% auto` : '—');
    setText('stat-kb',         d.kb_coverage != null ? `${d.kb_coverage}%` : '—');
    setText('stat-kb-sub',     d.kb_articles_embedded != null ? `${d.kb_articles_embedded}/${d.kb_articles_total || 0} articles` : '—');
    setText('stat-sentiment',  d.avg_sentiment != null ? Number(d.avg_sentiment).toFixed(2) : '—');
    setText('stat-sentiment-sub', d.sentiment_trend || 'live');
  } catch (e) { /* silent */ }
}

// ─── Tickets ──────────────────────────────────────────────
async function loadTickets() {
  const tbody = document.getElementById('tickets-tbody');
  try {
    const offset = (currentPage - 1) * PAGE_SIZE;
    const params = new URLSearchParams({ limit: PAGE_SIZE, offset });
    if (currentStatus)  params.set('status',  currentStatus);
    if (currentChannel) params.set('channel', currentChannel);
    if (currentSearch)  params.set('search',  currentSearch);

    const r = await fetch(`${API_BASE}/dashboard/tickets?${params}`, { headers: apiHeaders() });
    if (!r.ok) throw new Error('Load failed');
    const d = await r.json();
    const tickets = d.tickets || d.items || [];
    totalTickets = d.total || tickets.length;

    if (!tickets.length) {
      tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--slate-500);padding:30px;">No tickets found</td></tr>';
      renderPagination();
      return;
    }

    tbody.innerHTML = tickets.map((t) => renderRow(t)).join('');
    renderPagination();
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:#FCA5A5;padding:24px;">Failed to load tickets</td></tr>';
  }
}

function renderRow(t) {
  const ticketRef = t.ticket_ref || t.ticket_id || '—';
  const name    = escapeHtml(t.customer_name || t.name || 'Unknown');
  const plan    = escapeHtml(t.customer_plan || t.plan || 'free');
  const subject = escapeHtml(t.subject || '—');
  const channel = t.channel || 'web_form';
  const status  = t.status  || 'open';
  const prio    = t.priority || 'medium';
  const sent    = typeof t.sentiment === 'number' ? t.sentiment : 0.5;
  const resp    = t.response_time || t.first_response || '—';

  const sentColor = sent > 0.6 ? '#10B981' : sent >= 0.3 ? '#EAB308' : '#EF4444';
  const sentWidth = Math.max(0, Math.min(100, Math.round(sent * 100)));

  return `
    <tr>
      <td class="td-id">${escapeHtml(ticketRef)}</td>
      <td class="td-customer"><div class="name">${name}</div><div class="plan">${plan}</div></td>
      <td class="td-subject">${subject}</td>
      <td><span class="badge badge-channel-${channel}">${formatChannel(channel)}</span></td>
      <td><span class="badge badge-status-${status}">${formatLabel(status)}</span></td>
      <td><span class="badge badge-priority-${prio}">${formatLabel(prio)}</span></td>
      <td><div class="sentiment-bar"><div style="width:${sentWidth}%;background:${sentColor};"></div></div></td>
      <td style="font-size:12px;color:var(--slate-500);">${escapeHtml(resp)}</td>
    </tr>
  `;
}

function renderPagination() {
  const pagInfo  = document.getElementById('pag-info');
  const pagGroup = document.getElementById('pag-group');
  const totalPages = Math.max(1, Math.ceil(totalTickets / PAGE_SIZE));

  const from = (currentPage - 1) * PAGE_SIZE + 1;
  const to   = Math.min(currentPage * PAGE_SIZE, totalTickets);
  pagInfo.textContent = `Showing ${totalTickets ? from : 0}–${to} of ${totalTickets} tickets`;

  const btns = [];
  btns.push(`<button class="pag-btn" data-p="prev" ${currentPage === 1 ? 'disabled' : ''}>Previous</button>`);
  const maxShow = 5;
  let start = Math.max(1, currentPage - 2);
  let end = Math.min(totalPages, start + maxShow - 1);
  if (end - start < maxShow - 1) start = Math.max(1, end - maxShow + 1);
  for (let p = start; p <= end; p++) {
    btns.push(`<button class="pag-btn ${p === currentPage ? 'active' : ''}" data-p="${p}">${p}</button>`);
  }
  btns.push(`<button class="pag-btn" data-p="next" ${currentPage === totalPages ? 'disabled' : ''}>Next</button>`);
  pagGroup.innerHTML = btns.join('');

  pagGroup.querySelectorAll('.pag-btn').forEach((b) => {
    b.addEventListener('click', () => {
      const v = b.dataset.p;
      if (v === 'prev' && currentPage > 1) currentPage--;
      else if (v === 'next' && currentPage < totalPages) currentPage++;
      else if (!isNaN(+v)) currentPage = +v;
      loadTickets();
    });
  });
}

// ─── Filters ──────────────────────────────────────────────
function wireFilters() {
  document.querySelectorAll('#status-filters .filter-pill').forEach((p) => {
    p.addEventListener('click', () => {
      document.querySelectorAll('#status-filters .filter-pill').forEach((x) => x.classList.remove('active'));
      p.classList.add('active');
      currentStatus = p.dataset.status;
      currentPage = 1;
      loadTickets();
    });
  });
  document.querySelectorAll('#channel-filters .filter-pill').forEach((p) => {
    p.addEventListener('click', () => {
      document.querySelectorAll('#channel-filters .filter-pill').forEach((x) => x.classList.remove('active'));
      p.classList.add('active');
      currentChannel = p.dataset.channel;
      currentPage = 1;
      loadTickets();
    });
  });

  const searchEl = document.getElementById('ticket-search');
  let tmr;
  searchEl.addEventListener('input', () => {
    clearTimeout(tmr);
    tmr = setTimeout(() => {
      currentSearch = searchEl.value.trim();
      currentPage = 1;
      loadTickets();
    }, 300);
  });

  document.getElementById('refresh-btn').addEventListener('click', () => {
    countdown = 30;
    fetchAll();
  });
}

// ─── Escalations ──────────────────────────────────────────
async function loadEscalations() {
  const list = document.getElementById('esc-list');
  const count = document.getElementById('esc-count');
  try {
    const r = await fetch(`${API_BASE}/dashboard/tickets?status=escalated&limit=5`, { headers: apiHeaders() });
    if (!r.ok) return;
    const d = await r.json();
    const items = d.tickets || d.items || [];
    count.textContent = `${d.total ?? items.length} open`;

    if (!items.length) {
      list.innerHTML = '<div style="text-align:center;color:var(--slate-500);font-size:13px;padding:16px 0;">No escalations</div>';
      return;
    }

    list.innerHTML = items.map((t) => {
      const team = (t.escalated_to || 'support').toLowerCase().replace('@techcorp.io', '').split('.')[0];
      const ago  = timeAgo(t.escalated_at || t.created_at);
      return `
        <div class="esc-item">
          <div class="esc-icon">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#22D3EE" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
              <line x1="12" y1="9" x2="12" y2="13"></line>
              <line x1="12" y1="17" x2="12.01" y2="17"></line>
            </svg>
          </div>
          <div class="esc-info">
            <div class="subject">${escapeHtml(t.subject || '—')}</div>
            <div class="meta">${escapeHtml(t.ticket_ref || t.ticket_id || '')} · ${ago}</div>
          </div>
          <span class="badge badge-team-${team}">${formatLabel(team)}</span>
        </div>
      `;
    }).join('');
  } catch (_) {
    list.innerHTML = '<div style="text-align:center;color:#FCA5A5;font-size:13px;padding:12px 0;">Failed to load</div>';
  }
}

// ─── Metrics ──────────────────────────────────────────────
async function loadMetrics() {
  try {
    const r = await fetch(`${API_BASE}/metrics/daily`, { headers: apiHeaders() });
    if (!r.ok) return;
    const d = await r.json();

    // API shape: { metrics: { email: {...}, whatsapp: {...}, web_form: {...} } }
    const metrics = d.metrics || {};
    const email    = metrics.email    || {};
    const whatsapp = metrics.whatsapp || {};
    const webform  = metrics.web_form || {};

    const emailCount    = email.total_tickets    || 0;
    const whatsappCount = whatsapp.total_tickets || 0;
    const webformCount  = webform.total_tickets  || 0;
    const total = emailCount + whatsappCount + webformCount;

    updateChannelRow(0, 'email',    emailCount,    total);
    updateChannelRow(1, 'whatsapp', whatsappCount, total);
    updateChannelRow(2, 'web_form', webformCount,  total);

    // Aggregate metrics across channels
    const totalEscalated = (email.escalated_to_human || 0) + (whatsapp.escalated_to_human || 0) + (webform.escalated_to_human || 0);
    const escRate = total > 0 ? ((totalEscalated / total) * 100).toFixed(1) : '0';

    const latencies = [email.avg_latency_ms, whatsapp.avg_latency_ms, webform.avg_latency_ms].filter((v) => v > 0);
    const avgLat = latencies.length ? (latencies.reduce((a, b) => a + b, 0) / latencies.length / 1000).toFixed(2) + 's' : '0s';

    const p95s = [email.p95_latency_ms, whatsapp.p95_latency_ms, webform.p95_latency_ms].filter((v) => v > 0);
    const p95 = p95s.length ? (Math.max(...p95s) / 1000).toFixed(2) + 's' : '0s';

    const totalSla = (email.sla_breaches || 0) + (whatsapp.sla_breaches || 0) + (webform.sla_breaches || 0);

    setText('m-esc-rate', escRate + '%');
    setText('m-avg-lat',  avgLat);
    setText('m-sla',      totalSla);
    setText('m-p95',      p95);
  } catch (_) { /* silent */ }
}

function updateChannelRow(idx, channel, count, total) {
  const rows = document.querySelectorAll('#channel-rows .channel-row');
  const row = rows[idx];
  if (!row) return;
  const pct = total > 0 ? Math.round((count / total) * 100) : 0;
  row.querySelector('.progress-bar > div').style.width = pct + '%';
  row.querySelector('.channel-count').textContent = `${count} tickets`;
}

// ─── KB ───────────────────────────────────────────────────
async function loadKB() {
  const grid = document.getElementById('kb-grid');
  try {
    const r = await fetch(`${API_BASE}/dashboard/kb`, { headers: apiHeaders() });
    if (!r.ok) return;
    const d = await r.json();
    const articles = d.articles || [];
    const total    = d.total || articles.length;
    const embedded = d.embedded || articles.filter((a) => a.has_embedding).length;
    const pct      = total > 0 ? Math.round((embedded / total) * 100) : 0;

    setText('kb-total', `${total} articles`);
    setText('kb-coverage-text', `${embedded}/${total} articles`);
    document.getElementById('kb-coverage-bar').style.width = pct + '%';

    // Categories
    const catSelect = document.getElementById('kb-category');
    const cats = [...new Set(articles.map((a) => a.category).filter(Boolean))];
    catSelect.innerHTML = '<option value="">All categories</option>' +
      cats.map((c) => `<option value="${escapeHtml(c)}">${formatLabel(c)}</option>`).join('');

    renderKBGrid(articles);

    // Search + filter
    document.getElementById('kb-search').oninput = (e) => filterKB(articles, e.target.value, catSelect.value);
    catSelect.onchange = (e) => filterKB(articles, document.getElementById('kb-search').value, e.target.value);
  } catch (_) {
    grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;color:#FCA5A5;padding:12px 0;">Failed to load KB</div>';
  }
}

function filterKB(articles, q, cat) {
  q = (q || '').toLowerCase();
  const filtered = articles.filter((a) => {
    if (cat && a.category !== cat) return false;
    if (q && !(a.title || '').toLowerCase().includes(q)) return false;
    return true;
  });
  renderKBGrid(filtered);
}

function renderKBGrid(articles) {
  const grid = document.getElementById('kb-grid');
  const top = articles.slice(0, 10);
  if (!top.length) {
    grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;color:var(--slate-500);padding:12px 0;">No articles</div>';
    return;
  }
  const maxHits = Math.max(1, ...top.map((a) => a.hits || a.view_count || 0));
  grid.innerHTML = top.map((a) => {
    const hits = a.hits || a.view_count || 0;
    const pct  = Math.round((hits / maxHits) * 100);
    return `
      <div class="kb-article">
        <div class="title">${escapeHtml(a.title || 'Untitled')}</div>
        <div class="mini-bar"><div style="width:${pct}%;"></div></div>
        <span class="hits">${hits} hits</span>
      </div>
    `;
  }).join('');
}

// ─── Countdown ────────────────────────────────────────────
function startCountdown() {
  setInterval(() => {
    countdown--;
    document.getElementById('refresh-timer').textContent = `Auto-refresh in ${countdown}s`;
    if (countdown <= 0) {
      countdown = 30;
      fetchAll();
    }
  }, 1000);
}

// ─── Helpers ──────────────────────────────────────────────
function setText(id, v) { const el = document.getElementById(id); if (el) el.textContent = v; }
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function formatLabel(s) { return String(s || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()); }
function formatChannel(c) {
  if (c === 'web_form') return 'Web Form';
  if (c === 'whatsapp') return 'WhatsApp';
  if (c === 'email')    return 'Email';
  return formatLabel(c);
}
function timeAgo(iso) {
  if (!iso) return '—';
  try {
    const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
    if (diff < 60)    return 'just now';
    if (diff < 3600)  return Math.floor(diff / 60) + ' min ago';
    if (diff < 86400) return Math.floor(diff / 3600) + ' hr ago';
    return Math.floor(diff / 86400) + ' day ago';
  } catch (_) { return '—'; }
}

// ─── Init ─────────────────────────────────────────────────
initAuth();
