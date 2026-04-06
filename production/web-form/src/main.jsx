import { StrictMode, useState, useEffect, useCallback, useRef } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import SupportForm from '../SupportForm.jsx'
import Dashboard from '../Dashboard.jsx'

// ─── Agent Response Viewer ─────────────────────────────────────

function AgentResponseViewer({ ticketId, onBack }) {
  const [ticket,    setTicket]    = useState(null)
  const [loading,   setLoading]   = useState(true)
  const [error,     setError]     = useState(null)
  const [wsStatus,  setWsStatus]  = useState('connecting') // connecting | live | closed | error
  const wsRef = useRef(null)

  useEffect(() => {
    // WebSocket URL — relative path goes through Vite proxy → backend
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const url   = `${proto}://${window.location.host}/ws/ticket/${ticketId}`

    const ws = new WebSocket(url)
    wsRef.current = ws
    setWsStatus('connecting')

    ws.onopen = () => {
      setWsStatus('live')
      setLoading(false)
    }

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        if (msg.type === 'ping') return
        if (msg.type === 'init' || msg.type === 'update') {
          setTicket(msg.ticket)
          setLoading(false)
        }
      } catch { /* ignore */ }
    }

    ws.onerror = () => {
      setWsStatus('error')
      setError('WebSocket connection failed — falling back to polling')
      // Fallback: single HTTP fetch
      fetch(`/api/support/ticket/${ticketId}`)
        .then(r => r.json())
        .then(d => { setTicket(d); setLoading(false) })
        .catch(e => setError(e.message))
    }

    ws.onclose = () => {
      setWsStatus('closed')
    }

    return () => {
      ws.close()
    }
  }, [ticketId])

  const statusColor = {
    open:        'bg-blue-100 text-blue-700',
    in_progress: 'bg-yellow-100 text-yellow-700',
    resolved:    'bg-green-100 text-green-700',
    escalated:   'bg-red-100 text-red-700',
  }

  return (
    <div className="bg-white rounded-lg shadow-md p-6 max-w-2xl mx-auto">

      {/* Header */}
      <div className="flex items-center justify-between mb-6 pb-4 border-b border-gray-100">
        <div>
          <h2 className="text-lg font-bold text-gray-900">Ticket Status</h2>
          <p className="text-sm font-mono text-gray-500 mt-0.5">{ticketId}</p>
        </div>
        <button
          onClick={onBack}
          className="text-sm text-gray-500 hover:text-gray-700 underline"
        >
          Submit Another
        </button>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700 mb-4">
          Error loading ticket: {error}
        </div>
      )}

      {/* Ticket Info */}
      {ticket && (
        <div className="mb-6 grid grid-cols-2 gap-3 text-sm">
          <div className="bg-gray-50 rounded-lg p-3">
            <p className="text-xs text-gray-400 mb-1">Status</p>
            <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${statusColor[ticket.status] || 'bg-gray-100 text-gray-700'}`}>
              {ticket.status?.replace('_', ' ').toUpperCase()}
            </span>
          </div>
          <div className="bg-gray-50 rounded-lg p-3">
            <p className="text-xs text-gray-400 mb-1">Priority</p>
            <p className="font-medium text-gray-700 capitalize">{ticket.priority}</p>
          </div>
          <div className="bg-gray-50 rounded-lg p-3 col-span-2">
            <p className="text-xs text-gray-400 mb-1">Subject</p>
            <p className="font-medium text-gray-700">{ticket.subject || ticket.issue_summary}</p>
          </div>
        </div>
      )}

      {/* Agent Response */}
      <div>
        <div className="flex items-center gap-2 mb-3">
          <span className="text-sm font-semibold text-gray-700">Alex (AI Support)</span>
          <span className="text-xs text-gray-400">TechCorp Support</span>
        </div>

        {/* WebSocket live badge */}
        <div className="flex items-center gap-2 mb-3">
          {wsStatus === 'live' && (
            <span className="flex items-center gap-1 text-xs text-green-600">
              <span className="h-2 w-2 rounded-full bg-green-500 animate-pulse inline-block" />
              Live
            </span>
          )}
          {wsStatus === 'connecting' && (
            <span className="text-xs text-gray-400">Connecting…</span>
          )}
          {(wsStatus === 'closed' || wsStatus === 'error') && (
            <span className="text-xs text-gray-400">Disconnected</span>
          )}
        </div>

        {/* Waiting for agent */}
        {(!ticket?.messages || ticket.messages.length === 0) && !error && (
          <div className="bg-gray-50 border border-gray-200 rounded-lg p-5 text-center">
            <div className="flex items-center justify-center gap-2 mb-2">
              <svg className="animate-spin h-4 w-4 text-blue-500" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
              </svg>
              <p className="text-sm text-gray-500">Alex is reviewing your request…</p>
            </div>
            <p className="text-xs text-gray-400">Real-time updates via WebSocket</p>
          </div>
        )}

        {/* Agent response messages */}
        {ticket?.messages?.map((msg, i) => (
          <div key={i} className="bg-blue-50 border border-blue-100 rounded-lg p-4 mb-3">
            <div className="flex items-center gap-2 mb-2">
              <div className="h-7 w-7 rounded-full bg-blue-600 flex items-center justify-center text-white text-xs font-bold">
                A
              </div>
              <span className="text-xs font-medium text-blue-700">Alex — AI Support</span>
              {msg.sent_at && (
                <span className="text-xs text-gray-400 ml-auto">
                  {new Date(msg.sent_at).toLocaleTimeString()}
                </span>
              )}
            </div>
            <div className="text-sm text-gray-800 whitespace-pre-wrap leading-relaxed">
              {msg.content}
            </div>
          </div>
        ))}

        {/* Escalated notice */}
        {ticket?.status === 'escalated' && (
          <div className="mt-3 bg-orange-50 border border-orange-200 rounded-lg p-3 text-sm text-orange-700">
            Your request has been escalated to our specialist team. You'll receive a follow-up shortly.
          </div>
        )}
      </div>
    </div>
  )
}

// ─── App ──────────────────────────────────────────────────────

function App() {
  const [submittedTicketId, setSubmittedTicketId] = useState(null)
  const [showDashboard,     setShowDashboard]     = useState(
    window.location.hash === '#dashboard'
  )

  const handleSuccess = useCallback((ticketId) => {
    setSubmittedTicketId(ticketId)
  }, [])

  const openDashboard = () => {
    window.location.hash = '#dashboard'
    setShowDashboard(true)
  }
  const closeDashboard = () => {
    window.location.hash = ''
    setShowDashboard(false)
    setSubmittedTicketId(null)
  }

  if (showDashboard) {
    return <Dashboard onBackToForm={closeDashboard} />
  }

  return (
    <div className="min-h-screen bg-gray-100 py-10 px-4">

      {/* Header */}
      <div className="max-w-2xl mx-auto mb-8 text-center">
        <div className="flex items-center justify-center gap-3 mb-2">
          <span className="text-3xl">🏢</span>
          <h1 className="text-2xl font-bold text-gray-900">TechCorp</h1>
        </div>
        <p className="text-sm text-gray-500">Customer Support Portal — 24/7 AI-Powered</p>
        <button
          onClick={openDashboard}
          className="mt-2 text-xs text-blue-500 hover:text-blue-700 underline"
        >
          Admin Dashboard →
        </button>
      </div>

      {/* Show form OR agent response viewer */}
      {submittedTicketId ? (
        <AgentResponseViewer
          ticketId={submittedTicketId}
          onBack={() => setSubmittedTicketId(null)}
        />
      ) : (
        <SupportForm
          apiEndpoint="/api/support/submit"
          onSuccess={handleSuccess}
        />
      )}

      {/* Footer */}
      <div className="max-w-2xl mx-auto mt-6 text-center">
        <p className="text-xs text-gray-400">
          Powered by TechCorp AI Support ·{' '}
          <a href="https://techcorp.io/support" className="underline hover:text-gray-600">
            techcorp.io/support
          </a>
        </p>
      </div>
    </div>
  )
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>
)
