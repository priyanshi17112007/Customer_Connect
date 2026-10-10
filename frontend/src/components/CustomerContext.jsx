/**
 * Customer context: identity inputs and live conversation state.
 *
 * The API key field is deliberately UNCONTROLLED. React mirrors a controlled
 * input's value onto the element's `value` content attribute, which would put
 * the credential into the serialised DOM. Keeping the field uncontrolled means
 * the key lives only in the input element's live value property and in a ref
 * that is read once, at submit time. The parent is told only whether a key is
 * present, never what it is.
 *
 * The key is never persisted to storage and never rendered back to the user.
 * The conversation ID is issued by the backend; this component only shows it.
 */

/** Show an identifier in a recognisable but non-raw form. */
function maskId(value, keepHead = 6, keepTail = 3) {
  if (!value) return null
  if (value.length <= keepHead + keepTail) return value
  return `${value.slice(0, keepHead)}…${value.slice(-keepTail)}`
}

export default function CustomerContext({
  customerId,
  onCustomerIdChange,
  apiKeyRef,
  hasApiKey,
  onApiKeyPresenceChange,
  conversationId,
  online,
  locked,
}) {
  return (
    <section className="panel" aria-labelledby="ctx-title">
      <header className="panel__head">
        <h2 className="panel__title label" id="ctx-title">
          Customer Context
        </h2>
      </header>

      <div className="panel__body panel__body--form">
        <div className="field">
          <label className="field__label" htmlFor="customer-id">
            Customer ID
          </label>
          <input
            id="customer-id"
            className="field__input mono"
            type="text"
            value={customerId}
            onChange={(event) => onCustomerIdChange(event.target.value)}
            placeholder="CUS-001"
            autoComplete="off"
            spellCheck="false"
            maxLength={16}
            aria-describedby="customer-id-hint"
            disabled={locked}
          />
          <p className="field__hint" id="customer-id-hint">
            Format CUS-###. Validated by the API.
          </p>
        </div>

        <div className="field">
          <label className="field__label" htmlFor="api-key">
            <span className="field__lock" aria-hidden="true">
              ⬢
            </span>
            API Access Key
          </label>
          <div className="field__wrap">
            <input
              id="api-key"
              ref={apiKeyRef}
              className="field__input field__input--secret mono"
              type="password"
              defaultValue=""
              onChange={(event) =>
                onApiKeyPresenceChange(event.target.value.trim().length > 0)
              }
              placeholder="••••••••••••"
              autoComplete="off"
              spellCheck="false"
              aria-describedby="api-key-hint"
              disabled={locked}
            />
          </div>
          <p className="field__hint" id="api-key-hint">
            {hasApiKey
              ? 'Key entered. Held in this field only - never displayed or stored.'
              : 'Held in this field only. Never displayed or stored.'}
          </p>
        </div>

        <dl className="kv">
          <div className="kv__row">
            <dt className="kv__key label">Conversation</dt>
            <dd className="kv__val mono" title={conversationId ?? undefined}>
              {conversationId ? maskId(conversationId) : 'not started'}
            </dd>
          </div>
          <div className="kv__row">
            <dt className="kv__key label">Connection</dt>
            <dd
              className={`kv__val kv__val--${online ? 'on' : 'off'}`}
            >
              <span className="kv__dot" aria-hidden="true" />
              <span className="mono">{online ? 'CONNECTED' : 'OFFLINE'}</span>
            </dd>
          </div>
        </dl>
      </div>
    </section>
  )
}
