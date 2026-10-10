/**
 * Security status.
 *
 * Lists only controls the backend genuinely enforces. This is a readout, not a
 * client-side enforcement layer - authentication, authorization, guardrails and
 * rate limiting all happen server-side.
 */

const CONTROLS = [
  'API authentication',
  'Input validation',
  'Guardrails active',
  'Authorization active',
  'Rate limiting active',
]

export default function SecurityStatus({ online }) {
  return (
    <section className="panel" aria-labelledby="sec-status-title">
      <header className="panel__head">
        <h2 className="panel__title label" id="sec-status-title">
          Security Status
        </h2>
      </header>

      <div className="panel__body">
        <p className="panel__note">
          Enforced server-side by the ResolveAI API.
        </p>
        <ul className="control-list">
          {CONTROLS.map((control) => (
            <li className="control-list__item" key={control}>
              <span
                className={`control-list__mark ${online ? 'control-list__mark--on' : 'control-list__mark--idle'}`}
                aria-hidden="true"
              >
                ✓
              </span>
              <span>{control}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}
