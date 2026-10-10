/**
 * Escalation notice.
 *
 * Every value shown here comes from the backend response. The ticket ID is
 * displayed only when the backend actually issued one - it is never generated
 * client-side.
 */
export default function EscalationCard({ ticketId, conversationId }) {
  return (
    <section className="escalation" role="status" aria-live="polite">
      <div className="escalation__icon" aria-hidden="true">
        ⚠
      </div>

      <div className="escalation__body">
        <p className="escalation__title mono">HUMAN SUPPORT REQUIRED</p>
        <p className="escalation__text">
          Your request has been escalated to the support team.
        </p>

        <dl className="escalation__grid">
          <div className="escalation__row">
            <dt className="label">Ticket</dt>
            <dd className="mono">{ticketId ?? 'created — awaiting reference'}</dd>
          </div>
          <div className="escalation__row">
            <dt className="label">Conversation</dt>
            <dd className="mono">{conversationId ?? '—'}</dd>
          </div>
        </dl>
      </div>
    </section>
  )
}
