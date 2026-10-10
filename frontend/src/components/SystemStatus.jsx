/**
 * Connection panel: backend health, DB state, and manual retry.
 *
 * Informational only - it reports what GET /health actually returned and never
 * claims controls the backend does not implement.
 */

const ROWS = [
  {
    key: 'backend',
    label: 'Backend',
    onlineKey: 'online',
    onlineLabel: 'ONLINE',
    offlineLabel: 'OFFLINE',
  },
  {
    key: 'agent',
    label: 'Agent',
    onlineLabel: 'READY',
    offlineLabel: 'UNREACHABLE',
  },
]

export default function SystemStatus({ online, health, checking, onRetry, error }) {
  return (
    <section className="panel" aria-labelledby="sys-status-title">
      <header className="panel__head">
        <h2 className="panel__title label" id="sys-status-title">
          System Status
        </h2>
        <span className={`panel__badge ${online ? 'panel__badge--ok' : 'panel__badge--bad'}`}>
          {checking ? 'CHECKING' : online ? 'ONLINE' : 'OFFLINE'}
        </span>
      </header>

      <div className="panel__body">
        <ul className="status-list">
          {ROWS.map((row) => {
            const state = online ? 'on' : 'off'
            return (
              <li className="status-list__row" key={row.key}>
                <span className="status-list__label">{row.label}</span>
                <span className={`status-list__value status-list__value--${state}`}>
                  <span className="status-list__dot" aria-hidden="true" />
                  <span className="mono">
                    {online ? row.onlineLabel : row.offlineLabel}
                  </span>
                </span>
              </li>
            )
          })}

          <li className="status-list__row">
            <span className="status-list__label">Database</span>
            <span
              className={`status-list__value status-list__value--${
                health?.database === 'connected' ? 'on' : 'off'
              }`}
            >
              <span className="status-list__dot" aria-hidden="true" />
              <span className="mono">
                {(health?.database ?? 'unknown').toUpperCase()}
              </span>
            </span>
          </li>

          <li className="status-list__row">
            <span className="status-list__label">Version</span>
            <span className="status-list__value mono status-list__value--plain">
              {health?.version ? `v${health.version}` : '—'}
            </span>
          </li>
        </ul>

        {!online ? (
          <div className="panel__alert" role="status">
            <p className="panel__alert-title mono">BACKEND CONNECTION LOST</p>
            <p className="panel__alert-text">
              {error || 'The ResolveAI backend is not responding.'}
            </p>
            <button
              type="button"
              className="btn btn--secondary btn--block"
              onClick={onRetry}
              disabled={checking}
            >
              {checking ? 'CONNECTING…' : 'RETRY CONNECTION'}
            </button>
          </div>
        ) : null}
      </div>
    </section>
  )
}
