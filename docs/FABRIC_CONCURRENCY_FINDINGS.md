# Fabric Data Agent — Technical Findings & Load Test Summary

---

## Architecture: How It Works

```
SPA (MSAL token) → Python API (OBO exchange) → Foundry Responses API → Fabric Data Agent tool
                                                                         ↑ user's OID forwarded
```

The OBO (On-Behalf-Of) flow is **required** — Fabric enforces Row-Level Security using the end-user's Entra Object ID (OID). There is no SDK shortcut that preserves this; raw HTTP with the OBO token is the correct implementation.

---

## Core Constraint: Fabric Same-User Concurrency

Fabric enforces **one active run per user identity (OID)** at its execution layer. This is not configurable.

| Scenario | Behavior |
|---|---|
| User A and User B send simultaneous requests | ✓ Fully parallel — different OIDs |
| User A sends 2 requests simultaneously (2 browsers, Teams + web, dashboard fan-out) | ⚠ Fabric serializes — same OID |
| New tokens minted for the same user | ⚠ Still same OID — no effect on concurrency |

---

## Why the `fabric_data_agent_client` Sample Doesn't Apply

| Dimension | `fabric_data_agent_client` (GitHub sample) | Our Architecture (Foundry → Fabric Tool via OBO) |
|---|---|---|
| Call path | `Client → Fabric /openai/beta/threads` directly | `SPA → API (OBO) → Foundry /openai/responses → [Foundry invokes Fabric tool internally]` |
| Authentication | `InteractiveBrowserCredential` scoped to `api.fabric.microsoft.com` — gets a Fabric-native token | MSAL token → OBO exchange → token scoped to Foundry API — Foundry forwards user identity to Fabric |
| Thread control | App creates and names threads explicitly. New thread = new execution slot = concurrency works | Foundry owns the thread lifecycle. App creates a Foundry *conversation*, Foundry maps it to a Fabric thread internally |
| Can we use it here? | **No.** Our token is scoped to Foundry (`ai.azure.com`), not Fabric directly. Also bypasses the Foundry orchestration layer entirely (NL2SQL, agent instructions, tool routing all live there) | ✓ This is our architecture |
| Concurrency — different users | ✓ Natural — each user has their own token and thread | ✓ Natural — each user's OBO token = distinct Fabric identity = fully parallel, no contention |
| Concurrency — same user, simultaneous requests | ✓ Solved by creating a new named thread per request in code | ⚠ Foundry creates a new conversation → new Fabric thread per request, so it *should* work. But Fabric enforces **one active run per user identity** — so two simultaneous requests from `user@corp.com` still serialize |
| Real-world same-user concurrency | N/A (app manages it) | **Not a real-world problem.** In the Chat UI, the user waits for a reply before asking again — requests are naturally sequential. No real user fires 5 simultaneous questions |
| Real-world different-user concurrency | Works | ✓ Works perfectly. 50 users all have different OBO tokens → 50 distinct Fabric identities → fully parallel |
| OBO / RLS passthrough | ✗ Not supported — service principal auth only if used server-side; interactive browser only if client-side | ✓ Core strength — Fabric sees the real end-user identity, RLS enforced correctly per user |
| Load test with 1 token | Would also fail same-user concurrency | ⚠ Artificial problem — one token = one identity = serialized. Solved in the UI load test by pre-acquiring a token per test user |

---

## OBO vs Service Principal Trade-off

| | OBO (current) | Service Principal |
|---|---|---|
| Fabric RLS | ✓ Native — Fabric enforces per-user automatically | ✗ All queries run as service account — RLS blind |
| Same-user concurrency | ⚠ Serialized by Fabric per OID | ✓ No per-user limit |
| App-level RLS fallback | Not needed | ⚠ Must be implemented and maintained manually — security risk if missed |
| Verdict | Correct for this use case | Only viable if RLS is fully re-implemented in application layer |

Fabric enforces **one active run per user identity** at its layer. Since RLS *requires* user identity passthrough, these two requirements are in direct tension. There's no Foundry-level trick that resolves it — new conversations create new threads, but Fabric still sees the same user and serializes.

---

## Real-World Same-User Concurrency Scenarios

These all map to the same Fabric OID and therefore serialize:

| Scenario | Same Fabric Identity? |
|---|---|
| User opens app in Chrome + Firefox simultaneously | ✓ Same OBO token identity |
| User asks on web app, also has Teams chat open and asks there | ✓ Same OBO identity (our bot does OBO too) |
| Progressive Web App on phone + browser tab open | ✓ Same identity |
| Dashboard page that fans out 3 questions on load | ✓ Same identity |
| Auto-refresh / polling pattern in a real product | ✓ Same identity |

> **The correct framing: this is not a scale problem, it is a per-user concurrency problem.** It will surface in production the moment any individual user has the app open in more than one place at once, or triggers more than one request before the first completes. It affects every user, regardless of total user count.

| Scenario | Production concern? |
|---|---|
| 500 users each sending 1 request at a time | ✓ No issue — 500 distinct OIDs, fully parallel |
| 1 user with 2 browser tabs open, both active | ⚠ Real production issue — same OID, Fabric serializes |
| 1 user on Teams + web app simultaneously | ⚠ Real production issue — same OID |
| Dashboard that fans out N questions on page load | ⚠ Real production issue — same OID, N-1 fail or queue |
| Mobile + desktop active simultaneously | ⚠ Real production issue — same OID |

---

## Mitigation Options at the Application Layer

1. **Request queuing per user** — at the API level, if `user X` already has an in-flight Foundry call, queue the next request rather than firing it concurrently. Users get sequential responses, not errors.

2. **Optimistic UI with a queue indicator** — on the SPA side, if a user fires a second question while the first is in-flight, buffer it and send it only after the first `conversationId` comes back.

3. **Accept the constraint and document it** — for most enterprise chat scenarios (one screen, one question at a time), this never manifests. The Teams + Web simultaneous case is rare in practice.

4. **MCP Direct Path (Theory Test — implemented)** — `Browser → API → Foundry → MCP Tool → Fabric (new thread per request)`.  See details below.

---

## Theory 4 — MCP Direct Path (Implemented in /api/agent/v2)

### Hypothesis

The `fabric_data_agent_client` sample (GitHub) claims:
> *"New thread = new execution slot = concurrency works"*

If Fabric's constraint is **per-thread** (not globally per-OID), then bypassing Foundry's thread lifecycle and creating an explicit new Fabric thread for every request should allow multiple simultaneous requests from the same user to run in parallel.

### Architecture

```
Current (v1):
  Browser → API (OBO → ai.azure.com) → Foundry → built-in Fabric tool
            → Foundry manages thread lifecycle → Fabric (serialised per OID?)

Theory (v2) — implemented:
  Browser → API (OBO → ai.azure.com AND api.fabric.microsoft.com)
          → Foundry (inline MCP tool, no built-in Fabric tool)
          → /mcp endpoint (this API, fabric_mcp_server.py)
          → Fabric Assistants API (NEW uuid-named thread per request)
          → Fabric Data Agent (RLS enforced, same OBO identity)
```

### Key Differences

| Dimension | v1 (Current) | v2 (MCP Theory) |
|---|---|---|
| Foundry tool type | `fabric_dataagent_preview` (built-in) | `mcp` (custom MCP server) |
| Thread lifecycle | Foundry manages conversation → Fabric thread mapping | API creates explicit new thread per request |
| Thread reuse | Possibly per conversation or per user | Never reused — fresh `uuid4()` name each call |
| OBO scopes | `ai.azure.com` only | `ai.azure.com` (Foundry) + `api.fabric.microsoft.com` (Fabric direct) |
| Fabric token in model context | No | No — stored in TokenSessionStore, only opaque session_key in context |
| Concurrency if per-thread constraint | Serialised (same thread?) | Parallel (different thread per request) |
| Concurrency if per-OID global constraint | Serialised | Still serialised |

### What This Actually Tests

> **The open question:** does Fabric enforce *"one active run per thread"* (per-thread serialisation) or *"one active run per OID globally"* (global per-OID serialisation)?

- If **per-thread**: The MCP path should resolve same-user concurrency at the cost of no conversation context reuse across requests.
- If **per-OID global**: Neither approach helps without moving to service-principal auth (which breaks RLS).

The error message `"Can't add messages to thread_... while a run ... is active"` reads as per-thread — it names a specific thread. The `fabric_data_agent_client` README supports this interpretation. But unambiguous documentation from Microsoft does not exist.

### Implementation Files

| File | Purpose |
|---|---|
| `pythonapi/token_session_store.py` | In-process TTL cache; stores Fabric OBO token keyed by a short-lived UUID session_key |
| `pythonapi/fabric_mcp_server.py` | MCP server; exposes `query_fabric` tool; creates new Fabric thread per call |
| `pythonapi/mcp_foundry_service.py` | Foundry Responses API wrapper using inline MCP tool instead of built-in Fabric tool |
| `pythonapi/main.py` `/api/agent/v2` | Theory-test endpoint; does dual OBO, stores token, calls McpFoundryAgentService |
| `pythonapi/main.py` `/mcp` | MCP server mounted here; Foundry calls back to this URL |
| `pythonapi/auth.py` `exchange_token_for_fabric` | Second OBO exchange producing `api.fabric.microsoft.com`-scoped token |
| `pythonapi/config.py` `FabricDirectSettings` | `FABRIC_DIRECT_DATA_AGENT_URL` + `FABRIC_DIRECT_API_PUBLIC_URL` |

### Required New Environment Variables

```env
# URL of your published Fabric Data Agent (same URL as in fabric_data_agent_client)
FABRIC_DIRECT_DATA_AGENT_URL=https://{host}/aiskills/{workspace}/aiassistant/{agent}/openai

# Public root URL of this API so Foundry can call back to /mcp
# Dev: your devtunnel URL (same tunnel already used for Teams bot testing)
# Prod: your deployed API URL
FABRIC_DIRECT_API_PUBLIC_URL=https://your-tunnel-id.devtunnels.ms
```

### Testing the Theory

1. Set the two new env vars and restart the API.
2. Use the existing load-test UI — point it at `/api/agent/v2` instead of `/api/agent`.
3. Run with multiple concurrent users sharing the same Entra account.
4. Compare success rate and serialisation errors vs. the v1 path.

### OBO Consent Note

The API app registration must have **delegated permission** for `api.fabric.microsoft.com/user_impersonation` (or equivalent Fabric scope) for the second OBO to succeed. If it fails with `interaction_required`, grant admin consent for the Fabric scope in the app registration.

---

## Load Test Results

**Setup:** Single user (`fabricusera@...`), 5 concurrent, 10 total requests, Single User mode

| Metric | Value |
|---|---|
| Success rate | 30% (3/10) |
| Failures | 7 — all same-user Fabric serialization timeouts |
| Avg latency (successes) | 32,328ms |
| p95 / p99 | 56,238ms |

**Root cause confirmed:** 5 concurrent slots → same OID → Fabric serializes → ~4 requests per wave timeout.

---

## Concurrency Capacity Rule

> True parallel Fabric runs = number of distinct Entra accounts, not wave size.

| Test accounts provisioned | Max safe wave size | Production equivalent |
|---|---|---|
| 1 | 1 | 1 concurrent user |
| 3 | 3 | 3 concurrent users |
| N | N | N concurrent users |

---

## Recommendations

| Priority | Action |
|---|---|
| **Load testing** | Provision N Entra test accounts with Fabric access; use Multi-User mode in the load test UI — one account per concurrent slot |
| **Multi-surface users** (Teams + web + mobile simultaneously) | Accept serialization or implement UI-side request queuing — if user already has in-flight request, hold the next one client-side until response returns. Implement client-side in-flight guard — if a request is already in flight for this user, either disable the input or queue the next request until the first response returns. This is the only reliable mitigation short of moving off OBO. |
| **Service Principal path** | Only reconsider if product accepts moving RLS enforcement out of Fabric and into the application layer — significant security design work |

---

## Citations

### Citation 1 — Identity passthrough is OBO, service principal not supported

**Source:** https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/fabric — "Identity passthrough and access control" section

> *"This integration uses identity passthrough (On-Behalf-Of). The Fabric tool runs queries by using the identity of the signed-in user."*

> *"Use user identity authentication. Service principal authentication isn't supported for the Fabric data agent."*

---

### Citation 2 — Teams confirms the OBO requirement explicitly

**Source:** Same page — "Limitations" section

> *"The Fabric data agent tool doesn't work when the agent is published to Microsoft Teams. Agents published to Teams use project managed identity for authentication, but the Fabric data agent tool requires user identity passthrough (On-Behalf-Of)."*

---

### Citation 3 — New conversation resolves thread contention (new conversation = new Fabric thread, inferred)

**Source:** Same page — "Troubleshooting" table

> *"Can't add messages to thread_... while a run ... is active → A run is still active for the thread → Start a new conversation or wait for the active run to finish, then try again."*

⚠ **Honesty note:** Microsoft does not explicitly state "new Foundry conversation = new Fabric thread". This is **inferred** from the troubleshooting remedy above — if a new conversation reused the same thread, "start a new conversation" would not resolve the error.

---

### Citation 4 — Fabric side confirms OBO and user identity for queries

**Source:** https://learn.microsoft.com/en-us/fabric/data-science/data-agent-foundry — "How it works" section

> *"Uses the identity of the end user to generate secure queries over the data sources the user has permission to access."*

> *"Identity Passthrough (On-Behalf-Of) authorization secures this flow, to ensure robust security and proper access control across enterprise data."*

---

### Citation 5 — `fabric_data_agent_client` uses Assistants API directly, not Foundry

**Source:** https://github.com/microsoft/fabric_data_agent_client — `fabric_data_agent_client.py`

Confirmed in code: uses `client.beta.assistants.create()`, `client.beta.threads.runs.create()` — OpenAI Assistants API pointed directly at the Fabric endpoint (`base_url=self.data_agent_url`), token scoped to `https://api.fabric.microsoft.com/.default`. No Foundry involved.

---

### Citation 6 — Our OBO scope

**Source:** Our own code — `auth.py:172`

```python
FOUNDRY_SCOPES = ["https://ai.azure.com/.default"]
```

---

## What Is NOT Directly Cited

| Claim | Status |
|---|---|
| New conversation = new Fabric thread | ⚠ Inferred from troubleshooting doc, not explicitly stated |
| "One active run per user OID" as the enforcement mechanism | ⚠ Inferred from observed behavior + troubleshooting doc. No explicit Microsoft doc found stating this rule |
| Foundry does OBO #2 internally to Fabric | ⚠ Stated in the identity passthrough docs conceptually, but internal Foundry mechanics are not documented at that level |
