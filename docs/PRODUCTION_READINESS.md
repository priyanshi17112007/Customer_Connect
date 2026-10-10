# ResolveAI — production readiness audit (Phase 6)

Audit date: 2026-10-04. Scope: `backend/`, `frontend/`, `docs/`,
`requirements.txt`, `package.json`, `.env.example`, `.gitignore`. Phase 6.1
added a live end-to-end pass (§2.17–§2.18) that found two defects no offline
suite had detected.

This document records what was audited, what was classified as a defect, what
was changed, and — more importantly — what is still **not** production ready.
Passing tests are not evidence of production readiness on their own; see
§"Readiness matrix" and §"Known limitations".

---

## 1. How to read this document

Findings are classified as:

| Class | Meaning |
| --- | --- |
| `CRITICAL` / `HIGH` | Exploitable or breaks legitimate production traffic. Fixed in Phase 6. |
| `MEDIUM` / `LOW` | Real defect with limited impact. Fixed, or deliberately deferred with a reason. |
| `PRODUCTION_CONFIGURATION` | Correct code, incorrect or missing operational configuration. |
| `KNOWN_LIMITATION` | An architectural property. Not a bug; documented, not "fixed". |
| `NO_ISSUE` | Explicitly investigated and verified sound. Recorded so it is not re-litigated. |

---

## 2. Defects found and fixed in Phase 6

### 2.1 HIGH — Jailbreak pattern matched ordinary words (`backend/app/security/guardrails.py`)

`JAILBREAK_PATTERNS` contained `r"DAN"`, compiled with `re.IGNORECASE` and
matched with `pattern.search()`. With no anchors this is a substring match, so
**any** message containing the letters `dan` was refused as a `CRITICAL`
jailbreak attempt.

Reproduced against the running service before the fix:

```
[BLOCKED] My delivery was abundant and late          error=BLOCKED_BY_GUARDRAIL
[BLOCKED] Dan asked me to check the order status     error=BLOCKED_BY_GUARDRAIL
[     ok] This vendor is my preferred supplier        error=None
[     ok] I am a candidate for a bulk order discount  error=None
```

This silently destroyed legitimate traffic, and no test covered it because the
existing suites only asserted that attacks *were* blocked.

**Fix.** The persona requires an activation instruction to have any effect, so
the pattern is now anchored on one. A bare given name is no longer an attack,
while every real attack form still matches:

```python
r"(?:\byou\s+are\b|\bact(?:ing)?\s+as\b|\bbecome\b|\bpretend\b|\bimagine\b"
r"|\benter\b|\bmode\b|\bprompt\b|\bjailbreak\b|\boverride\b)"
r"[^\n]{0,32}\bDAN\b"
r"|\bDAN\b[^\n]{0,32}\b(?:mode|prompt|jailbreak|ignore|instructions?|"
r"restrictions?|guidelines?)\b"
```

The other 13 jailbreak patterns are unchanged. No control was removed.

### 2.2 HIGH — Ownership check could fail open (`backend/app/agents/support_agent.py`)

`enforce_ownership()` ended with:

```python
if order_owner and order_owner.upper() != state.customer_id.upper():
    return self._access_denied(...)
return observation
```

The guard only compared when `order_owner` was **truthy**. An order row with a
`NULL`/missing owner, or a tool payload that did not carry a `customer_id` key,
skipped the comparison entirely and the data was released — contradicting the
function's own docstring, which states it fails closed.

**Fix.** An absent owner is not a match:

```python
if not order_owner or order_owner.upper() != state.customer_id.upper():
    return self._access_denied(observation, "order belongs to another customer")
```

The legitimate path is unaffected: a matching owner (including a
case-insensitive match) is still allowed, and chained tools still consult the
verified `get_order` owner.

### 2.3 HIGH — Duplicated order-scoped tool list (`backend/app/agents/support_agent.py`)

`ORDER_SCOPED_TOOLS` was declared twice: once at module level (used by
`enforce_ownership`) and again as `ToolSelector.ORDER_SCOPED_TOOLS`. The sets
were identical, but any future order-scoped tool added to only one of them would
either bypass ownership enforcement or be denied outright.

**Fix.** `ToolSelector.ORDER_SCOPED_TOOLS = ORDER_SCOPED_TOOLS` — one source of
truth, verified by identity (`is`) in the regression suite.

### 2.4 HIGH — Default API key accepted silently in production (`backend/app/security/auth.py`)

`USING_DEFAULT_API_KEY` was computed and then never read. The service started
with the publicly known `dev-api-key-change-in-production` value and issued no
warning.

**Fix.** The default is still tolerated for local development (so the project
runs out of the box) and now always logs a warning; when
`ENVIRONMENT=production` the application refuses to start:

```
RuntimeError: APP_API_KEY is still the default development value while
ENVIRONMENT=production. Set a strong, unique APP_API_KEY before starting the
service in production.
```

### 2.5 MEDIUM — Raw exception text written to tool logs (`backend/app/tools/support_tools.py`)

Seven handlers logged `f"...: {e}"` with the raw exception. SQLAlchemy and
psycopg exception text routinely contains bound parameters, column values and
connection strings. The redaction path used everywhere else in the application
was bypassed.

**Fix.** All seven now pass through `redact()` from `app.security.redaction`.

### 2.6 MEDIUM — Direct-contact PII sent to third-party LLM providers (`backend/app/agents/support_agent.py`)

`polish_response()` passed `verified_facts()` — including the `get_customer`
result (`name`, `email`, `phone`, `address`) — to Google Gemini or Groq. No
redaction was applied to the outbound payload.

**Fix.** New `llm_safe_facts()` strips direct-contact keys before the outbound
call and the customer's message is redacted. `verified_facts()` is unchanged, so
server-side logging keeps its full fidelity. The reply templates are built from
order/delivery/payment/refund fields only, so response quality is unaffected.
This path is inert unless `AGENT_ENABLE_LLM=true`.

### 2.7 MEDIUM — Timezone-naive arithmetic on a timezone-aware column (`backend/app/tools/support_tools.py`)

`check_return_eligibility()` computed `datetime.now() - delivery.actual_delivery`
while `Delivery.actual_delivery` is `DateTime(timezone=True)`. SQLite returns
naive values so this works today; on any timezone-aware backend it raises
`TypeError`, which the blanket handler converts into `DATABASE_ERROR` — return
eligibility would break silently. `datetime.now()` was also server-local rather
than UTC.

**Fix.** Both sides are normalised to naive UTC before subtracting. This is a
migration-readiness fix, not a behaviour change.

### 2.8 MEDIUM — Wrong environment variable name in `.env.example`

`.env.example` documented `ALLOWED_ORIGINS`, but the code reads
`CORS_ALLOWED_ORIGINS` (`backend/app/security/auth.py`). Anyone configuring CORS
from the documented file got no effect and silently kept the built-in default.

It also declared six variables the code never reads (`SECRET_KEY`, `ALGORITHM`,
`ACCESS_TOKEN_EXPIRE_MINUTES`, `RATE_LIMIT_REQUESTS`, `RATE_LIMIT_WINDOW`,
`ALLOWED_ORIGINS`) — the first three implying a JWT system that deliberately does
not exist — and omitted seven the code does read (`CORS_ALLOWED_ORIGINS`,
`DISABLE_DOCS`, `ALLOWED_HOSTS`, `SECURITY_LOG_LEVEL`, `AGENT_ENABLE_LLM`,
`LLM_TIMEOUT_SECONDS`, `GEMINI_API_KEY`).

**Fix.** `.env.example` was rewritten to match the implementation exactly,
grouped and annotated. The local gitignored `backend/.env` had the same stale
key and was corrected in place.

### 2.9 MEDIUM — `requirements.txt` did not describe the tested environment

The file pinned `fastapi==0.109.0`, `pydantic==2.5.3`, `sqlalchemy==2.0.25`.
The versions actually installed and validated against were `fastapi 0.138.1`,
`pydantic 2.13.4`, `sqlalchemy 2.1.3`. Installing from the old file would have
produced an environment the project had never been tested in.

It also listed eleven packages that were neither imported nor installed
(`alembic`, `pydantic-settings`, `python-multipart`, `langchain-community`,
`langgraph`, `python-jose`, `passlib`, `bcrypt`, `slowapi`, `aiofiles`, and the
`pytest` trio), plus `httpx` twice.

**Fix.** The file now records the versions that were actually exercised, lists
only what is imported or needed to run the server, and documents what was
removed. No package was upgraded or downgraded.

### 2.10 MEDIUM — A wrong API key was reported as a backend outage (`frontend/src/App.jsx`)

```js
if ([401, 403, 502, 503, 504].includes(err.status)) setOnline(false)
```

The backend answers `403 INVALID_API_KEY` for a bad key. A credential error
therefore flipped five independent indicators to "OFFLINE"/"AGENT UNREACHABLE"
and rendered the "BACKEND CONNECTION LOST" panel, even though the service had
just responded.

**Fix.** Only `502/503/504` (and a genuinely absent health check) mark the
backend unreachable.

### 2.11 MEDIUM — Health re-check erased the only error message (`frontend/src/App.jsx`)

`checkHealth()` called `setError(null)` on success. Clicking **RETRY CONNECTION**
hit the public `/health` endpoint, succeeded, and deleted the credential error
that explained what was wrong.

**Fix.** Only a stale connectivity error (`NETWORK_ERROR`) is cleared; a chat
error survives a health re-check.

### 2.12 MEDIUM — Timeline allow-list resolved `Object.prototype` (`frontend/src/services/activity.js`)

The allow-lists are plain object literals, so `TOOL_LABELS[raw] ?? null` also
resolved inherited members. `?? null` cannot catch this because
`TOOL_LABELS['constructor']` is a **truthy function**, which React rejects as a
child — blanking the screen. Demonstrated:

```
old lookup TOOLS["constructor"] -> function TRUTHY (would be rendered as a label)
would ?? null catch it? false
fixed lookup -> null
```

Reachable only if the backend emitted such a value, but it is precisely the
boundary the whole safety argument rests on.

**Fix.** All lookups go through a `listed()` helper using `Object.hasOwn()`.

### 2.13 MEDIUM — No error boundary (`frontend/src/main.jsx`)

Any exception during render unmounted the entire tree: no header, no transcript,
no composer, blank screen. Item 2.12 was a concrete trigger.

**Fix.** `frontend/src/components/ErrorBoundary.jsx` wraps the app and renders a
safe, non-diagnostic recovery panel. Exception text goes to the browser console
only, never to the DOM.

### 2.14 MEDIUM — API key was serialised into the DOM (`frontend/src/components/CustomerContext.jsx`)

The key field was a *controlled* React input. React mirrors a controlled value
onto the element's `value` **content attribute** (`react-dom` `updateWrapper` →
`setDefaultValue`, which runs whenever `props.hasOwnProperty('value')`). Passing
`defaultValue` does **not** prevent this; only removing the `value` prop does.

Consequence: `document.body.innerHTML`, and anything that serialises the DOM
(crash reporter, session-replay tool, extension), contained the plaintext key.
It was never in storage, never visible, never logged, and never sent anywhere
but the API — but it was in the DOM.

**Fix.** The field is now uncontrolled. The key never enters React state and is
read from a ref only at submit time; the parent is told only *whether* a key is
present. Verified in-browser:

```
{"type": "password", "attrHits": [], "contentAttribute": "", "liveValueIsInput": true}
```

### 2.15 MEDIUM — The mobile context collapse hid its own toggle (`frontend/src/styles/shell.css`)

`ctx-toggle` lives inside `.app__sidebar`, and collapsing applied
`display: none` to the sidebar. On a phone, tapping **HIDE CONTEXT** removed the
customer-ID and API-key fields *and the only control that could bring them
back*, with no way back short of a full page reload.

**Fix.** Collapse now targets `.sidebar__panels`, leaving the toggle visible.
Verified: collapse and re-expand both work at 390px with no overflow.

### 2.16 LOW — Dead `coreDetail` prop (`frontend/src/App.jsx`)

`ChatWindow` forwards `coreDetail` to `ProcessingState`, but `App` never passed
it, so the transcript-level status line could never render. Now supplied.

### 2.17 HIGH — Rate limiter registered twice, halving the effective limit (found in Phase 6.1 live validation)

`register_security_middleware()` adds its `auth_middleware_factory` argument and
then adds `RateLimitMiddleware` itself. `create_app()` was passing
`RateLimitMiddleware` as that first argument — a slot left over from an
`AuthMiddleware` that no longer exists, since authentication is a route
dependency. The limiter therefore ran **twice per request** and each request
consumed two units of the budget:

```
before:  limit=20  remaining=19,17,15,13,11,9,7,5,3,1   -> 429 on request #11
after:   limit=20  remaining=19,18,17,16,15,14,13,12,11,10,9,8,7,6,5,4,3,2,1,0 -> 429 on #21
```

Legitimate clients were throttled at 10 requests/60 s instead of the documented
20, and `X-RateLimit-Remaining` misreported the budget. No test caught it: the
committed suite does not exercise rate limiting, and the regression suites drive
the limiter in-process, where a duplicate registration is invisible.

**Fix.** The factory argument is now `Optional` and applied only when supplied;
`create_app()` calls `register_security_middleware(app)`. The limiter is
registered exactly once and the middleware order is unchanged.

### 2.18 MEDIUM — Brute-force 429 omitted `Retry-After` (found in Phase 6.1 live validation)

The auth-failure throttle returned its retry hint only inside the JSON body
(`error.retry_after`), because `_error()` places `**extra` in the response
content. Unlike the request rate limiter, it never set the HTTP header, so a
throttled credential guess gave the client no machine-readable wait hint and the
frontend — which reads `Retry-After` — could not see it. `SECURITY.md` §8 states
that throttled responses return `Retry-After`.

**Fix.** The 429 now sets `Retry-After: 300`, matching the request limiter. The
body field is unchanged, so the error envelope contract is unaffected.

---

## 3. Verified sound (no change made)

| Area | Evidence |
| --- | --- |
| Constant-time key comparison | `hmac.compare_digest` in `auth.py` |
| Generic auth errors | 401/403 messages are fixed literals; neither echoes the submitted or expected key |
| Brute-force throttle | 10 failures / 300s per IP → `429` with `Retry-After` |
| IDOR | cross-customer order → `ORDER_NOT_ACCESSIBLE`, no tracking number, no amount leaked |
| Conversation isolation | another customer's `conversation_id` yields no data |
| Guardrails before tools/LLM | injection and jailbreak checks run before intent, tools and the LLM; blocked requests execute no tool |
| LLM non-authoritative | deterministic `ResponseBuilder` output stays authoritative; `is_faithful` rejects unverified claims; timeout, provider failure and malformed output all fall back |
| Response redaction | no tracking id, amount or internal message in any error path |
| Structured errors | every public error is `{success:false,error:{code,message,request_id}}` |
| Body size / content type | >16384 B → `413`; non-JSON → `415`; over-long field → `422` |
| Rate limiting | `429` + `Retry-After`, CORS-decorated, no internals leaked |
| CORS | explicit allow-list, `*` rejected, `allow_credentials=False`, `X-Request-ID`/`Retry-After` exposed, disallowed origins not reflected, outermost so 401/403/413/429 are readable by a browser |
| Security headers | `nosniff`, `DENY`, CSP, Referrer-Policy, Permissions-Policy, HSTS present |
| SQLAlchemy `expire_on_commit=False` | no detached-instance reads after commit |
| Session lifecycle | every tool closes in `finally`; no leaked sessions |
| No bare `except:` | every handler names its exception type |
| DB abstraction | SQLAlchemy throughout; `DATABASE_URL` is the only backend-specific setting |
| Frontend credentials | no `localStorage` / `sessionStorage` / cookies / IndexedDB; no console logging; key never in a URL |
| Timeline safety | labels allow-listed, `details` never read, unknown events generic, no stage invented, per-request isolation |
| Responsive UI | no horizontal overflow at 320–1920px |

---

## 4. Known limitations (architectural, not defects)

1. **Shared API key — no customer identity.** `X-API-Key` authenticates the
   *deployment*. `customer_id` is a caller-supplied claim, verified against
   order-ownership rows but not against an authenticated principal. Anyone
   holding the key can act as any customer id. Resolving this requires a real
   identity system (OIDC/JWT or per-customer tokens) — deliberately out of
   scope, and not something to fake.
2. **SQLite.** Correct for the controlled demo. The code is behind SQLAlchemy
   and reads `DATABASE_URL`, so PostgreSQL is a configuration change plus
   Alembic migrations and a re-test of the timezone-sensitive queries. Not done
   here: it would be a migration, not a stabilisation.
3. **In-memory rate limiting and counters.** Correct for a single instance.
   Multiple workers or replicas each keep their own counters, so the effective
   limit multiplies by the worker count. Distributed deployment requires shared
   storage (Redis or a gateway-level limiter). Redis was not introduced: it is
   not needed for the current deployment shape.
4. **TLS.** The application does not terminate TLS. `Strict-Transport-Security`
   is only honoured over HTTPS, so a reverse proxy must provide it. The
   development Uvicorn server is **not** TLS termination and must not be exposed
   directly. Start with `--no-server-header --no-date-header`.
5. **No request cancellation.** The API client supports an external
   `AbortSignal`, but the app does not pass one, so an in-flight request is not
   cancelled on unmount. The 30s timeout still bounds it.
6. **No CI, no linting, partial test suite.** A committed suite exists at
   `backend/tests` (27 `unittest` tests: 21 API contract, 6 compile/import),
   run from `backend/` with `python -m unittest discover -s tests -t .`
   (`-t .` is required: the test modules use package-relative imports).
   There is still no `pytest`
   or ESLint configuration and no CI; the wider verification in this project
   was run as focused external scripts that are not part of the repository.
7. **Verified facts include a structural risk.** If a tool ever nested its
   payload under a `result` key, `enforce_ownership` would no longer find the
   owner. The fail-closed fix makes that deny rather than release.
8. **Delivery/tracking extraction is best-effort.** Pattern-based extraction from
   `courier`/`tracking_number` free text. Since the deterministic reply is
   built only from verified tool fields, a missed extraction under-reports
   rather than invents.
9. **LLM enhancement is optional and off by default.** With `AGENT_ENABLE_LLM`
   off the agent is fully deterministic. When on, the model can rephrase only.
10. **`tool_calls[]` still ships internal tool names** to the browser. They are
    never rendered (the frontend ignores the field), but the data is in the
    response payload. Changing the API contract was out of scope.

---

## 5. Production checklist

- [ ] Set a unique, high-entropy `APP_API_KEY`. The app refuses to start in
      production without it.
- [ ] Set `ENVIRONMENT=production`.
- [ ] Set `DISABLE_DOCS=true` unless internal API documentation is required.
- [ ] Set `CORS_ALLOWED_ORIGINS` to the exact frontend origins. Wildcards are
      rejected by design.
- [ ] Set `DEBUG=false` and `SECURITY_LOG_LEVEL=INFO` or higher.
- [ ] Set `DATABASE_URL` to PostgreSQL and introduce Alembic migrations.
- [ ] Terminate TLS at a reverse proxy; run the app with
      `--no-server-header --no-date-header`.
- [ ] Replace the in-memory limiter with shared storage before scaling past one
      instance.
- [ ] Keep `AGENT_ENABLE_LLM=false` unless a DPA covers the chosen provider.
- [ ] Re-run the audit suites against the production configuration.

---

## 6. Readiness matrix

Status vocabulary: `READY`, `READY WITH CONFIGURATION`, `NEEDS WORK`,
`KNOWN LIMITATION`.

| Category | Status | Evidence | Remaining work |
| --- | --- | --- | --- |
| 1. Backend | READY WITH CONFIGURATION | 46/46 live checks, 470/470 backend regressions, compile+import clean | Run under a real process supervisor |
| 2. Agent orchestration | READY | deterministic loop, tool allow-list, iteration cap, escalation | none for single-node use |
| 3. Tools | READY | registered-tool allow-list, per-tool timeout, fail-closed ownership, all sessions closed | verify thread-pool reuse under sustained load |
| 4. Database | READY WITH CONFIGURATION | SQLAlchemy-only, `expire_on_commit=False`, indexes on FK/status columns, `DATABASE_URL` switch | PostgreSQL + Alembic; re-test tz-sensitive queries |
| 5. Authentication | KNOWN LIMITATION | constant-time compare, generic errors, brute-force throttle, production key enforced | per-customer identity (OIDC/JWT) — not implemented by design |
| 6. Authorization | READY | IDOR blocked and fail-closed; conversation isolation verified | depends on #5 for true caller identity |
| 7. Security | READY WITH CONFIGURATION | headers, CORS outermost, redaction, no default key in production | TLS termination, doc suppression, shared rate-limit store |
| 8. Prompt injection protection | READY | guardrails precede tools and LLM; real jailbreaks blocked; benign traffic no longer false-positives | periodic pattern tuning against new attack forms |
| 9. LLM reliability | READY | timeout, provider fallback, malformed output → deterministic reply | none when disabled (default) |
| 10. Hallucination protection | READY | verified-facts-only builder, `is_faithful` backstop, model cannot change intent/plan/outcome | bare numbers not pattern-checked |
| 11. Memory | READY | per-conversation rows, cross-customer read rejected | conversation rows are never expired |
| 12. Logging | READY | credentials, PII and raw exceptions redacted; no request bodies logged | ship to a real log sink |
| 13. Frontend | READY WITH CONFIGURATION | 47/47 timeline suite, 18/18 Phase 6 UI checks, no overflow 320–1920 | set `VITE_API_BASE_URL`; production hosting/headers |
| 14. Error handling | READY | structured envelope everywhere, no internals leaked, safe status mapping | `Retry-After` captured but not surfaced in the UI |
| 15. Testing | NEEDS WORK | 27 committed tests (`backend/tests`, `python -m unittest discover -s tests -t .`) + 470 backend regression checks; live and UI figures in rows 1 and 13 | no CI, no linting, no coverage gate |
| 16. Deployment | NEEDS WORK | startup flags, health endpoint, production env guards | no supervisor/reverse-proxy/systemd unit or Dockerfile |
| 17. Monitoring | NEEDS WORK | structured security events + `X-Request-ID` | no metrics, tracing or alerting |
| 18. Scalability | KNOWN LIMITATION | single-node design | shared limiter, PostgreSQL, stateless workers |
| 19. Documentation | READY WITH CONFIGURATION | `SECURITY.md`, this document, corrected `.env.example` | README/operations runbook |
| 20. Enterprise integration | KNOWN LIMITATION | none claimed or faked | SSO, per-tenant keys, audit export, DPA for LLM |

---

## 7. Honest bottom line

The **code** is in good shape: the security boundaries are real, fail-closed,
and now regression-tested, and the audit found no remaining exploitable defect.

The **deployment** is not production ready. A production claim would require at
minimum: a unique `APP_API_KEY`, `ENVIRONMENT=production`, TLS at a proxy,
`DISABLE_DOCS=true`, shared rate-limit storage, PostgreSQL with migrations, and
a real identity system. Items 5 and 20 above are architectural and were not
addressed here because inventing a token system or an SSO integration would
create the appearance of enterprise readiness without the substance.

Test results alone justify none of this. They show the audited behaviours hold
under test, not that the system is safe for untrusted internet traffic.