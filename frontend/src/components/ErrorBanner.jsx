/**
 * Error notice for the current request.
 *
 * `detail` must already be a safe, human-readable string produced by the API
 * client. Raw backend internals, tracebacks and credentials are never rendered.
 */
export default function ErrorBanner({ message, code, requestId, onDismiss }) {
  if (!message) return null

  return (
    <div className="banner" role="alert">
      <span className="banner__icon" aria-hidden="true">
        ✕
      </span>

      <div className="banner__body">
        <p className="banner__message">{message}</p>
        <p className="banner__meta mono">
          {code ? <span className="banner__code">{code}</span> : null}
          {requestId ? <span>REQUEST ID: {requestId}</span> : null}
        </p>
      </div>

      {onDismiss ? (
        <button
          type="button"
          className="banner__close"
          onClick={onDismiss}
          aria-label="Dismiss error"
        >
          ✕
        </button>
      ) : null}
    </div>
  )
}
