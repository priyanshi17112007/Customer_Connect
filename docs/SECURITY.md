# Security

Security documentation for the AI Customer Support Resolution Agent.

Scope: the FastAPI service in `backend/`, its agent layer, tools and database
access. This document describes what is enforced in code today, the residual
risks that remain, and the operational requirements for a production
deployment.

---

## 1. Threat model

### What the system is

A stateless JSON API in front of a deterministic support agent. The agent
classifies intent, selects a fixed set of tools, executes them against a
relational database, and answers **only** from the verified rows those tools
returned. An optional LLM layer may rephrase an already-verified answer; it can
never introduce a new fact, call a tool, or change a decision.

### Assets

| Asset | Why it matters |
|---|---|
| Customer PII | Names, contact details, order history, addresses |
| Payment and refund data | Financial records tied to a customer |
| Support tickets | Contain customer problem descriptions |
| Application credentials | `APP_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY` |
| The database file | `support.db` holds every record in one file |
| Agent control plane | Tool selection, prompts, escalation logic |

### Trust boundaries

```
Internet
   │  TLS (terminated by reverse proxy - NOT this service)
   ▼
[ Reverse proxy / load balancer ]        ← HSTS only takes effect here
   │
   ▼
[ TrustedHost ] → [ CORS ] → [ Request ID ] → [ Security headers ] →
[ Body size ] → [ Content type ] → [ Auth-failure throttle ] →
[ Rate limit ] → [ Auth: X-API-Key ] → [ Pydantic validation ]
   │
   ▼
[ Guardrails ] → [ Intent + entity extraction ] → [ Required-info check ]
   │
   ▼
[ Tool plan ] → [ Ownership check ] → [ Tool execution ] → [ Verified facts ]
   │
   ▼
[ Response builder ] → [ Optional LLM rephrase ] → [ Faithfulness check ] →
[ JSON response ]
```

Trust assumptions:

1. **The client is untrusted.** Every field, header and identifier is validated
   before use.
2. **The LLM is untrusted.** Its output is validated against verified facts and
   discarded on mismatch.
3. **The database is trusted but not exposed.** It is only reachable through
   parameterised tool calls.
4. **The network is untrusted.** This service does not terminate TLS.

### Threats in scope

| # | Threat | Control |
|---|---|---|
| T1 | Unauthenticated access to the API | `X-API-Key` on every non-public path |
| T2 | Brute-force / credential stuffing | Constant-time compare + failure throttle |
| T3 | IDOR — reading another customer's order | Fail-closed ownership check |
| T4 | Prompt injection to steer the agent | Input guardrails + fixed tool plan |
| T5 | Jailbreak to disable safety behaviour | Jailbreak pattern set |
| T6 | System-prompt or secret leakage | No prompt exposure, redaction on output |
| T7 | Tool abuse / unintended tool use | Fixed allow-list, client cannot select tools |
| T8 | Mass assignment of internal fields | `extra="forbid"` on every request model |
| T9 | Resource exhaustion | Body-size cap, rate limits, tool timeouts |
| T10 | Secret leakage via logs or errors | Central redaction + safe error bodies |
| T11 | Database or `.env` disclosure over HTTP | No static mounts; 404 on all paths |
| T12 | Information disclosure in responses | Neutral ownership error, no echo of PII |
| T13 | Clickjacking / MIME sniffing / referrer leak | Security headers, CSP `sandbox` |
| T14 | Host header attacks | `TrustedHostMiddleware` when configured |

### Threats explicitly out of scope

- Compromise of the host, the Python runtime, or a dependency.
- Physical access to the database file.
- Malicious first-party code.
- Denial of service at a volume that exhausts network or host capacity —
  application-level limits raise the cost but do not eliminate it.

---

## 2. Authentication

**Mechanism: a single shared API key sent in the `X-API-Key` header.**

- Implemented in `app/security/auth.py`; enforced as a FastAPI route dependency
  (`Depends(verify_api_key)`) on every non-public path. The middleware stack
  skips the public paths listed in `PUBLIC_PATHS`
  (`app/security/middleware.py`).
- Comparison uses `hmac.compare_digest`, so it is constant-time and does not
  leak the key through response timing.
- The key lives in the environment variable `APP_API_KEY`.
- On startup the key is registered with the redaction engine
  (`register_secret`), so it is masked even if it appears in an unexpected
  place such as third-party exception text.
- Rejection responses are deliberately generic and never disclose the expected
  key, its location, or why comparison failed.

| Condition | Status | Body |
|---|---|---|
| Header absent | `401` | `MISSING_API_KEY` |
| Header present, wrong value | `403` | `INVALID_API_KEY` |
| Header present, correct | — | Request proceeds |

### Public paths

Only these bypass authentication:

- `GET /health` — liveness/readiness.
- `/docs`, `/redoc`, `/openapi.json` — **only when documentation is enabled.**

### Secrets must not be committed

`APP_API_KEY` has an insecure built-in development fallback, defined as a
constant in `app/security/auth.py`. Its literal value is deliberately not
reproduced here so that automated secret scanners do not flag this document.
Setting `APP_API_KEY` is mandatory for any non-local deployment.

> **The shared API key does not establish individual customer identity.**
> Every caller presenting the key is indistinguishable from every other caller.
> `customer_id` in the request body is a *claim supplied by the caller*, not a
> verified identity. See §3.

---

## 3. Authorization and IDOR

Authentication and authorization are separate controls. Authentication answers
"is this caller allowed in?"; authorization answers "is this caller allowed to
see *this* record?" The system implements both, but only the second one is
bound to a customer.

### The ownership check

`enforce_ownership()` in `app/agents/support_agent.py` guards every
order-scoped tool:

```
get_order  get_delivery_status  get_payment_status
check_refund_status  check_return_eligibility
```

The check **fails closed**:

1. If the observation did not succeed, it is returned unchanged.
2. If the tool is order-scoped and **no customer identity is present, access is
   denied**. Order data is never released "because we could not check".
3. Any order-scoped tool other than `get_order` requires that `get_order`
   already ran and was verified; the owner is taken from that verified row
   rather than re-queried.
4. If the recorded owner differs from the claimed customer, access is denied. An
   **absent** owner — a null column, a missing key, or a payload shape that
   carries no `customer_id` — is also denied: an unknown owner is not a match,
   so the check can never be skipped by omitting the value it compares.
5. On denial the loop stops, so no further tool in the plan runs.

The set of order-scoped tools has a single definition (`ORDER_SCOPED_TOOLS` in
`app/agents/support_agent.py`). `ToolSelector.ORDER_SCOPED_TOOLS` references that
same object rather than restating it, so a newly added order-scoped tool cannot
be silently excluded from the ownership check.

### Denial is indistinguishable from non-existence

Both a wrong customer and a missing order produce the identical
`ORDER_NOT_ACCESSIBLE` error and the same user-facing sentence. An attacker
therefore cannot use the response to discover which order IDs exist.

### Known weaknesses in this design

- **The owner is compared against a caller-supplied value.** Whoever holds the
  shared key can assert any `customer_id` and will then be authorised for that
  customer's data. This is a genuine IDOR at the *identity* layer and is **not
  fixed** in this phase. See §13.
- **Conversation scoping** uses the same claim: `AgentMemory` refuses to reuse a
  `conversation_id` belonging to a different `customer_id`, but that check is
  only as strong as the claim itself.

### Guardrails are not authorization

Input guardrails reject malicious *text*. They never grant or deny access to a
record. A well-behaved caller using someone else's `customer_id` is not
detected by guardrails — only by the ownership check, which trusts that same
claim.

---

## 4. Prompt injection

Untrusted input reaches an agent that selects tools and builds answers. The
controls are layered so that no single failure is sufficient.

**Input layer** (`app/security/guardrails.py`)

- A pattern set covering instruction-override attempts ("ignore previous
  instructions"), role reassignment ("you are now an admin"), delimiter
  escapes, encoded payloads and tool-directive attempts.
- On a match the request is refused with `BLOCKED_BY_GUARDRAIL` and **no tool
  runs**. The refused payload is logged through `safe_preview()`, which
  redacts and truncates it.

**Structural layer** — the more important control:

- The LLM has **no tool bindings**. It cannot call `get_order` or any other
  function.
- The tool plan is derived deterministically from a validated intent and the
  extracted entities, never from model output.
- Tool arguments come from validated entities with a strict format
  (`^ORD-\d{3,8}$`, `^CUS-\d{1,8}$`), not from free text.
- Every tool runs under an explicit allow-list with a per-call timeout.
- The final answer is assembled by `ResponseBuilder` from verified database
  rows. Model text can only be substituted for wording, not for facts.

**Prompt confidentiality**

- System prompts are never returned to clients.
- No endpoint exposes prompt or configuration content.

---

## 5. Jailbreak protection

A separate pattern set targets attempts to disable the assistant's behaviour
rather than to inject instructions: persona overrides, "DAN"-style framings,
hypothetical/fiction framings intended to bypass rules, encoded-obfuscation
attempts, and claims of prior authorisation ("I already have approval").

Matched requests are refused before planning. Like injection handling, the
payload is redacted and truncated in logs and never executed.

This is a **heuristic** control. It raises effort; it is not a guarantee.

**Precision matters as much as coverage.** A pattern that matches ordinary
language is an availability defect, because it refuses paying customers. The
`DAN` persona is therefore matched only in combination with an activation
phrase ("you are DAN", "act as DAN", "DAN mode", …), because the persona has no
effect without an instruction to act on it. An earlier version used an anchored
`r"DAN"` case-insensitively, which also blocked any message merely *containing*
those letters — "abundant", "vendor", "candidate" — and even a customer named
Dan. Both the benign cases and the attack forms are covered by a regression
test. When adding patterns, anchor them and test the false-positive rate.

---

## 6. Tool abuse

| Control | Implementation |
|---|---|
| Allow-list | Only registered tools can execute; unknown names are rejected. |
| No client selection | Clients send text only. There is no parameter naming a tool. |
| Least privilege | Read tools and `create_support_ticket` are separated; only the escalation path may write. |
| Timeouts | Each call is bounded; a hung tool cannot stall the request. |
| Iteration cap | The loop stops at `max_iterations`, so a pathological plan terminates. |
| Fail-stop | The first failed observation ends resolution rather than retrying into a weaker state. |
| Verified output | Responses are built from tool results, so a tool failure yields a safe message, never invented data. |
| Approval flags | Tools requiring approval cannot be auto-executed. |

Writes are limited to ticket creation during escalation, which derives the
customer from the verified conversation rather than from the message body.

---

## 7. Mass assignment

`ChatRequest` is the trust boundary and uses
`ConfigDict(extra="forbid")`. Unknown fields are rejected with `422` **before**
any agent, guardrail, tool or LLM work occurs.

This prevents a caller from smuggling privileged fields such as `role`,
`is_admin`, `system_prompt`, `tool`, `escalate`, `conversation_owner` or
internal flags into the application state.

Additional constraints on the same model:

| Field | Rule |
|---|---|
| `customer_id` | Required, `^CUS-\d{1,8}$`, max 16 chars |
| `message` | Required, 1–2000 chars, stripped, non-blank, no NUL |
| `conversation_id` | Optional, `^CONV-\d{1,12}$`, max 32 chars |

`conversation_id` is an opaque handle, never an authorisation token; ownership
is re-checked against `customer_id` when a conversation is resumed.

---

## 8. Rate limiting

| Scope | Limit | Window |
|---|---|---|
| `POST /api/v1/chat` | 20 requests | 60 s |
| Repeated auth failures per client IP | 10 | 300 s |
| Tool execution | 10 s per call | — |
| Agent loop | `max_iterations` | — |

- Counters are keyed per client IP and swept periodically to bound memory.
- Throttled responses return `429` with `Retry-After`. Request-budget responses
  additionally carry `X-RateLimit-Limit` and `X-RateLimit-Remaining`; the
  brute-force throttle keeps its own counter and so reports only `Retry-After`.
- Repeated auth failures are throttled **separately and more aggressively**, so
  credential guessing is throttled harder than legitimate use.

Limitations: the store is in-process. It does not coordinate across workers or
instances, and it resets on restart. See §13.

---

## 9. Request validation

Validation is layered; the first failure wins.

1. **Body size** — `Content-Length` above the configured cap is rejected with
   `413` before the body is read.
2. **Content type** — `POST` must be `application/json`, otherwise `415`.
3. **Authentication** — `X-API-Key` verified before the handler runs.
4. **Pydantic model** — types, patterns, length bounds, `extra="forbid"`.
5. **Agent input validator** — message must be non-empty, within bounds, and
   free of control characters.
6. **Entity format** — `order_id` and `customer_id` must match their strict
   patterns before they reach a query.

Validation errors return `422` with the **field names only**. Submitted values
are never echoed back, so the error channel cannot be used to reflect payloads.

---

## 10. Secrets management

| Secret | Variable | Purpose |
|---|---|---|
| API key | `APP_API_KEY` | Client authentication |
| Gemini key | `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Optional LLM |
| Groq key | `GROQ_API_KEY` | LLM fallback |
| Database URL | `DATABASE_URL` | Connection string |

Requirements:

- Secrets are read from the environment only. They are never hard-coded and
  never returned by an endpoint.
- `.env` is git-ignored (`.gitignore` line 1) and `*.db` is ignored
  (line 16), so neither local secrets nor the database file can be committed
  by accident.
- The LLM layer is optional and off by default. The service is fully functional
  with no API keys configured — it then uses the deterministic path.
- The live API key is registered with the redaction engine at import time, so
  it is masked in all output even if it leaks into an unexpected string.
- **The shipped default key is refused in production.** `APP_API_KEY` falls back
  to a well-known development value so the project runs with no setup. When
  `ENVIRONMENT=production` and that default is still in place, the application
  raises at startup and refuses to serve. Outside production the default is
  tolerated but logs a warning on every start.

> Rotate `APP_API_KEY` by restarting with a new value. There is exactly one
> active key; rotation is not zero-downtime.

> The environment variable that governs CORS is `CORS_ALLOWED_ORIGINS`.
> `ALLOWED_ORIGINS` is **not** read by the application; an earlier
> `.env.example` documented that name, which meant the setting silently did
> nothing and the built-in default applied. The current `.env.example` matches
> the code exactly.

---

## 11. Logging and redaction

Central redaction lives in `app/security/redaction.py` and is used by logging,
security events, error handling and stored operational metadata.

**Masked by pattern**

- Google API keys (`AIza…`), Groq keys (`gsk_…`)
- `Authorization:` headers including the scheme-qualified forms
  (`Bearer`, `Basic`, `Digest`, `Token`)
- API-key assignments (`api_key=`, `x-api-key:`, `apikey:`)
- Passwords, passphrases, secrets, tokens, private keys
- `DATABASE_URL` and `postgres://` / `mysql://` connection strings

**Masked as PII**

- Email addresses
- Phone numbers
- Values under a sensitive dictionary key (`api_key`, `password`, `token`,
  `ssn`, `card_number`, `cvv`, …), applied recursively through nested dicts and
  lists

**Masked literally**

- The active `APP_API_KEY`, whatever format it takes

**Structural controls**

- `redact_headers()` masks sensitive request headers before logging.
- `safe_preview()` redacts *and* truncates untrusted input, so attack payloads
  are not stored verbatim.
- Identifiers can be masked with `mask_identifier()`.
- Tool call parameters are **not** echoed to clients; only the tool name,
  status and error code are returned.
- `AgentLog` rows record operational events, never chain-of-thought.
- Every tool exception is logged through `redact()`. Raw driver exception text
  is never written directly, because SQLAlchemy/psycopg messages routinely
  embed bound parameters, column values and connection strings.
- **Direct-contact PII is stripped before any third-party LLM call.** The
  optional rephrase step sends `llm_safe_facts()`, which removes `name`,
  `email`, `phone`, `address` and similar keys from the verified facts, and the
  customer's own message is redacted first. Server-side logging keeps the full
  `verified_facts()`. The deterministic reply templates never use these fields,
  so nothing is lost from the answer.

Security events are emitted as structured records
(`app/security/secops.py`) with a request ID, endpoint, method, status,
severity and client IP. See §13 for the lack of alerting.

---

## 12. Security headers, CORS and error handling

### Headers

Applied to every response by `SecurityHeadersMiddleware`:

| Header | Value |
|---|---|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `no-referrer` |
| `Cache-Control` | `no-store` |
| `Pragma` | `no-cache` |
| `Cross-Origin-Opener-Policy` | `same-origin` |
| `Cross-Origin-Resource-Policy` | `same-origin` |
| `X-Permitted-Cross-Domain-Policies` | `none` |
| `Content-Security-Policy` | `default-src 'none'; frame-ancestors 'none'; base-uri 'none'; sandbox` |
| `Permissions-Policy` | `geolocation=(), microphone=(), camera=(), payment=()` |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` |
| `X-Request-ID` | per-request correlation ID |

The `server` header is removed by the middleware, but that is **not sufficient on
a real deployment**: uvicorn adds `Server: uvicorn` in its own protocol layer
*after* the ASGI application returns, so an in-app delete cannot remove it. The
header only disappears when the server is started with:

```
uvicorn app.main:app --no-server-header --no-date-header
```

Verified behaviour: with the default flags the live server returns
`Server: uvicorn`; with `--no-server-header` the header is absent. Passing these
flags is a deployment requirement. A reverse proxy that strips the header has
the same effect.

`Strict-Transport-Security` is only honoured by browsers over HTTPS. Because
this service does not terminate TLS, **HSTS is effective only behind a reverse
proxy that serves HTTPS**. Served over plain HTTP it is a harmless no-op.

### CORS

- Explicit allow-list from `CORS_ALLOWED_ORIGINS`; the default permits only
  local development origins (`localhost:5173`, `localhost:3000`, `127.0.0.1:5173`).
- `*` is rejected and filtered out even if configured.
- `allow_credentials=False`, so the API key can never ride on a cookie.
- Allowed methods `GET`, `POST`, `OPTIONS`; allowed headers are the minimum
  required set.
- CORS is a browser policy. It is **not** an authentication or authorization
  mechanism, and a non-browser client ignores it entirely.

### Why CORS is the outermost layer

`CORSMiddleware` is added **last** in `create_app()`, and Starlette treats the
most recently added middleware as the outermost layer. CORS therefore wraps the
entire security stack:

```
Request
  ↓
CORS
  ↓
Request context (request ID)
  ↓
Security headers
  ↓
Request size limit
  ↓
Content-type validation
  ↓
Auth-failure handling
  ↓
Rate limiting
  ↓
Route  (X-API-Key authentication, Pydantic validation)
  ↓
Agent
```

Starlette builds the chain inside-out: `register_security_middleware()` adds the
security layers so they execute in logical order, and `CORSMiddleware` is
registered after it so that it wraps them all.

The reason for this placement is short-circuit responses. When an inner layer
rejects a request — a `401`/`403` from the auth guard, a `413` from the body-size
cap, a `429` from a rate limit — the response is produced by that layer and does
not continue inward. With CORS *inside* the security stack it would never travel
back out through `CORSMiddleware`, so the reply would carry no
`Access-Control-Allow-Origin`. The browser would then refuse to expose it and the
client would report an opaque network error instead of the safe error body,
discarding the `Retry-After` hint on a throttled request. With CORS outermost,
those short-circuited responses are decorated normally, and the exposed headers
(`X-Request-ID`, `Retry-After`) reach the frontend, which can then display the
error code and honour the retry delay.

This is a **response-delivery** fix only. The policy is unchanged and was not
weakened: the explicit allow-list, the wildcard rejection and
`allow_credentials=False` are exactly as before. Placement does not turn CORS
into an authentication, authorization or CSRF control — it only ensures that a
response the server was always going to send is legible to a permitted browser
origin. A non-browser client ignores CORS completely, and an origin outside the
allow-list still receives no CORS headers.

### Host header validation

`TrustedHostMiddleware` is enabled when `ALLOWED_HOSTS` is set, blocking
Host-header poisoning and cache-poisoning pivots. Unset in development; set it
in production.

### Error handling

All errors return a uniform envelope:

```json
{
  "success": false,
  "error": { "code": "...", "message": "...", "request_id": "..." }
}
```

- `debug=False` on the app, so FastAPI never returns a traceback.
- Unhandled exceptions are logged server-side with redaction and returned to
  the client as a generic `INTERNAL_ERROR` — no stack trace, no SQL, no file
  path.
- Validation errors disclose field names only.
- Ownership failures use the neutral message described in §3, preventing order
  enumeration.
- Every response carries `request_id` for correlation.

### Documentation exposure

`DISABLE_DOCS=true` removes `/docs`, `/redoc` **and** `/openapi.json` — all
three return `404`. Verified: with docs enabled all three return `200`. The
OpenAPI schema is the most complete description of the attack surface, so
disabling it in production is recommended. `/health` continues to work, and its
payload reports `docs: null` rather than advertising the path.

---

## 13. Known limitations

These are accepted, not fixed. Each is a deliberate decision for this phase.

1. **Shared API key — no individual customer identity.**
   A single `APP_API_KEY` authenticates the *deployment*, not the *customer*.
   Any holder of the key can submit any `customer_id` and will be authorised
   for that customer's orders, account details and conversation history. The
   ownership check in §3 is real and does prevent accidental cross-customer
   access, but it compares against a caller-supplied claim, so it cannot detect
   a caller who lies about who they are. **Closing this requires per-customer
   credentials (OIDC/OAuth2, mTLS or signed client certificates) and is out of
   scope for this project by design.** No placeholder token system has been
   implemented, because a fake JWT would create a false impression of security.

2. **In-memory rate limiting.** Counters live in process memory. They do not
   coordinate across workers, are lost on restart, and are bypassable by
   rotating source IPs. Move to Redis or an API gateway for multi-instance use.

3. **TLS termination is external.** The service speaks plain HTTP and relies on
   a reverse proxy for TLS. HSTS is inert without that proxy. Without TLS, the
   API key and all customer data travel in clear text.

4. **Guardrails are heuristic.** Injection and jailbreak detection is
   pattern-based. Novel phrasings, non-English payloads, encoded or
   multi-turn-split attacks can evade it. The structural controls in §4 are the
   real defence; guardrails are defence in depth.

5. **LLM faithfulness check is heuristic.** The LLM may only rephrase verified
   facts, and its output is discarded if it references anything absent from the
   verified set. This catches most drift but is a string-level check, not a
   semantic guarantee.

6. **`/docs` is enabled by default.** `DISABLE_DOCS` must be set explicitly in
   production. There is no "production implies locked" default.

7. **No SIEM or alerting.** Security events are structured and written to
   standard logging, but nothing pages anyone. Repeated auth failures and
   ownership denials are logged at `WARNING`/`HIGH` and would need shipping to
   a SIEM or log-based alert to be actionable.

8. **Audit log retention is unbounded in code.** `AgentLog` rows accumulate;
   no retention or pruning policy is implemented.

9. **No per-customer rate limiting.** Limits are per IP, so one noisy customer
   can exhaust the budget of everyone behind the same address.

10. **`support.db` is a single SQLite file.** It is not reachable through the
    API — no static mounts exist and all file paths return `404` — but the file
    itself is unencrypted. Protect it at the filesystem level.

11. **No dependency or container scanning** is configured in the repository.

---

## 14. Production checklist

- [ ] Set a strong, unique `APP_API_KEY` (the default must not be used)
- [ ] Set `DISABLE_DOCS=true`
- [ ] Set `ENVIRONMENT=production` and `DEBUG=false`
- [ ] Set `ALLOWED_HOSTS` to the real hostnames
- [ ] Restrict `CORS_ALLOWED_ORIGINS` to real frontends (no wildcards)
- [ ] Terminate TLS at a reverse proxy; verify HSTS is effective
- [ ] Start the server with `--no-server-header --no-date-header` to suppress
      version disclosure (the in-app delete cannot do this)
- [ ] Set `DATABASE_URL` to a managed, encrypted database
- [ ] Rotate `GEMINI_API_KEY` / `GROQ_API_KEY`, or leave them unset and stay
      on the deterministic path
- [ ] Configure `SECURITY_LOG_LEVEL` and ship logs to a SIEM
- [ ] Confirm `.env` and `*.db` remain git-ignored
- [ ] Move rate limiting to shared storage if running more than one worker
- [ ] Accept and communicate limitation §1 to anyone relying on this service

---

## 15. Verification

Phase 4.5 validation covers: fail-closed ownership, cross-customer denial for
every order-scoped tool, identity-versus-authorization behaviour, `DISABLE_DOCS`
on and off, credential and PII redaction, database and `.env`
non-exposure, mass assignment rejection, security headers, CORS behaviour,
safe error bodies and this document's required content.

Regression suites for Phases 1–4 remain green and must continue to pass:
intent/entity/guardrail validation, tool selection and execution, response
building and escalation, memory and logging, the optional LLM layer, and the
Phase 4 API security suite.