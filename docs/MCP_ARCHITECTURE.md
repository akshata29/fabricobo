# MCP Chat & Load Test — Architecture, Data Flow, and Implementation Guide

> **Purpose:** This document describes the MCP-based Fabric query path (`/api/agent/v2`)
> introduced alongside the original built-in path (`/api/agent`). It covers the full
> architecture, end-to-end request flow, token-security model, UI components, and the
> concurrency-theory load-test methodology.

---

## Table of Contents

1. [Background — Why an MCP Path?](#1-background--why-an-mcp-path)
2. [High-Level Architecture](#2-high-level-architecture)
3. [Component Map](#3-component-map)
4. [End-to-End Request Flow](#4-end-to-end-request-flow)
5. [Token Security Model](#5-token-security-model)
6. [MCP Server Implementation](#6-mcp-server-implementation)
7. [Foundry Agent Configuration](#7-foundry-agent-configuration)
8. [MCP Chat UI](#8-mcp-chat-ui)
9. [MCP Load Test UI](#9-mcp-load-test-ui)
10. [Wave Concurrency Analysis](#10-wave-concurrency-analysis)
11. [Configuration Reference](#11-configuration-reference)
12. [Deployment Notes](#12-deployment-notes)
13. [Microsoft Learn References](#13-microsoft-learn-references)

---

## 1. Background — Why an MCP Path?

The original `/api/agent` path uses the built-in `fabric_dataagent_preview` tool provided
by Azure AI Foundry Agents. Foundry manages the Fabric thread lifecycle internally: each
conversation reuses a single Fabric thread, so if the same user sends two requests
simultaneously the second one is blocked until the first completes ("run already active
on this thread").

**Hypothesis:** By replacing the built-in tool with a custom MCP server that:

1. Performs a Fabric-scoped OBO exchange independently, and  
2. Creates a **brand-new, uuid-named Fabric thread for every request**,

…we can side-step the per-thread serialisation constraint and achieve true parallel
Fabric execution for the same user identity.

The MCP Load Test (`McpLoadTest.tsx`) exists specifically to validate this hypothesis by
measuring whether concurrent waves of requests actually run in parallel or are still
serialised at a higher level.

> **Theory source:** The `fabric_data_agent_client` reference implementation notes that
> "New thread = new execution slot = concurrency works" in its thread-management section.  
> GitHub: <https://github.com/microsoft/fabric_data_agent_client>

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Browser (React SPA)                             │
│                                                                              │
│  ┌──────────────┐    POST /api/agent/v2     ┌──────────────────────────┐    │
│  │  McpChat.tsx  │ ──────────────────────── │                          │    │
│  └──────────────┘                           │   FastAPI (pythonapi/)   │    │
│                                             │                          │    │
│  ┌────────────────┐  POST /api/agent/v2     │  1. Validate JWT (OBO#1) │    │
│  │McpLoadTest.tsx │ ──────────────────────── │  2. OBO #2 (Fabric)     │    │
│  └────────────────┘                         │  3. Store token in store │    │
│                                             │  4. Call Foundry         │    │
└─────────────────────────────────────────────────────────────────────────────┘
                                                       │
                                        POST /openai/responses
                                        (inline MCP tool config)
                                                       │
                                               ┌───────▼────────┐
                                               │  Azure AI      │
                                               │  Foundry Agent  │
                                               │ (GPT-4o model)  │
                                               └───────┬────────┘
                                                       │
                                       tools/call query_fabric
                                       (POST /mcp  JSON-RPC 2.0)
                                                       │
                                    ┌──────────────────▼──────────────────┐
                                    │        /mcp  (Starlette ASGI)       │
                                    │     fabric_mcp_server.py             │
                                    │                                      │
                                    │  1. Look up Fabric OBO token        │
                                    │     by session_key                  │
                                    │  2. Create NEW uuid-named thread    │
                                    │  3. OpenAI Assistants API call      │
                                    │  4. Poll for completion             │
                                    │  5. Return text answer              │
                                    └──────────────────┬──────────────────┘
                                                       │
                                    Bearer <Fabric OBO token>
                                    POST  /threads/fabric?tag="mcp-direct-<uuid>"
                                                       │
                                    ┌──────────────────▼──────────────────┐
                                    │   Microsoft Fabric Data Agent        │
                                    │   (OpenAI Assistants v1 API)        │
                                    │    ↳  RLS enforced per OBO identity │
                                    └─────────────────────────────────────┘
```

**Key difference from the built-in path:**

| Aspect | Built-in path (`/api/agent`) | MCP path (`/api/agent/v2`) |
|---|---|---|
| Fabric tool | `fabric_dataagent_preview` (Foundry-managed) | `query_fabric` via custom MCP server |
| Thread lifecycle | Reused per Foundry conversation | **New UUID thread per request** |
| Fabric OBO exchange | Performed by Foundry internally | Performed by our API (`OBO #2`) |
| Concurrency (same user) | Serialised (one run per thread) | Potentially parallel |
| Fabric token exposure to LLM | No | No (session_key pattern) |

---

## 3. Component Map

| File | Role |
|---|---|
| [`pythonapi/main.py`](../pythonapi/main.py) | FastAPI app. Exposes `POST /api/agent/v2` and mounts `/mcp`. Calls `ensure_agent()` at startup. |
| [`pythonapi/mcp_foundry_service.py`](../pythonapi/mcp_foundry_service.py) | `McpFoundryAgentService` — creates/updates the Foundry agent, calls `POST /openai/responses` with inline MCP tool. |
| [`pythonapi/fabric_mcp_server.py`](../pythonapi/fabric_mcp_server.py) | The MCP server itself. `build_mcp_asgi_app()` → Starlette ASGI, handles JSON-RPC 2.0, calls Fabric Assistants API. |
| [`pythonapi/token_session_store.py`](../pythonapi/token_session_store.py) | In-process UUID→token dictionary. The Fabric OBO token never travels through Foundry's AI context. |
| [`pythonapi/auth.py`](../pythonapi/auth.py) | `OboTokenService` — dual OBO exchange (Foundry scope + Fabric scope). `acquire_service_token()` for startup. |
| [`pythonapi/config.py`](../pythonapi/config.py) | `FabricDirectSettings` — `FABRIC_DIRECT_*` env vars. `mcp_server_url` property = `api_public_url/mcp`. |
| [`client-app/src/McpChat.tsx`](../client-app/src/McpChat.tsx) | Chat UI calling `/api/agent/v2`. Teal styling, multi-turn conversation. |
| [`client-app/src/McpLoadTest.tsx`](../client-app/src/McpLoadTest.tsx) | Load test UI calling `/api/agent/v2`. Wave analysis, concurrency verdict, full result history. |

---

## 4. End-to-End Request Flow

Each numbered step corresponds to code in the files listed in parentheses.

### Step 1 — Browser sends question

```
POST /api/agent/v2
Authorization: Bearer <MSAL access token (API scope)>
{ "question": "What is the total balance?", "conversationId": "<optional>" }
```

Initiated by [`McpChat.tsx` — `sendMessage()`](../client-app/src/McpChat.tsx) or  
[`McpLoadTest.tsx` — `runSingleRequest()`](../client-app/src/McpLoadTest.tsx).

---

### Step 2 — API validates JWT and performs dual OBO exchange

`POST /api/agent/v2` handler in [`main.py`](../pythonapi/main.py):

```python
# OBO #1 — Foundry scope  (used to call Foundry Responses API)
foundry_obo_token = await obo_service.exchange_token(raw_token)

# OBO #2 — Fabric scope  (used by MCP tool to call Fabric directly with RLS)
fabric_obo_token = await obo_service.exchange_token_for_fabric(raw_token)
```

Both OBO exchanges are performed by [`auth.py` — `OboTokenService`](../pythonapi/auth.py)
using the Microsoft Identity Platform On-Behalf-Of flow.

> **Reference:** [On-Behalf-Of flow — Microsoft identity platform](https://learn.microsoft.com/entra/identity-platform/v2-oauth2-on-behalf-of-flow)

---

### Step 3 — Fabric token stored; session key generated

Still in the `/api/agent/v2` handler:

```python
session_key = str(uuid.uuid4())
token_session_store.set_token(session_key, fabric_obo_token)
```

The `session_key` is an opaque UUID. **The Fabric token itself never leaves this
process or appears in any AI prompt.** Only the session key UUID is forwarded.

See [`token_session_store.py`](../pythonapi/token_session_store.py).

---

### Step 4 — API calls Foundry with inline MCP tool

[`mcp_foundry_service.py` — `run_agent()`](../pythonapi/mcp_foundry_service.py)
prepends the session key to the question:

```python
tagged_input = f"[session_key={session_key}] {question}"
```

It then calls Foundry's Responses API with an **inline MCP tool** definition:

```python
# POST /openai/responses
{
  "model": "<deployment>",
  "input": "[session_key=<UUID>] What is the total balance?",
  "instructions": _MCP_SYSTEM_INSTRUCTIONS,
  "conversation_id": "<id>",
  "tools": [
    {
      "type": "mcp",
      "server_label": "fabric_direct",
      "server_url": "https://<public-api-url>/mcp",
      "require_approval": "never"
    }
  ]
}
```

When an agent has been pre-created, the request uses an `agent_reference` instead of
repeating inline instructions — see [`mcp_foundry_service.py`](../pythonapi/mcp_foundry_service.py).

> **Reference:** [MCP tool with Azure AI Foundry Agents](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/model-context-protocol)

---

### Step 5 — Foundry (GPT-4o) parses session key and calls MCP tool

The system instructions (`_MCP_SYSTEM_INSTRUCTIONS`) tell the model to:

1. Extract the UUID from the `[session_key=<UUID>]` prefix.
2. Call `query_fabric(question=<actual question>, session_key=<UUID>)`.
3. Return the tool result formatted as readable text, **without** including
   the session key in the visible response.

Foundry issues a `POST /mcp` with JSON-RPC 2.0 `tools/call`:

```json
{
  "jsonrpc": "2.0", "id": 1,
  "method": "tools/call",
  "params": {
    "name": "query_fabric",
    "arguments": {
      "question": "What is the total balance?",
      "session_key": "<UUID>"
    }
  }
}
```

---

### Step 6 — MCP server looks up Fabric token and creates a new thread

[`fabric_mcp_server.py` — `_handle_query_fabric()`](../pythonapi/fabric_mcp_server.py):

```python
fabric_token = get_token(session_key)          # look up from store
thread_name = f"mcp-direct-{uuid.uuid4()}"    # NEW thread per request
thread_info = await _create_fabric_thread(
    fabric_token, settings.data_agent_url, thread_name
)
```

`_create_fabric_thread()` calls:

```
GET {fabric_base}/threads/fabric?tag="{thread_name}"
Authorization: Bearer <Fabric OBO token>
```

The URL transformation (aiskills → dataagents) mirrors the
[fabric_data_agent_client](https://github.com/microsoft/fabric_data_agent_client)
reference library.

---

### Step 7 — Question posted and run polled

Using `AsyncOpenAI` pointed at the Fabric Data Agent endpoint:

```python
openai_client = AsyncOpenAI(
    api_key="unused",
    base_url=settings.data_agent_url,
    default_headers={"Authorization": f"Bearer {fabric_token}", ...},
)

assistant = await openai_client.beta.assistants.create(model="fabric-data-agent")
await openai_client.beta.threads.messages.create(thread_id=thread_id, role="user", content=question)
run = await openai_client.beta.threads.runs.create(thread_id=thread_id, assistant_id=assistant.id)

# Poll with configurable timeout
while run.status in ("queued", "in_progress"):
    await asyncio.sleep(2)
    run = await openai_client.beta.threads.runs.retrieve(...)
```

Row-Level Security is enforced by Fabric based on the OBO token identity. The thread
is deleted after the response is returned (stateless design).

---

### Step 8 — Response travels back

```
Fabric answer text
  → MCP server (JSON-RPC 2.0 result)
  → Foundry (GPT-4o formats response)
  → POST /openai/responses reply
  → mcp_foundry_service.run_agent() → AgentResponse
  → POST /api/agent/v2 response
  → Browser renders in McpChat.tsx or McpLoadTest.tsx
```

The Fabric OBO token is immediately evicted from the session store once the Foundry
round-trip is finished:

```python
token_session_store.delete_token(session_key)
```

---

## 5. Token Security Model

The MCP path handles three separate tokens:

| Token | Scope | Stored where | Travels to LLM? |
|---|---|---|---|
| MSAL access token | API audience | Browser → Authorization header | No |
| Foundry OBO token | Foundry / Azure AI scope | In-flight only (not persisted) | No |
| Fabric OBO token | Fabric Data Agent scope | `token_session_store` (in-process, keyed by session UUID) | **No** |

The session key pattern ensures the Fabric token never appears in:
- Any HTTP request body sent to Foundry
- The model's prompt or context
- Any log output (only the first 8 characters of the session key are logged for correlation)

The session store key is a random UUID4 generated fresh per request and deleted
immediately after the Foundry call returns.

> **Reference:** [On-Behalf-Of flow — Microsoft identity platform](https://learn.microsoft.com/entra/identity-platform/v2-oauth2-on-behalf-of-flow)

---

## 6. MCP Server Implementation

The MCP server ([`fabric_mcp_server.py`](../pythonapi/fabric_mcp_server.py)) implements
the **MCP Streamable HTTP transport** (protocol version `2025-03-26`) directly on
Starlette — no external MCP framework dependency.

### Supported JSON-RPC 2.0 Methods

| Method | Description |
|---|---|
| `initialize` | Returns capability handshake (`protocolVersion`, `serverInfo`) |
| `ping` | Keepalive — returns empty result |
| `tools/list` | Returns the `query_fabric` tool schema |
| `tools/call` | Executes `query_fabric(question, session_key)` |
| _(notifications, id=null)_ | Acknowledged with HTTP 202, no body |

### Tool Schema

```json
{
  "name": "query_fabric",
  "description": "Query the Microsoft Fabric Data Agent directly. Returns business data filtered by the authenticated user's Row-Level Security policy. Each invocation creates a new Fabric execution thread, enabling concurrent same-user queries.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "question":    { "type": "string", "description": "Natural language question about business data." },
      "session_key": { "type": "string", "description": "Short-lived opaque identifier (UUID) provided by the API request context." }
    },
    "required": ["question", "session_key"]
  }
}
```

### Path Normalisation Middleware

FastAPI strips the `/mcp` prefix when forwarding to the mounted sub-app. If the
original request was `POST /mcp` (no trailing slash), the sub-app receives `path=""`.
Starlette would issue a 307 redirect to `/` — wasting a devtunnel round-trip.

The `_NormalizePath` ASGI middleware handles this:

```python
class _NormalizePath:
    def __init__(self, app): self._app = app
    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path", "/") == "":
            scope = {**scope, "path": "/", "raw_path": b"/"}
        await self._app(scope, receive, send)
```

> **Reference:** [MCP Streamable HTTP transport specification](https://modelcontextprotocol.io/specification/basic/transports)

---

## 7. Foundry Agent Configuration

### Startup Agent Creation

At application startup ([`main.py` — `lifespan()`](../pythonapi/main.py)):

```python
service_token = await acquire_service_token()
await mcp_foundry_service.ensure_agent(service_token)
```

`ensure_agent()` in [`mcp_foundry_service.py`](../pythonapi/mcp_foundry_service.py):

1. `GET agents?api-version=2025-05-15-preview` — list existing agents
2. If the agent named `FabricMcpAgent` already exists → update (new version with current `mcp_server_url`)
3. If not found → `POST agents` with:

```json
{
  "name": "FabricMcpAgent",
  "definition": {
    "kind": "prompt",
    "model": "<model deployment name>",
    "instructions": "<_MCP_SYSTEM_INSTRUCTIONS>",
    "tools": [
      {
        "type": "mcp",
        "server_label": "fabric_direct",
        "server_url": "https://<api_public_url>/mcp",
        "require_approval": "never"
      }
    ]
  }
}
```

A `409 Conflict` is treated as "agent already exists" — safe to continue.

This means the agent appears in the Azure AI Foundry portal with full trace visibility
before any user request arrives.

> **Reference:** [Prompt Agent quickstart — Azure AI Foundry](https://learn.microsoft.com/azure/foundry/agents/quickstarts/prompt-agent)  
> **Reference:** [MCP tool — Azure AI Foundry Agents](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/model-context-protocol)

### System Instructions

The `_MCP_SYSTEM_INSTRUCTIONS` constant in `mcp_foundry_service.py` instructs GPT-4o to:

- Extract the UUID from the `[session_key=<UUID>]` prefix at the start of every input
- Pass it unmodified as the `session_key` argument to `query_fabric`
- Strip the prefix from the visible response
- Relay tool errors as clear user-facing messages

---

## 8. MCP Chat UI

**File:** [`client-app/src/McpChat.tsx`](../client-app/src/McpChat.tsx)

The MCP Chat component is a focused chat interface that calls `POST /api/agent/v2`
instead of `/api/agent`. It is functionally equivalent to `Chat.tsx` but is styled
in teal/green to visually distinguish the two paths during testing.

### Key Behaviours

- **Multi-turn conversations** — `conversationId` is persisted across messages and sent
  on each request so Foundry maintains context.
- **Quick questions** — Four preset questions for instant testing without typing.
- **Tool evidence** — Each assistant message can expand to show the `toolSteps` count
  and the `toolEvidenceDetail` array returned by the API.
- **Architecture hint** — The sidebar displays the active path configuration:
  ```
  Endpoint: /api/agent/v2
  Auth:     OBO x2 (Foundry + Fabric)
  Tool:     Inline MCP → /mcp → Fabric
  Theory:   new thread per request
  ```

### Data Contract

```typescript
// Request
POST /api/agent/v2
{ question: string, conversationId?: string }

// Response (AgentResponse)
{
  status: "completed" | "error" | ...,
  correlationId: string,
  conversationId?: string,
  responseId?: string,
  assistantAnswer?: string,
  toolEvidence?: { itemId, type, status, detail? }[],
  error?: string
}
```

---

## 9. MCP Load Test UI

**File:** [`client-app/src/McpLoadTest.tsx`](../client-app/src/McpLoadTest.tsx)

The MCP Load Test has full feature parity with `LoadTest.tsx` plus
**wave concurrency analysis** specific to the MCP path.

### Load Test Configuration

| Parameter | Default | Description |
|---|---|---|
| `concurrentUsers` | 5 | Number of requests fired simultaneously per wave |
| `totalRequests` | 20 | Total requests across all waves |
| `delayBetweenRequestsMs` | 0 | Stagger within a wave (0 = fire all at once) |
| `delayBetweenWavesMs` | 0 | Pause between waves |

### Identity Modes

- **Single user** — All requests use the logged-in user's token. Validates same-user concurrency.
- **Multi-user** — Builds a token pool from `SPA_TEST_USERS_JSON`. Each wave slot uses a
  different identity's Fabric OBO token, testing cross-user parallelism.

### Test History

Results are saved to `POST /api/tests` with `testId` prefixed `MCP-` (e.g. `MCP-A1B2C3D4`).
The history drawer fetches `GET /api/tests?type=mcp` — separated from the standard Chat
load test history which uses `?type=chat`.

### CU Metrics (F64 SKU)

```typescript
const CU_HOURS_PER_REQUEST    = 0.11;     // empirically measured
const F64_CU_HOURS_PER_DAY    = 1536;     // 64 CUs × 24 hours
const F64_MAX_REQUESTS_PER_DAY = Math.floor(F64_CU_HOURS_PER_DAY / CU_HOURS_PER_REQUEST);
// = 13,963 requests/day on F64
```

Each completed request contributes `0.11 CU-hours`. The summary panel shows total
CU-hours consumed and the percentage of the daily F64 budget used.

---

## 10. Wave Concurrency Analysis

This is unique to the MCP Load Test and is the primary mechanisam for validating the
new-thread concurrency hypothesis.

### `analyzeWave(waveResults: RequestResult[]): WaveAnalysis`

For each wave — a group of `N` requests fired simultaneously:

```typescript
const actualWaveMs    = max(endTimes) - min(startTimes);  // wall-clock duration of the wave
const maxIndividualMs = max(latencies);                    // slowest single request
const ratio           = actualWaveMs / maxIndividualMs;   // the key metric
const parallel        = ratio < 1.5;                      // threshold for "parallel"
```

| `ratio` | Interpretation |
|---|---|
| ≈ 1.0 | All requests ran truly in parallel — wall-clock ≈ slowest individual |
| 1.0–1.5 | Some overlap — classified as **parallel** |
| > 1.5 | Requests were largely serialised — wall-clock >> slowest individual |

After each wave completes, the log shows:

```
[PARALLEL] Wave W finished in 12.3s vs max individual 11.8s  overlap ratio: 1.0x
[SERIALISED] Wave W took 58.2s  4.9x longest individual  requests were serialised
```

### `overallVerdict(analyses: WaveAnalysis[])`

```typescript
const pct      = (parallelWaves / totalWaves) * 100;
const parallel = pct >= 60;
```

If **≥ 60% of waves ran in parallel** → verdict: **SUPPORTED** (new-thread theory works).  
Otherwise → verdict: **NOT SUPPORTED** (Fabric still serialises at a level above the thread).

The verdict panel appears below the progress bar in the left column and updates live as
each wave completes. It is also persisted in the `waveAnalyses` array saved to
`/api/tests`.

### Wave Analysis Table

Displayed after test completion (or when loading a historical run):

| Wave | Requests | Successful | Wall-clock (ms) | Max individual (ms) | Ratio | Verdict |
|---|---|---|---|---|---|---|
| 1 | 5 | 5 | 12,300 | 11,800 | 1.0 | PARALLEL |
| 2 | 5 | 4 | 53,400 | 11,200 | 4.8 | SERIALISED |

---

## 11. Configuration Reference

### Required Environment Variables (MCP path only)

Set these in addition to the standard `FOUNDRY_*` and `AZURE_*` variables.

| Variable | Description | Example |
|---|---|---|
| `FABRIC_DIRECT_DATA_AGENT_URL` | Fabric Data Agent published URL (OpenAI-compatible endpoint) | `https://<workspace>.fabric.microsoft.com/.../.../openai` |
| `FABRIC_DIRECT_API_PUBLIC_URL` | Publicly reachable URL of this API — used as the MCP `server_url` Foundry will call back to | `https://<tunnel>.devtunnels.ms` (dev) or `https://<api>.azurewebsites.net` (prod) |

### Optional Variables

| Variable | Default | Description |
|---|---|---|
| `FABRIC_DIRECT_MCP_AGENT_NAME` | `FabricMcpAgent` | Name of the Foundry agent created at startup |
| `FABRIC_DIRECT_QUERY_TIMEOUT_SECONDS` | `120` | Fabric polling timeout per request |

### Derived Settings (`config.py` — `FabricDirectSettings`)

```python
@property
def mcp_server_url(self) -> str:
    return self.api_public_url.rstrip("/") + "/mcp"

@property
def is_configured(self) -> bool:
    return bool(self.data_agent_url and self.api_public_url)
```

The `/mcp` endpoint is only mounted when `is_configured` is `True`.

---

## 12. Deployment Notes

### Development (devtunnel)

The `FABRIC_DIRECT_API_PUBLIC_URL` must be a publicly reachable URL because Foundry
calls back to `/mcp` from its cloud environment. During local development use a
devtunnel:

```bat
# start-tunnel.bat — creates a persistent devtunnel
# update-tunnel.bat — updates .env with the new tunnel URL
```

After the tunnel URL changes (e.g. restart), the API updates the `FabricMcpAgent`
automatically at startup by calling `ensure_agent()` with the new URL.

### Production

- Set `FABRIC_DIRECT_API_PUBLIC_URL` to the stable API hostname.
- The `/mcp` route must be reachable from Azure AI Foundry's outbound IPs.
- Ensure `FABRIC_DIRECT_DATA_AGENT_URL` points to the correct Fabric workspace and
  Data Agent deployment.
- Consider setting `FABRIC_DIRECT_QUERY_TIMEOUT_SECONDS` higher (e.g. `180`) if
  complex queries time out.

### Health Check

```
GET /health
→ { "status": "healthy", "implementation": "python", "mcp_path_configured": true }
```

---

## 13. Microsoft Learn References

| Topic | URL |
|---|---|
| MCP tool with Azure AI Foundry Agents | <https://learn.microsoft.com/azure/foundry/agents/how-to/tools/model-context-protocol> |
| Prompt Agent quickstart | <https://learn.microsoft.com/azure/foundry/agents/quickstarts/prompt-agent> |
| Azure AI Foundry Agents overview | <https://learn.microsoft.com/azure/foundry/agents/overview> |
| Fabric Data Agent (built-in tool) | <https://learn.microsoft.com/azure/ai-foundry/agents/how-to/tools/fabric> |
| On-Behalf-Of flow (OBO) | <https://learn.microsoft.com/entra/identity-platform/v2-oauth2-on-behalf-of-flow> |
| Foundry Responses API reference | <https://learn.microsoft.com/azure/foundry/agents/reference/responses-api> |
| Fabric Data Agent client (GitHub) | <https://github.com/microsoft/fabric_data_agent_client> |
| MCP Streamable HTTP transport spec | <https://modelcontextprotocol.io/specification/basic/transports> |
| Row-Level Security in Microsoft Fabric | <https://learn.microsoft.com/fabric/security/service-admin-row-level-security> |
