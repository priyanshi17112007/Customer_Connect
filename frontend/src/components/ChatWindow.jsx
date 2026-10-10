import MessageBubble from './MessageBubble'
import ProcessingState from './ProcessingState'

/**
 * Conversation transcript.
 *
 * Renders only what the backend returned. The empty state explains how to start;
 * there is no demo transcript and no sample answer that could be mistaken for a
 * real resolution.
 */
export default function ChatWindow({ messages, loading, coreState, coreDetail, endRef }) {
  return (
    <div className="chat" role="log" aria-live="polite" aria-label="Conversation transcript">
      <div className="chat__stream">
        {messages.length === 0 && !loading ? (
          <div className="chat__empty">
            <div className="chat__empty-mark" aria-hidden="true">
              ◉
            </div>
            <h2 className="chat__empty-title">Ready to resolve</h2>
            <p className="chat__empty-text">
              Enter your customer ID and API access key, then describe your
              issue. Every answer is produced by the ResolveAI backend from
              verified records.
            </p>
            <ul className="chat__empty-list">
              <li>Ask about an order, payment, delivery, return or refund</li>
              <li>Responses are answered only from verified backend data</li>
              <li>Unresolved requests are escalated to a real support ticket</li>
            </ul>
          </div>
        ) : null}

        {messages.map((message) => (
          <MessageBubble
            key={message.id}
            role={message.role}
            text={message.text}
            time={message.time}
            meta={message.meta}
            activity={message.activity}
          />
        ))}

        {loading ? <ProcessingState state={coreState} detail={coreDetail} /> : null}

        <div ref={endRef} />
      </div>
    </div>
  )
}
