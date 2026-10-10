import { useId, useState } from 'react'

import AgentActivity from './AgentActivity'

/**
 * One chat message.
 *
 * `meta` is backend-supplied operational data (intent, decision, resolution
 * state, request id). `activity` is the timeline the backend reported for this
 * exact request, already labelled by services/activity.js. Tool arguments are
 * never rendered - the backend does not return them, and chain-of-thought is
 * never shown.
 */

/** Humanise a machine token like ORDER_NOT_ACCESSIBLE. */
function humanise(code) {
  if (!code) return null
  return String(code)
    .toLowerCase()
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

export default function MessageBubble({ role, text, time, meta = null, activity = null }) {
  const isUser = role === 'user'
  const isError = meta?.tone === 'error'
  const stages = Array.isArray(activity) ? activity : []
  const [showStages, setShowStages] = useState(false)
  const stagesId = useId()

  return (
    <article className={`msg msg--${isUser ? 'user' : 'agent'}`}>
      <div className="msg__gutter">
        <span className={`msg__avatar ${isUser ? 'msg__avatar--user' : 'msg__avatar--agent'}`}>
          {isUser ? 'USR' : 'AI'}
        </span>
      </div>

      <div className="msg__content">
        <header className="msg__head">
          <span className="msg__author mono">{isUser ? 'USER' : 'RESOLVEAI'}</span>
          {time ? <time className="msg__time mono">{time}</time> : null}
        </header>

        {meta?.badge ? (
          <span className={`msg__badge ${isError ? 'msg__badge--error' : meta.tone === 'warning' ? 'msg__badge--warn' : 'msg__badge--ok'}`}>
            {meta.badge}
          </span>
        ) : null}

        <p className="msg__text">{text}</p>

        {meta?.fields?.length ? (
          <ul className="msg__fields">
            {meta.fields.map(([label, value]) => (
              <li className="msg__field" key={label}>
                <span className="msg__field-key label">{label}</span>
                <span className="msg__field-val mono">{value}</span>
              </li>
            ))}
          </ul>
        ) : null}

        {meta?.note ? <p className="msg__note">{meta.note}</p> : null}

        {/* Per-request timeline, collapsed by default so the transcript stays
            dominant. Each message keeps only its own activity. */}
        {stages.length ? (
          <div className="msg__activity">
            <button
              type="button"
              className="act-toggle"
              aria-expanded={showStages}
              aria-controls={stagesId}
              onClick={() => setShowStages((open) => !open)}
            >
              <span>
                {showStages ? 'HIDE' : 'SHOW'} AGENT ACTIVITY · {stages.length}
              </span>
              <span className="act-toggle__chev" aria-hidden="true">
                {showStages ? '▲' : '▼'}
              </span>
            </button>

            {showStages ? (
              <div id={stagesId}>
                <AgentActivity items={stages} variant="inline" />
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </article>
  )
}
