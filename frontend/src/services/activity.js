/**
 * Backend activity -> safe timeline items.
 *
 * The backend already returns safe operational events in `ChatData.activity`
 * (see backend/app/api/schemas.py -> AgentActivityItem). This module only
 * *relabels* those events. It deliberately does not:
 *
 *   - invent a stage the backend did not report;
 *   - render a backend string verbatim as a UI label;
 *   - read `details`, which can carry an order id, a threat-classifier value,
 *     an internal error code or a list of internal tool names.
 *
 * Every label below is an explicit allow-list entry. Anything unrecognised
 * collapses to a single generic label, so a new or internal backend string can
 * never reach the screen as-is.
 *
 * No chain-of-thought, prompt text or tool argument is read or displayed: the
 * backend does not return them, and this module does not look for them.
 */

/** Tool identity -> operator-facing label. `tool_name` is only honoured here. */
const TOOL_LABELS = {
  get_order: 'ORDER VERIFIED',
  get_delivery_status: 'CHECKING DELIVERY',
  get_payment_status: 'CHECKING PAYMENT',
  check_refund_status: 'CHECKING REFUND',
  check_return_eligibility: 'CHECKING RETURN ELIGIBILITY',
  get_customer: 'VERIFYING CUSTOMER',
  create_support_ticket: 'CREATING SUPPORT TICKET',
}

/** Detected intent -> operator-facing label. */
const INTENT_LABELS = {
  order_status: 'ORDER STATUS',
  delivery_status: 'DELIVERY STATUS',
  payment_status: 'PAYMENT STATUS',
  refund_status: 'REFUND STATUS',
  return_eligibility: 'RETURN ELIGIBILITY',
  account: 'ACCOUNT',
  general: 'GENERAL INQUIRY',
}

/** Fixed backend step strings -> operator-facing labels. */
const STEP_LABELS = {
  'Input received and validated': 'REQUEST RECEIVED',
  'Input validation failed': 'REQUEST REJECTED',
  'Security check passed': 'SECURITY CHECK PASSED',
  'Security check blocked the request': 'REQUEST BLOCKED',
  'Intent: unknown': 'INTENT NOT RECOGNISED',
  'Required information available': 'INFORMATION COLLECTED',
  'Required information missing': 'INFORMATION MISSING',
  'Tool plan created': 'TOOLS SELECTED',
  'No tool required for this request': 'NO TOOLS REQUIRED',
  'Verified information gathered; resolution ready': 'INFORMATION VERIFIED',
  'Automatic resolution not possible; escalation required': 'ESCALATING TO SUPPORT',
  'Support ticket created': 'SUPPORT TICKET CREATED',
  'Ticket creation failed': 'TICKET CREATION FAILED',
  'Resolution stopped': 'REQUEST FAILED',
  'Iteration limit reached; stopping safely': 'PROCESSING LIMIT REACHED',
  'Escalation not possible': 'ESCALATION UNAVAILABLE',
  'Escalation skipped': 'ESCALATION SKIPPED',
  'Agent stopped safely': 'PROCESSING ERROR',
  'Conversation memory unavailable': 'CONTEXT UNAVAILABLE',
  'Reply phrasing refined by language model': 'RESPONSE FINALISED',
  'Kept deterministic reply': 'RESPONSE FINALISED',
}

const INTENT_PREFIX = 'Intent identified:'
const TOOL_PREFIX = 'Tool executed:'

/** Shown for any event we do not explicitly recognise. */
export const GENERIC_LABEL = 'PROCESSING REQUEST'

/**
 * Allow-list lookup.
 *
 * The tables above are plain object literals, so a bare `table[key]` would also
 * resolve inherited Object.prototype members: a backend value of "constructor"
 * or "toString" would return a truthy function and be rendered as a label, which
 * React rejects as a child. Only own, explicitly listed keys may match.
 */
function listed(table, key) {
  if (typeof key !== 'string') return null
  return Object.hasOwn(table, key) ? table[key] : null
}

/** Labels that already describe an escalation outcome. */
const ESCALATION_LABELS = new Set([
  'ESCALATING TO SUPPORT',
  'SUPPORT TICKET CREATED',
  'TICKET CREATION FAILED',
  'ESCALATION UNAVAILABLE',
  'ESCALATION SKIPPED',
])

function toolLabel(raw) {
  if (typeof raw !== 'string') return null
  return listed(TOOL_LABELS, raw.trim())
}

/**
 * Resolve one raw activity item into a safe display item.
 * Returns null when the item carries nothing displayable.
 */
function mapItem(raw, index) {
  if (!raw || typeof raw !== 'object') return null

  const step = typeof raw.step === 'string' ? raw.step : ''
  const backendStatus = typeof raw.status === 'string' ? raw.status.toLowerCase() : ''
  // `details` is intentionally never read.
  void raw.details

  let label = listed(STEP_LABELS, step)
  let sublabel = null

  if (!label && step.startsWith(INTENT_PREFIX)) {
    const intent = step.slice(INTENT_PREFIX.length).trim()
    label = 'INTENT DETECTED'
    sublabel = listed(INTENT_LABELS, intent)
  }

  if (!label && step.startsWith(TOOL_PREFIX)) {
    // The structured `tool_name` is authoritative. If it is present but not
    // allow-listed, the event is treated as unrecognised rather than falling
    // back to the prose step, so a mismatched pair cannot smuggle a label.
    let tool = null
    if (typeof raw.tool_name === 'string' && raw.tool_name.trim()) {
      tool = toolLabel(raw.tool_name)
    } else {
      tool = toolLabel(step.slice(TOOL_PREFIX.length))
    }
    label = tool ?? GENERIC_LABEL
  }

  if (!label) {
    // A recognised tool_name on an otherwise unknown step is still safe to
    // show as a tool stage; anything else becomes generic.
    const tool = toolLabel(raw.tool_name)
    label = tool ?? GENERIC_LABEL
  }

  const failed = backendStatus === 'error'
  const escalated = ESCALATION_LABELS.has(label)

  return {
    id: `a${index}`,
    label,
    sublabel,
    state: failed ? (escalated ? 'escalated' : 'error') : 'completed',
  }
}

/**
 * Convert one backend response into the timeline for that request.
 *
 * @param {Array} activity  raw `ChatData.activity`
 * @param {object} outcome  { resolved, escalated, errorCode } from the same
 *                           response, used for one terminal summary node only.
 */
export function mapActivity(activity, outcome = {}) {
  const source = Array.isArray(activity) ? activity : []
  const items = []
  source.forEach((raw, index) => {
    const item = mapItem(raw, index)
    if (item) items.push(item)
  })

  // One terminal node, derived from the response's own resolution fields.
  // Skipped when the activity already ends the run with an escalation stage,
  // so the timeline never repeats itself.
  const alreadyEscalated = items.some((item) => item.state === 'escalated')
  let terminal = null

  if (outcome.escalated && !alreadyEscalated) {
    terminal = { label: 'ESCALATED TO SUPPORT', state: 'escalated' }
  } else if (outcome.resolved) {
    terminal = { label: 'RESOLUTION COMPLETE', state: 'completed' }
  } else if (outcome.errorCode) {
    terminal = { label: 'REQUEST FAILED', state: 'error' }
  }

  if (terminal) {
    items.push({ id: `t${items.length}`, sublabel: null, ...terminal })
  }

  return items
}

/** The single live node shown while a request is in flight. */
export function liveItem(phase = 'analyzing') {
  return [
    {
      id: 'live',
      label: phase === 'resolving' ? 'RESOLVEAI CORE PROCESSING' : 'ANALYZING REQUEST',
      sublabel: null,
      state: 'active',
    },
  ]
}

/** The node shown when the request itself failed before any activity existed. */
export function failedItem() {
  return [
    {
      id: 'failed',
      label: 'REQUEST FAILED',
      sublabel: null,
      state: 'error',
    },
  ]
}