import '../styles/activity.css'

/**
 * Agent Activity / Resolution Timeline.
 *
 * Renders the safe operational events the backend already returned in
 * `ChatData.activity`, labelled by services/activity.js. This component never
 * invents a stage, never shows model reasoning, and never displays tool
 * arguments, prompts, identifiers or internal error details.
 *
 * `items` always comes from a mapper, so every label here is allow-listed.
 */

const GLYPH = {
  active: '◉',
  completed: '✓',
  error: '✕',
  escalated: '!',
}

/** Screen-reader text for each state - the glyph alone is never the only cue. */
const STATE_TEXT = {
  active: 'Processing',
  completed: 'Completed',
  error: 'Failed',
  escalated: 'Escalated',
}

export default function AgentActivity({ items = [], variant = 'band', busy = false }) {
  const stages = items.filter(Boolean)
  if (stages.length === 0) return null

  const counts = stages.reduce((acc, item) => {
    acc[item.state] = (acc[item.state] ?? 0) + 1
    return acc
  }, {})

  const summary =
    `Agent activity: ${stages.length} stage${stages.length === 1 ? '' : 's'}. ` +
    `Last stage ${STATE_TEXT[stages[stages.length - 1].state].toLowerCase()}.`

  return (
    <section
      className={`act act--${variant}`}
      aria-label="Agent activity"
      aria-busy={busy || undefined}
    >
      {variant === 'band' ? (
        <header className="act__head">
          <span className="act__title label">Agent Activity</span>
          <span className="act__count mono">
            {stages.length} STAGE{stages.length === 1 ? '' : 'S'}
            {counts.error ? ` · ${counts.error} FAILED` : ''}
            {counts.escalated ? ` · ${counts.escalated} ESCALATED` : ''}
          </span>
        </header>
      ) : null}

      <ol className="act__list">
        {stages.map((item, index) => (
          <li
            key={item.id ?? index}
            className={`act__node act__node--${item.state}`}
          >
            <span className="act__mark" aria-hidden="true">
              {GLYPH[item.state] ?? GLYPH.completed}
            </span>

            <span className="act__body">
              <span className="act__label mono">{item.label}</span>
              {item.sublabel ? (
                <span className="act__sub">{item.sublabel}</span>
              ) : null}
            </span>

            <span className="sr-only">{STATE_TEXT[item.state]}</span>
          </li>
        ))}
      </ol>

      {/* One polite announcement per run instead of eleven live nodes. */}
      <p className="sr-only" role="status" aria-live="polite">
        {summary}
      </p>
    </section>
  )
}