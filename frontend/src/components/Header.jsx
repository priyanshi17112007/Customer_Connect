import '../styles/header.css'

/**
 * Top bar: product identity plus live connection/security indicators.
 * Deliberately sparse - status only, no navigation clutter.
 */
export default function Header({ online, health }) {
  return (
    <header className="app-header">
      <div className="app-header__brand">
        <div className="app-header__mark" aria-hidden="true">
          <span className="app-header__mark-dot" />
        </div>
        <div className="app-header__titles">
          <h1 className="app-header__title">RESOLVEAI</h1>
          <p className="app-header__subtitle">
            AI-Powered Customer Support Resolution System
          </p>
        </div>
      </div>

      <div className="app-header__status">
        <span
          className={`status-chip ${online ? 'status-chip--online' : 'status-chip--offline'}`}
        >
          <span className="status-chip__dot" aria-hidden="true" />
          <span className="mono">
            {online ? 'SYSTEM ONLINE' : 'SYSTEM OFFLINE'}
          </span>
          {health?.version ? (
            <span className="status-chip__meta mono">v{health.version}</span>
          ) : null}
        </span>

        <span className="status-chip status-chip--static">
          <span className="status-chip__lock" aria-hidden="true">
            ▪
          </span>
          <span className="mono">SECURITY ACTIVE</span>
        </span>
      </div>
    </header>
  )
}
