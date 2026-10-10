/**
 * In-flight request indicator.
 *
 * Describes the UI phase only. It does not display, summarise or infer the
 * agent's internal reasoning - the backend never exposes that.
 */
export default function ProcessingState({ state = 'analyzing', detail = null }) {
  const label =
    state === 'resolving' ? 'RESOLVING REQUEST' : 'ANALYZING REQUEST'

  return (
    <div className="proc" role="status" aria-live="polite">
      <div className="proc__bar">
        <span className="proc__bar-fill" />
      </div>
      <div className="proc__body">
        <span className="proc__pulse" aria-hidden="true" />
        <div className="proc__text">
          <p className="proc__label mono">{label}</p>
          {detail ? <p className="proc__detail">{detail}</p> : null}
        </div>
        <span className="proc__dots" aria-hidden="true">
          <i />
          <i />
          <i />
        </span>
      </div>
    </div>
  )
}
