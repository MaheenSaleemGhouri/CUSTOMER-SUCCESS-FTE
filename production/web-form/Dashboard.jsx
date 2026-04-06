/**
 * Dashboard.jsx — TechCorp Customer Success Admin Dashboard (Option E)
 *
 * Sections:
 *  • Stats bar     — total/open/escalated/resolved tickets + KB status
 *  • Tickets table — paginated, filterable by status + channel
 *  • KB panel      — article list with embedding status + search stats
 *  • Metrics panel — per-channel analytics from /metrics/daily
 */

import { useState, useEffect, useCallback } from 'react'

// ─── helpers ───────────────────────────────────────────────────────────────

const API = (path) => `/api${path}`

// Read admin key from localStorage (set once, persists across refreshes)
function getAdminKey() {
  return localStorage.getItem('techcorp_admin_key') || ''
}

async function apiFetch(path) {
  const headers = { 'Content-Type': 'application/json' }
  const key = getAdminKey()
  if (key) headers['X-Admin-Key'] = key

  const res = await fetch(API(path), { headers })

  if (res.status === 401) {
    // Prompt for key if not configured
    const entered = prompt('Enter Admin API Key (set ADMIN_API_KEY in .env):')
    if (entered) {
      localStorage.setItem('techcorp_admin_key', entered)
      return apiFetch(path)  // retry
    }
    throw new Error('Authentication required')
  }

  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

function Badge({ label, color = 'gray' }) {
  const colors = {
    blue:   'bg-blue-100 text-blue-700',
    green:  'bg-green-100 text-green-700',
    yellow: 'bg-yellow-100 text-yellow-700',
    red:    'bg-red-100 text-red-700',
    orange: 'bg-orange-100 text-orange-700',
    gray:   'bg-gray-100 text-gray-600',
    purple: 'bg-purple-100 text-purple-700',
  }
  return (
    <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${colors[color] || colors.gray}`}>
      {label}
    </span>
  )
}

function statusBadge(s) {
  const map = { open: 'blue', in_progress: 'yellow', escalated: 'red', resolved: 'green' }
  return <Badge label={s?.replace('_', ' ').toUpperCase()} color={map[s] || 'gray'} />
}

function priorityBadge(p) {
  const map = { critical: 'red', high: 'orange', medium: 'yellow', low: 'gray' }
  return <Badge label={p?.toUpperCase()} color={map[p] || 'gray'} />
}

function channelBadge(ch) {
  const map = { email: 'purple', whatsapp: 'green', web_form: 'blue' }
  return <Badge label={ch?.replace('_', ' ')} color={map[ch] || 'gray'} />
}

function sentimentBar(score) {
  if (score == null) return <span className="text-gray-300 text-xs">—</span>
  const pct = Math.round(score * 100)
  const color = score < 0.3 ? 'bg-red-400' : score < 0.6 ? 'bg-yellow-400' : 'bg-green-400'
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-16 h-1.5 bg-gray-200 rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-gray-500">{pct}%</span>
    </div>
  )
}

function timeAgo(iso) {
  if (!iso) return '—'
  const diff = Date.now() - new Date(iso).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

// ─── StatsBar ──────────────────────────────────────────────────────────────

function StatsBar({ stats, loading }) {
  const cards = stats ? [
    { label: 'Total Tickets',   value: stats.tickets.total,       color: 'text-gray-800' },
    { label: 'Open',            value: stats.tickets.open,        color: 'text-blue-600' },
    { label: 'In Progress',     value: stats.tickets.in_progress, color: 'text-yellow-600' },
    { label: 'Escalated',       value: stats.tickets.escalated,   color: 'text-red-600' },
    { label: 'Resolved',        value: stats.tickets.resolved,    color: 'text-green-600' },
    { label: 'AI Resolved',     value: stats.tickets.ai_resolved, color: 'text-purple-600' },
    {
      label: 'KB Articles',
      value: `${stats.kb.with_embeddings}/${stats.kb.total}`,
      color: stats.kb.with_embeddings === stats.kb.total ? 'text-green-600' : 'text-orange-500',
      sub:   'with embeddings',
    },
    {
      label: 'Avg Sentiment',
      value: `${Math.round((stats.avg_sentiment || 0.5) * 100)}%`,
      color: stats.avg_sentiment < 0.3 ? 'text-red-600' : stats.avg_sentiment < 0.6 ? 'text-yellow-600' : 'text-green-600',
    },
  ] : []

  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-3 mb-6">
      {loading
        ? Array(8).fill(0).map((_, i) => (
            <div key={i} className="bg-white rounded-lg p-3 animate-pulse">
              <div className="h-3 bg-gray-200 rounded w-3/4 mb-2" />
              <div className="h-6 bg-gray-200 rounded w-1/2" />
            </div>
          ))
        : cards.map((c) => (
            <div key={c.label} className="bg-white rounded-lg shadow-sm p-3 border border-gray-100">
              <p className="text-xs text-gray-400 mb-1">{c.label}</p>
              <p className={`text-xl font-bold ${c.color}`}>{c.value}</p>
              {c.sub && <p className="text-xs text-gray-400">{c.sub}</p>}
            </div>
          ))
      }
    </div>
  )
}

// ─── TicketsTable ──────────────────────────────────────────────────────────

function TicketsTable() {
  const [tickets, setTickets]         = useState([])
  const [total,   setTotal]           = useState(0)
  const [loading, setLoading]         = useState(true)
  const [error,   setError]           = useState(null)
  const [statusF, setStatusF]         = useState('all')
  const [channelF, setChannelF]       = useState('all')
  const [page, setPage]               = useState(0)
  const LIMIT = 15

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const qs = new URLSearchParams()
      if (statusF  !== 'all') qs.set('status',  statusF)
      if (channelF !== 'all') qs.set('channel', channelF)
      qs.set('limit',  LIMIT)
      qs.set('offset', page * LIMIT)
      const data = await apiFetch(`/dashboard/tickets?${qs}`)
      setTickets(data.tickets)
      setTotal(data.total)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [statusF, channelF, page])

  useEffect(() => { load() }, [load])

  const pages = Math.ceil(total / LIMIT)

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-100 mb-6">
      {/* Header + filters */}
      <div className="flex flex-wrap items-center justify-between gap-3 p-4 border-b border-gray-100">
        <div>
          <h2 className="font-semibold text-gray-800">Support Tickets</h2>
          <p className="text-xs text-gray-400">{total} total</p>
        </div>
        <div className="flex gap-2">
          <select
            value={statusF}
            onChange={e => { setStatusF(e.target.value); setPage(0) }}
            className="text-sm border border-gray-200 rounded px-2 py-1 text-gray-600"
          >
            <option value="all">All Statuses</option>
            <option value="open">Open</option>
            <option value="in_progress">In Progress</option>
            <option value="escalated">Escalated</option>
            <option value="resolved">Resolved</option>
          </select>
          <select
            value={channelF}
            onChange={e => { setChannelF(e.target.value); setPage(0) }}
            className="text-sm border border-gray-200 rounded px-2 py-1 text-gray-600"
          >
            <option value="all">All Channels</option>
            <option value="web_form">Web Form</option>
            <option value="email">Email</option>
            <option value="whatsapp">WhatsApp</option>
          </select>
          <button onClick={load} className="text-sm text-blue-600 hover:text-blue-800 px-2 py-1 border border-blue-200 rounded">
            ↻ Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="m-4 bg-red-50 border border-red-200 rounded p-3 text-sm text-red-600">{error}</div>
      )}

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-gray-400 border-b border-gray-100 bg-gray-50">
              <th className="text-left px-4 py-2 font-medium">Ref</th>
              <th className="text-left px-4 py-2 font-medium">Customer</th>
              <th className="text-left px-4 py-2 font-medium">Summary</th>
              <th className="text-left px-4 py-2 font-medium">Channel</th>
              <th className="text-left px-4 py-2 font-medium">Priority</th>
              <th className="text-left px-4 py-2 font-medium">Status</th>
              <th className="text-left px-4 py-2 font-medium">Sentiment</th>
              <th className="text-left px-4 py-2 font-medium">Opened</th>
              <th className="text-left px-4 py-2 font-medium">Response</th>
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array(8).fill(0).map((_, i) => (
                  <tr key={i} className="border-b border-gray-50">
                    {Array(9).fill(0).map((__, j) => (
                      <td key={j} className="px-4 py-3">
                        <div className="h-3 bg-gray-100 rounded animate-pulse" />
                      </td>
                    ))}
                  </tr>
                ))
              : tickets.length === 0
                ? (
                  <tr><td colSpan={9} className="text-center py-10 text-gray-400 text-sm">No tickets found</td></tr>
                )
                : tickets.map(t => (
                    <tr key={t.ticket_id} className="border-b border-gray-50 hover:bg-gray-50 transition-colors">
                      <td className="px-4 py-3">
                        <span className="font-mono text-xs text-blue-600">{t.ticket_ref}</span>
                        {t.escalated && <span className="ml-1 text-red-400 text-xs">⚠</span>}
                      </td>
                      <td className="px-4 py-3">
                        <div className="font-medium text-gray-700 truncate max-w-[120px]">{t.customer_name}</div>
                        <div className="text-xs text-gray-400 capitalize">{t.customer_plan}</div>
                      </td>
                      <td className="px-4 py-3">
                        <div className="text-gray-600 truncate max-w-[200px]" title={t.summary}>{t.summary}</div>
                        <div className="text-xs text-gray-400 capitalize">{t.category?.replace('_', ' ')}</div>
                      </td>
                      <td className="px-4 py-3">{channelBadge(t.channel)}</td>
                      <td className="px-4 py-3">{priorityBadge(t.priority)}</td>
                      <td className="px-4 py-3">{statusBadge(t.status)}</td>
                      <td className="px-4 py-3">{sentimentBar(t.sentiment)}</td>
                      <td className="px-4 py-3 text-xs text-gray-400 whitespace-nowrap">{timeAgo(t.opened_at)}</td>
                      <td className="px-4 py-3 text-xs text-gray-400 whitespace-nowrap">
                        {t.first_response_ms ? `${(t.first_response_ms / 1000).toFixed(1)}s` : '—'}
                      </td>
                    </tr>
                  ))
            }
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {pages > 1 && (
        <div className="flex items-center justify-between px-4 py-3 border-t border-gray-100 text-sm text-gray-500">
          <span>Page {page + 1} of {pages}</span>
          <div className="flex gap-2">
            <button
              disabled={page === 0}
              onClick={() => setPage(p => p - 1)}
              className="px-3 py-1 border border-gray-200 rounded disabled:opacity-40 hover:bg-gray-50"
            >← Prev</button>
            <button
              disabled={page >= pages - 1}
              onClick={() => setPage(p => p + 1)}
              className="px-3 py-1 border border-gray-200 rounded disabled:opacity-40 hover:bg-gray-50"
            >Next →</button>
          </div>
        </div>
      )}
    </div>
  )
}

// ─── KBPanel ───────────────────────────────────────────────────────────────

function KBPanel() {
  const [articles, setArticles] = useState([])
  const [loading,  setLoading]  = useState(true)
  const [error,    setError]    = useState(null)
  const [catFilter, setCatFilter] = useState('all')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const qs  = catFilter !== 'all' ? `?category=${catFilter}` : ''
      const data = await apiFetch(`/dashboard/kb${qs}`)
      setArticles(data.articles)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [catFilter])

  useEffect(() => { load() }, [load])

  const categories = [...new Set(articles.map(a => a.category))].sort()
  const allCats    = catFilter === 'all' ? [...new Set(articles.map(a => a.category))].sort() : [catFilter]

  const withEmb    = articles.filter(a => a.has_embedding).length
  const pct        = articles.length ? Math.round((withEmb / articles.length) * 100) : 0

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-100 mb-6">
      <div className="flex flex-wrap items-center justify-between gap-3 p-4 border-b border-gray-100">
        <div>
          <h2 className="font-semibold text-gray-800">Knowledge Base</h2>
          <p className="text-xs text-gray-400">
            {articles.length} articles · {withEmb} with embeddings ({pct}%)
          </p>
        </div>
        <div className="flex gap-2">
          <select
            value={catFilter}
            onChange={e => setCatFilter(e.target.value)}
            className="text-sm border border-gray-200 rounded px-2 py-1 text-gray-600"
          >
            <option value="all">All Categories</option>
            {categories.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
          <button onClick={load} className="text-sm text-blue-600 hover:text-blue-800 px-2 py-1 border border-blue-200 rounded">
            ↻ Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="m-4 bg-red-50 border border-red-200 rounded p-3 text-sm text-red-600">{error}</div>
      )}

      {/* Embedding progress bar */}
      {articles.length > 0 && (
        <div className="px-4 py-3 border-b border-gray-50">
          <div className="flex items-center gap-3">
            <span className="text-xs text-gray-500">Vector Search Coverage</span>
            <div className="flex-1 h-2 bg-gray-100 rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full ${pct === 100 ? 'bg-green-500' : pct > 50 ? 'bg-blue-500' : 'bg-orange-400'}`}
                style={{ width: `${pct}%` }}
              />
            </div>
            <span className={`text-xs font-medium ${pct === 100 ? 'text-green-600' : 'text-orange-500'}`}>
              {pct}%
            </span>
            {pct < 100 && (
              <span className="text-xs text-gray-400">Run kb_seed.py to generate embeddings</span>
            )}
          </div>
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-gray-400 border-b border-gray-100 bg-gray-50">
              <th className="text-left px-4 py-2 font-medium">Ref</th>
              <th className="text-left px-4 py-2 font-medium">Category</th>
              <th className="text-left px-4 py-2 font-medium">Title</th>
              <th className="text-left px-4 py-2 font-medium">Words</th>
              <th className="text-left px-4 py-2 font-medium">Embedding</th>
              <th className="text-left px-4 py-2 font-medium">Searches</th>
              <th className="text-left px-4 py-2 font-medium">Used</th>
              <th className="text-left px-4 py-2 font-medium">Updated</th>
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array(6).fill(0).map((_, i) => (
                  <tr key={i} className="border-b border-gray-50">
                    {Array(8).fill(0).map((__, j) => (
                      <td key={j} className="px-4 py-3">
                        <div className="h-3 bg-gray-100 rounded animate-pulse" />
                      </td>
                    ))}
                  </tr>
                ))
              : articles.length === 0
                ? <tr><td colSpan={8} className="text-center py-8 text-gray-400 text-sm">No KB articles found. Run kb_seed.py to populate.</td></tr>
                : articles.map(a => (
                    <tr key={a.id} className="border-b border-gray-50 hover:bg-gray-50 transition-colors">
                      <td className="px-4 py-3 font-mono text-xs text-blue-600">{a.kb_ref}</td>
                      <td className="px-4 py-3">
                        <Badge label={a.category} color={
                          a.category === 'technical' ? 'purple' :
                          a.category === 'billing'   ? 'orange' :
                          a.category === 'integration' ? 'blue' :
                          a.category === 'escalation' ? 'red' : 'gray'
                        } />
                      </td>
                      <td className="px-4 py-3 text-gray-700 max-w-[260px] truncate" title={a.title}>{a.title}</td>
                      <td className="px-4 py-3 text-gray-500">{a.word_count}</td>
                      <td className="px-4 py-3">
                        {a.has_embedding
                          ? <span className="text-green-600 text-xs font-medium">✓ Ready</span>
                          : <span className="text-orange-500 text-xs">⚠ Missing</span>
                        }
                      </td>
                      <td className="px-4 py-3 text-gray-500">{a.search_hits}</td>
                      <td className="px-4 py-3 text-gray-500">{a.search_used}</td>
                      <td className="px-4 py-3 text-xs text-gray-400">{timeAgo(a.updated_at)}</td>
                    </tr>
                  ))
            }
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ─── MetricsPanel ─────────────────────────────────────────────────────────

function MetricsPanel() {
  const [metrics, setMetrics] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiFetch('/metrics/daily')
      setMetrics(data.metrics)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const channelColors = { email: 'purple', whatsapp: 'green', web_form: 'blue' }

  const channels = metrics ? Object.keys(metrics) : []

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-100 mb-6">
      <div className="flex items-center justify-between p-4 border-b border-gray-100">
        <div>
          <h2 className="font-semibold text-gray-800">Channel Metrics (24h)</h2>
          <p className="text-xs text-gray-400">Today's performance by channel</p>
        </div>
        <button onClick={load} className="text-sm text-blue-600 hover:text-blue-800 px-2 py-1 border border-blue-200 rounded">
          ↻ Refresh
        </button>
      </div>

      {error && (
        <div className="m-4 bg-red-50 border border-red-200 rounded p-3 text-sm text-red-600">{error}</div>
      )}

      {loading ? (
        <div className="p-6 grid grid-cols-1 md:grid-cols-3 gap-4">
          {Array(3).fill(0).map((_, i) => (
            <div key={i} className="border border-gray-100 rounded-lg p-4 animate-pulse">
              <div className="h-4 bg-gray-200 rounded w-2/3 mb-4" />
              {Array(5).fill(0).map((__, j) => (
                <div key={j} className="h-3 bg-gray-100 rounded w-full mb-2" />
              ))}
            </div>
          ))}
        </div>
      ) : channels.length === 0 ? (
        <div className="p-8 text-center text-gray-400 text-sm">
          No metrics yet for today. Process some tickets to see data here.
        </div>
      ) : (
        <div className="p-4 grid grid-cols-1 md:grid-cols-3 gap-4">
          {channels.map(ch => {
            const m = metrics[ch]
            const color = channelColors[ch] || 'gray'
            const escalationRate = m.total_tickets > 0
              ? Math.round((m.escalated_to_human / m.total_tickets) * 100)
              : 0
            const aiRate = m.total_tickets > 0
              ? Math.round((m.resolved_by_ai / m.total_tickets) * 100)
              : 0
            return (
              <div key={ch} className="border border-gray-100 rounded-lg p-4">
                <div className="flex items-center gap-2 mb-4">
                  {channelBadge(ch)}
                  <span className="text-sm font-medium text-gray-700 capitalize">
                    {ch.replace('_', ' ')}
                  </span>
                </div>
                <div className="space-y-2 text-sm">
                  <div className="flex justify-between">
                    <span className="text-gray-500">Total Tickets</span>
                    <span className="font-medium text-gray-800">{m.total_tickets}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">AI Resolved</span>
                    <span className={`font-medium ${aiRate > 50 ? 'text-green-600' : 'text-yellow-600'}`}>
                      {m.resolved_by_ai} ({aiRate}%)
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">Escalated</span>
                    <span className={`font-medium ${escalationRate > 30 ? 'text-red-600' : 'text-orange-500'}`}>
                      {m.escalated_to_human} ({escalationRate}%)
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">Avg Latency</span>
                    <span className="font-medium text-gray-700">
                      {m.avg_latency_ms > 0 ? `${(m.avg_latency_ms / 1000).toFixed(1)}s` : '—'}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">P95 Latency</span>
                    <span className="font-medium text-gray-700">
                      {m.p95_latency_ms > 0 ? `${(m.p95_latency_ms / 1000).toFixed(1)}s` : '—'}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">SLA Breaches</span>
                    <span className={`font-medium ${m.sla_breaches > 0 ? 'text-red-600' : 'text-green-600'}`}>
                      {m.sla_breaches}
                    </span>
                  </div>

                  {/* AI Resolution bar */}
                  <div className="pt-2">
                    <div className="flex justify-between text-xs text-gray-400 mb-1">
                      <span>AI Resolution Rate</span>
                      <span>{aiRate}%</span>
                    </div>
                    <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
                      <div
                        className="h-full bg-blue-500 rounded-full"
                        style={{ width: `${aiRate}%` }}
                      />
                    </div>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ─── EscalationInbox ──────────────────────────────────────────────────────

function EscalationInbox() {
  const [tickets, setTickets] = useState([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiFetch('/dashboard/tickets?status=escalated&limit=10')
      setTickets(data.tickets)
    } catch {
      setTickets([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const teamColor = (reason) => {
    const map = {
      pricing_inquiry: 'Sales → sales@techcorp.io',
      refund_request:  'Billing → billing@techcorp.io',
      legal_escalation:'Legal → legal@techcorp.io',
      angry_customer:  'CSM → csm@techcorp.io',
      human_requested: 'CSM → csm@techcorp.io',
      technical_tier2: 'Engineering → bugs@techcorp.io',
    }
    return map[reason] || 'Support → support@techcorp.io'
  }

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-100 mb-6">
      <div className="flex items-center justify-between p-4 border-b border-gray-100">
        <div>
          <h2 className="font-semibold text-gray-800">Escalation Inbox</h2>
          <p className="text-xs text-gray-400">Tickets requiring human attention</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="bg-red-100 text-red-700 text-xs font-medium px-2 py-0.5 rounded-full">
            {tickets.length} pending
          </span>
          <button onClick={load} className="text-sm text-blue-600 hover:text-blue-800 px-2 py-1 border border-blue-200 rounded">
            ↻
          </button>
        </div>
      </div>

      {loading ? (
        <div className="p-4 space-y-3">
          {Array(3).fill(0).map((_, i) => (
            <div key={i} className="border border-gray-100 rounded-lg p-3 animate-pulse">
              <div className="h-3 bg-gray-200 rounded w-1/3 mb-2" />
              <div className="h-3 bg-gray-100 rounded w-2/3" />
            </div>
          ))}
        </div>
      ) : tickets.length === 0 ? (
        <div className="p-8 text-center text-gray-400 text-sm">
          No escalated tickets. All clear! ✓
        </div>
      ) : (
        <div className="divide-y divide-gray-50">
          {tickets.map(t => (
            <div key={t.ticket_id} className="p-4 hover:bg-gray-50 transition-colors">
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-mono text-xs text-blue-600">{t.ticket_ref}</span>
                    {channelBadge(t.channel)}
                    {priorityBadge(t.priority)}
                  </div>
                  <p className="text-sm text-gray-700 truncate">{t.summary}</p>
                  <div className="flex items-center gap-3 mt-1">
                    <span className="text-xs text-gray-400">{t.customer_name}</span>
                    <span className="text-xs text-gray-300">·</span>
                    <span className="text-xs text-gray-400">{timeAgo(t.opened_at)}</span>
                    {t.escalation_reason && (
                      <>
                        <span className="text-xs text-gray-300">·</span>
                        <span className="text-xs text-orange-500">{t.escalation_reason?.replace('_', ' ')}</span>
                      </>
                    )}
                  </div>
                </div>
                <div className="text-right flex-shrink-0">
                  <p className="text-xs text-gray-500 mb-1">Routed to</p>
                  <p className="text-xs font-medium text-gray-700 whitespace-nowrap">
                    {teamColor(t.escalation_reason).split(' → ')[0]}
                  </p>
                  <p className="text-xs text-blue-500">
                    {teamColor(t.escalation_reason).split(' → ')[1]}
                  </p>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Dashboard (main export) ──────────────────────────────────────────────

export default function Dashboard({ onBackToForm }) {
  const [stats,   setStats]   = useState(null)
  const [loading, setLoading] = useState(true)

  const loadStats = useCallback(async () => {
    try {
      const data = await apiFetch('/dashboard/stats')
      setStats(data)
    } catch {
      setStats(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadStats() }, [loadStats])

  // Auto-refresh stats every 30s
  useEffect(() => {
    const t = setInterval(loadStats, 30000)
    return () => clearInterval(t)
  }, [loadStats])

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Top nav */}
      <div className="bg-white border-b border-gray-200 sticky top-0 z-10">
        <div className="max-w-screen-xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-xl">🏢</span>
            <div>
              <h1 className="text-base font-bold text-gray-900">TechCorp Support Dashboard</h1>
              <p className="text-xs text-gray-400">Customer Success FTE — Admin View</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs text-gray-400">
              Auto-refreshes every 30s
            </span>
            <button
              onClick={onBackToForm}
              className="text-sm text-blue-600 border border-blue-200 rounded px-3 py-1.5 hover:bg-blue-50"
            >
              ← Support Form
            </button>
          </div>
        </div>
      </div>

      {/* Main content */}
      <div className="max-w-screen-xl mx-auto px-4 py-6">
        <StatsBar stats={stats} loading={loading} />

        <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
          <div className="xl:col-span-2">
            <TicketsTable />
            <MetricsPanel />
          </div>
          <div>
            <EscalationInbox />
            <KBPanel />
          </div>
        </div>
      </div>
    </div>
  )
}
