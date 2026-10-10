/**
 * Quick actions.
 *
 * These only populate the composer. They never call the API and never produce
 * a reply - every answer comes from the backend.
 */

export const QUICK_ACTIONS = [
  { id: 'track', label: 'Track Order', text: 'Where is my order?' },
  { id: 'payment', label: 'Payment Issue', text: 'I have a problem with my payment.' },
  { id: 'return', label: 'Return Request', text: 'Can I return my order?' },
  { id: 'refund', label: 'Refund Status', text: 'Where is my refund?' },
]

export default function QuickActions({ onPick, disabled }) {
  return (
    <div className="quick" role="group" aria-label="Quick actions">
      <span className="quick__label label">Quick Actions</span>
      <div className="quick__row">
        {QUICK_ACTIONS.map((action) => (
          <button
            key={action.id}
            type="button"
            className="chip"
            onClick={() => onPick(action.text)}
            disabled={disabled}
          >
            <span className="mono">{action.label.toUpperCase()}</span>
          </button>
        ))}
      </div>
    </div>
  )
}
