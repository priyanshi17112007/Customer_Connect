/**
 * ResolveAI API client.
 *
 * Talks to the existing FastAPI backend. The backend is the source of truth:
 * this module performs no business logic and never fabricates a response.
 *
 * Contracts (verified against backend/app/api/schemas.py + routes.py):
 *   GET  /health        -> { success, data: { status, version, database, timestamp }, request_id }
 *   POST /api/v1/chat    -> { success, data: ChatData, request_id }
 *   errors              -> { success: false, error: { code, message, request_id } }
 */

const BASE_URL = (import.meta.env?.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000').replace(/\/+$/, '')

const REQUEST_TIMEOUT_MS = 30000

/**
 * Maps an HTTP status to a safe, human-readable message.
 * Never surfaces backend internals, tracebacks or credentials.
 */
const STATUS_MESSAGES = {
  400: 'The request could not be processed.',
  401: 'Authentication failed. Please check your API access key.',
  403: 'This request is not authorized.',
  404: 'The ResolveAI service endpoint was not found.',
  413: 'Your message is too large to send.',
  415: 'Unsupported request format.',
  422: 'Please check the information entered.',
  429: 'Too many requests. Please wait before trying again.',
  500: 'The support service encountered an internal error.',
  502: 'The support service is unreachable right now.',
  503: 'The support service is temporarily unavailable.',
  504: 'The support service took too long to respond.',
}

export class ApiError extends Error {
  constructor({ message, status = 0, code = 'NETWORK_ERROR', requestId = null, retryAfter = null }) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.requestId = requestId
    this.retryAfter = retryAfter
  }
}

/** Read the backend error envelope, tolerating a non-JSON body. */
async function readErrorEnvelope(response) {
  try {
    const body = await response.json()
    if (body && typeof body === 'object') {
      const err = body.error ?? {}
      return {
        code: typeof err.code === 'string' ? err.code : `HTTP_${response.status}`,
        message: typeof err.message === 'string' ? err.message : '',
        requestId: typeof err.request_id === 'string' ? err.request_id : null,
      }
    }
  } catch {
    // Body was empty or not JSON - fall through to the generic message.
  }
  return { code: `HTTP_${response.status}`, message: '', requestId: null }
}

async function request(path, { method = 'GET', apiKey, body, signal } = {}) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)

  // Allow the caller to cancel as well as the internal timeout.
  const onExternalAbort = () => controller.abort()
  if (signal) {
    if (signal.aborted) controller.abort()
    else signal.addEventListener('abort', onExternalAbort)
  }

  const headers = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  // The API key is sent per-request and never persisted by this module.
  if (apiKey) headers['X-API-Key'] = apiKey

  let response
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    })
  } catch (error) {
    if (error?.name === 'AbortError') {
      throw new ApiError({
        message: 'The request timed out. Please try again.',
        code: 'TIMEOUT',
      })
    }
    throw new ApiError({
      message: 'Unable to connect to ResolveAI backend.',
      code: 'NETWORK_ERROR',
    })
  } finally {
    clearTimeout(timer)
    if (signal) signal.removeEventListener('abort', onExternalAbort)
  }

  if (!response.ok) {
    const envelope = await readErrorEnvelope(response)
    const retryAfterHeader = response.headers.get('Retry-After')
    const retryAfter = retryAfterHeader ? Number(retryAfterHeader) || null : null
    // Prefer the backend's own safe message when it provided one.
    const message =
      envelope.message || STATUS_MESSAGES[response.status] || 'The request could not be completed.'
    throw new ApiError({
      message,
      status: response.status,
      code: envelope.code,
      requestId: envelope.requestId,
      retryAfter,
    })
  }

  try {
    return await response.json()
  } catch {
    throw new ApiError({
      message: 'The support service returned an unreadable response.',
      code: 'BAD_RESPONSE',
    })
  }
}

/** Liveness probe. Public - no API key required by the backend. */
export async function healthCheck({ signal } = {}) {
  const payload = await request('/health', { signal })
  return {
    online: true,
    status: payload?.data?.status ?? 'unknown',
    version: payload?.data?.version ?? null,
    database: payload?.data?.database ?? 'unknown',
    timestamp: payload?.data?.timestamp ?? null,
    requestId: payload?.request_id ?? null,
  }
}

/**
 * Submit a support request.
 *
 * Field names come straight from the backend ChatRequest model. `customer_id`
 * and `message` are required by the backend; `conversation_id` is optional and
 * is only sent once the backend has issued one.
 */
export async function sendChat({ customerId, message, conversationId, apiKey, signal }) {
  const body = { customer_id: customerId, message }
  if (conversationId) body.conversation_id = conversationId

  const payload = await request('/api/v1/chat', {
    method: 'POST',
    apiKey,
    body,
    signal,
  })

  const data = payload?.data ?? {}
  return {
    conversationId: data.conversation_id ?? null,
    response: data.response ?? '',
    intent: data.intent ?? null,
    decision: data.decision ?? null,
    resolved: Boolean(data.resolved),
    escalated: Boolean(data.escalated),
    ticketId: data.ticket_id ?? null,
    errorCode: data.error_code ?? null,
    activity: Array.isArray(data.activity) ? data.activity : [],
    toolCalls: Array.isArray(data.tool_calls) ? data.tool_calls : [],
    requestId: payload?.request_id ?? null,
  }
}

export { BASE_URL }
