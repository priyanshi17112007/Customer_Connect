import { useCallback, useEffect, useRef, useState } from 'react'

import Header from './components/Header'
import SystemStatus from './components/SystemStatus'
import SecurityStatus from './components/SecurityStatus'
import CustomerContext from './components/CustomerContext'
import AgentCore from './components/AgentCore'
import AgentActivity from './components/AgentActivity'
import ChatWindow from './components/ChatWindow'
import ChatInput from './components/ChatInput'
import QuickActions from './components/QuickActions'
import ErrorBanner from './components/ErrorBanner'
import EscalationCard from './components/EscalationCard'
import { healthCheck, sendChat } from './services/api'
import { failedItem, liveItem, mapActivity } from './services/activity'
import './styles/shell.css'
import './styles/app.css'

let messageSeq = 0
const nextId = () => `m${++messageSeq}`

const nowLabel = () =>
  new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

/** Turn a machine token into readable text. */
function humanise(code) {
  if (!code) return null
  return String(code)
    .toLowerCase()
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

export default function App() {
  // ------------------------------------------------------- application state
  const [customerId, setCustomerId] = useState('CUS-001')
  // The API key is never copied into React state: it stays in the password
  // field and is read from this ref only at submit time. `hasApiKey` carries
  // only presence, never the value.
  const apiKeyRef = useRef(null)
  const [hasApiKey, setHasApiKey] = useState(false)
  const [conversationId, setConversationId] = useState(null)
  const [messages, setMessages] = useState([])
  const [draft, setDraft] = useState('')
  const [loading, setLoading] = useState(false)
  const [online, setOnline] = useState(false)
  const [checking, setChecking] = useState(true)
  const [health, setHealth] = useState(null)
  const [coreState, setCoreState] = useState('idle')
  const [error, setError] = useState(null)
  const [escalation, setEscalation] = useState(null)
  const [contextOpen, setContextOpen] = useState(true)
  // Timeline for the current request only. Replaced on every send, never
  // merged, so one request's activity can never bleed into the next.
  const [timeline, setTimeline] = useState([])

  const endRef = useRef(null)
  const timerRef = useRef(null)
  // The phase timer must observe whether a request is still in flight;
  // `loading` in a closure would capture the stale pre-request value.
  const inFlightRef = useRef(false)

  // Keep the newest message in view.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, loading])

  // Clear the transient core state a moment after a terminal outcome.
  useEffect(() => {
    if (coreState !== 'complete' && coreState !== 'error') return undefined
    timerRef.current = setTimeout(() => setCoreState('idle'), 2600)
    return () => clearTimeout(timerRef.current)
  }, [coreState])

  // --------------------------------------------------------------- health
  const checkHealth = useCallback(async () => {
    setChecking(true)
    try {
      const result = await healthCheck()
      setHealth(result)
      setOnline(true)
      // Clear only a stale connectivity error. A chat error (e.g. a rejected
      // credential) must survive a health re-check, otherwise clicking
      // "retry connection" erases the only explanation on screen.
      setError((prev) => (prev && prev.code !== 'NETWORK_ERROR' ? prev : null))
    } catch (err) {
      setOnline(false)
      setHealth(null)
      setError({
        message: err.message || 'The ResolveAI backend is not responding.',
        code: err.code ?? 'NETWORK_ERROR',
        requestId: err.requestId ?? null,
      })
    } finally {
      setChecking(false)
    }
  }, [])

  // One check on mount. No polling - retry is manual by design.
  useEffect(() => {
    checkHealth()
  }, [checkHealth])

  // ------------------------------------------------------------- sending
  const canSend = Boolean(customerId.trim() && hasApiKey && draft.trim())

  async function handleSend() {
    const message = draft.trim()
    const apiKey = apiKeyRef.current?.value?.trim() ?? ''
    if (!message || !apiKey || loading || !canSend) return

    // Optimistic user bubble.
    setMessages((prev) => [
      ...prev,
      { id: nextId(), role: 'user', text: message, time: nowLabel() },
    ])
    setDraft('')
    setError(null)
    setEscalation(null)
    setTimeline([])
    setLoading(true)
    inFlightRef.current = true
    setCoreState('analyzing')

    // Phase the UI: "analyzing" reads better if the request takes a moment.
    const phaseTimer = setTimeout(() => {
      if (inFlightRef.current) setCoreState('resolving')
    }, 1200)

    try {
      const result = await sendChat({
        customerId: customerId.trim(),
        message,
        conversationId,
        apiKey,
      })

      if (result.conversationId) setConversationId(result.conversationId)

      // Only what the backend actually reported this turn.
      const stages = mapActivity(result.activity, result)
      setTimeline(stages)

      const fields = []
      if (result.intent) fields.push(['Intent', humanise(result.intent)])
      if (result.decision) fields.push(['Decision', humanise(result.decision)])
      if (result.conversationId) fields.push(['Conversation', result.conversationId])
      if (result.requestId) fields.push(['Request ID', result.requestId])

      let badge = null
      let tone = 'ok'
      if (result.errorCode) {
        badge = humanise(result.errorCode)
        tone = 'warn'
      } else if (result.escalated) {
        badge = 'Escalated'
        tone = 'warn'
      } else if (result.resolved) {
        badge = 'Verified Response'
        tone = 'ok'
      }

      const note = result.errorCode
        ? 'The backend could not release this data. Nothing was changed.'
        : null

      setMessages((prev) => [
        ...prev,
        {
          id: nextId(),
          role: 'agent',
          text: result.response || 'The backend returned an empty response.',
          time: nowLabel(),
          activity: stages,
          meta: {
            badge,
            tone,
            fields,
            note,
          },
        },
      ])

      if (result.escalated) {
        setEscalation({
          ticketId: result.ticketId,
          conversationId: result.conversationId ?? conversationId,
        })
      }

      setCoreState(result.errorCode ? 'error' : 'complete')
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          id: nextId(),
          role: 'agent',
          text: err.message || 'The request could not be completed.',
          time: nowLabel(),
          meta: {
            badge: humanise(err.code) ?? 'Request Failed',
            tone: 'error',
            fields: err.requestId ? [['Request ID', err.requestId]] : [],
          },
        },
      ])
      setError({
        message: err.message,
        code: err.code,
        requestId: err.requestId ?? null,
      })
      // The request never produced activity - show one honest failure node.
      setTimeline(failedItem())
      setCoreState('error')

      // Only a genuine connectivity/gateway failure means the backend is
      // unreachable. 401/403 mean the service answered and rejected our
      // credential, so reporting "offline" would be false.
      if ([502, 503, 504].includes(err.status)) {
        setOnline(false)
      }
    } finally {
      clearTimeout(phaseTimer)
      inFlightRef.current = false
      setLoading(false)
    }
  }

  // --------------------------------------------------------- identity change
  // Switching customer must not leave the previous customer's session on
  // screen. Authorisation here is a shared API key plus a caller-supplied
  // customer_id, so a stale transcript, conversation id, escalation or activity
  // timeline would show one customer another's data - and the next send would
  // replay the previous conversation_id against the new identity, which the
  // backend rejects as CONVERSATION_NOT_ACCESSIBLE. Everything below is scoped
  // to a single customer, so all of it is dropped together.
  function handleCustomerIdChange(next) {
    if (next === customerId) return
    setCustomerId(next)
    setMessages([])
    setConversationId(null)
    setEscalation(null)
    setTimeline([])
    setError(null)
    setCoreState('idle')
    // A draft written for the previous customer is not valid for this one.
    setDraft('')
  }

  function handleQuickAction(text) {
    setDraft(text)
    setError(null)
  }

  return (
    <div className="app">
      <Header online={online} health={health} />

      <main className="app__main">
        {/* ------------------------------------------------ left sidebar */}
        <aside
          className={`app__sidebar${contextOpen ? '' : ' app__sidebar--collapsed'}`}
          aria-label="Customer and system context"
        >
          {/* Collapses the context stack on narrow screens. */}
          <button
            type="button"
            className="ctx-toggle"
            onClick={() => setContextOpen((open) => !open)}
            aria-expanded={contextOpen}
            aria-controls="context-panels"
          >
            <span>{contextOpen ? 'HIDE CONTEXT' : 'CUSTOMER CONTEXT'}</span>
            <span className="ctx-toggle__chev" aria-hidden="true">
              {contextOpen ? '▲' : '▼'}
            </span>
          </button>

          <div className="sidebar__panels" id="context-panels">
            <CustomerContext
              customerId={customerId}
              onCustomerIdChange={handleCustomerIdChange}
              apiKeyRef={apiKeyRef}
              hasApiKey={hasApiKey}
              onApiKeyPresenceChange={setHasApiKey}
              conversationId={conversationId}
              online={online}
              locked={loading}
            />
            <SystemStatus
              online={online}
              health={health}
              checking={checking}
              error={error?.code === 'NETWORK_ERROR' ? error.message : null}
              onRetry={checkHealth}
            />
          </div>
        </aside>

        {/* ---------------------------------------------------- centre core */}
        <section className="app__center" aria-label="Support core and conversation">
          <AgentCore
            state={coreState}
            detail={loading ? 'Awaiting a verified response from the backend.' : null}
          />

          <AgentActivity
            items={loading ? liveItem(coreState) : timeline}
            busy={loading}
          />

          {escalation ? (
            <EscalationCard
              ticketId={escalation.ticketId}
              conversationId={escalation.conversationId}
            />
          ) : null}

          {error ? (
            <ErrorBanner
              message={error.message}
              code={error.code}
              requestId={error.requestId}
              onDismiss={() => setError(null)}
            />
          ) : null}

          <ChatWindow
            messages={messages}
            loading={loading}
            coreState={coreState}
            coreDetail={loading ? 'Awaiting a verified response from the backend.' : null}
            endRef={endRef}
          />

          <div className="app__composer">
            <QuickActions onPick={handleQuickAction} disabled={loading} />
            <ChatInput
              value={draft}
              onChange={setDraft}
              onSubmit={handleSend}
              disabled={loading}
              canSend={canSend}
            />
          </div>
        </section>

        {/* ------------------------------------------------------ right rail */}
        <aside className="app__rail" aria-label="Security status">
          <SecurityStatus online={online} />
        </aside>
      </main>
    </div>
  )
}
