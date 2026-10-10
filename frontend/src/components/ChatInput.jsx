import { useEffect, useRef } from 'react'

/**
 * Composer.
 *
 * Enter sends, Shift+Enter inserts a newline. The textarea grows with content
 * up to a cap, and is disabled while a request is in flight so a request cannot
 * be fired twice.
 */
const MAX_ROWS_PX = 168

export default function ChatInput({
  value,
  onChange,
  onSubmit,
  disabled,
  canSend,
}) {
  const ref = useRef(null)

  // Grow with content, then scroll internally.
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, MAX_ROWS_PX)}px`
  }, [value])

  function handleKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      if (!disabled && canSend) onSubmit()
    }
  }

  const reason = !canSend
    ? 'Enter a customer ID and API access key to send'
    : disabled
      ? 'Waiting for the current request to finish'
      : undefined

  return (
    <form
      className="composer"
      onSubmit={(event) => {
        event.preventDefault()
        if (!disabled && canSend) onSubmit()
      }}
    >
      <label className="sr-only" htmlFor="composer-input">
        Describe your issue or ask about an order
      </label>

      <textarea
        id="composer-input"
        ref={ref}
        className="composer__input"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Describe your issue or ask about an order…"
        rows={1}
        disabled={disabled}
        aria-describedby={reason ? 'composer-reason' : undefined}
        maxLength={2000}
      />

      <button
        type="submit"
        className="composer__send"
        disabled={disabled || !canSend}
        aria-label="Send request"
        title={reason ?? 'Send request'}
      >
        <span className="composer__send-text mono">SEND</span>
        <span className="composer__send-arrow" aria-hidden="true">
          →
        </span>
      </button>

      {reason ? (
        <p className="composer__reason" id="composer-reason">
          {reason}
        </p>
      ) : null}
    </form>
  )
}
