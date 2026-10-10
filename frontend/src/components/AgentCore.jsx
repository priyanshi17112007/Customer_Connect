import '../styles/core.css'

/**
 * AI Core visual.
 *
 * Reflects the UI processing state only. The labels describe what the interface
 * is doing while awaiting the API - they are never a rendering of model
 * reasoning, and no internal reasoning is displayed anywhere in this app.
 */

const STATE_LABELS = {
  idle: 'STANDBY',
  analyzing: 'ANALYZING REQUEST',
  resolving: 'RESOLVING REQUEST',
  complete: 'RESPONSE READY',
  error: 'REQUEST FAILED',
}

export default function AgentCore({ state = 'idle', detail = null }) {
  return (
    <div className={`core core--${state}`}>
      <div className="core__stage" aria-hidden="true">
        <span className="core__ring core__ring--outer" />
        <span className="core__ring core__ring--mid" />
        <span className="core__ring core__ring--inner" />
        <span className="core__orbit">
          <span className="core__satellite" />
        </span>
        <span className="core__orb" />
        <span className="core__scan" />
      </div>

      <div className="core__meta">
        <p className="core__name mono">RESOLVEAI CORE</p>
        <p
          className="core__state mono"
          role="status"
          aria-live="polite"
        >
          {STATE_LABELS[state] ?? STATE_LABELS.idle}
        </p>
        {detail ? <p className="core__detail">{detail}</p> : null}
      </div>
    </div>
  )
}
