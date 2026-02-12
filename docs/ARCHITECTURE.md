# Architecture – Fabric OBO Identity Passthrough POC

## High-Level Architecture

```
┌──────────────────┐       ┌───────────────────────────────┐       ┌────────────────────────────────┐
│   Browser SPA    │ JWT   │  ASP.NET Core Web API (.NET 9)│  OBO  │  Azure AI Foundry              │
│   (MSAL.js)      │──────▶│                               │──────▶│  Responses API v2              │
│                  │       │  • JWT validation (Entra ID)  │       │                                │
│  • Login via     │◀──────│  • Entitlement lookup (UPN)   │◀──────│  • Named Agent (FabricOboAgent)│
│    MSAL popup    │  JSON │  • OBO token exchange         │  REST │  • Fabric Data Agent tool      │
│  • Chat UI       │       │  • Foundry Responses API call │       │  • Identity passthrough (OBO)  │
└──────────────────┘       └───────────────────────────────┘       └──────────┬─────────────────────┘
                                                                              │
                                                                              │ Delegates user
                                                                              │ identity (OBO)
                                                                              ▼
                                                              ┌──────────────────────────────┐
                                                              │  Microsoft Fabric             │
                                                              │  Warehouse + Data Agent       │
                                                              │                               │
                                                              │  • Published Data Agent       │
                                                              │    queries Warehouse tables   │
                                                              │  • RLS Security Policy        │
                                                              │    enforces per-user filtering │
                                                              │  • User sees ONLY their data  │
                                                              └──────────────────────────────┘
```

## Component Descriptions

| Component | Role |
|---|---|
| **SPA / Client** | Authenticates user via MSAL.js, obtains access token for the API scope (`api://{API_CLIENT_ID}/access_as_user`). Provides a chat UI to ask questions. |
| **ASP.NET Core Web API** | Validates the incoming JWT, calls the entitlement service, acquires an OBO token scoped to `https://ai.azure.com/.default`, and calls the Foundry v2 Responses API. |
| **Entitlement Service** | Advisory lookup mapping UPN → RepCode / permissions. **Not** an enforcement boundary — enforcement is by Fabric RLS. |
| **Azure AI Foundry Named Agent** | A pre-created named agent (`FabricOboAgent`) with a **Fabric Data Agent tool** configured for identity passthrough. Referenced via `agent_reference` in the Responses API. |
| **Fabric Data Agent** | Published data agent that runs queries against the Warehouse (`FabricOboPOC`). Fabric applies **Row-Level Security (RLS)** based on the identity of the user propagated through OBO → Foundry → Fabric. |

## Token Flow Detail

```
SPA Token                    OBO Token                    Fabric Identity
─────────────                ──────────                   ───────────────
aud: api://{API_APP_ID}  →   aud: https://ai.azure.com →  User's OID/UPN
appid: {SPA_CLIENT_ID}       appid: {API_APP_ID}          flows through to
scp: access_as_user          scp: user_impersonation       Fabric RLS
```

The API app (`FabricObo-API`) must have:
- **Delegated permission**: `user_impersonation` on Azure Machine Learning Services (`18a66f5f-dbdf-4c17-9dd7-1634712a9cbe`)
- **Admin consent** granted for all principals

## Sequence Diagram

```
User          SPA (MSAL.js)     Web API (.NET 9)     Entra ID        Foundry Responses    Fabric Data Agent
 │               │                   │                  │                  │                    │
 │──sign in─────▶│                   │                  │                  │                    │
 │  (popup)      │──acquireToken────▶│                  │                  │                    │
 │               │  scope: api://    │                  │                  │                    │
 │               │  {API}/access_as_ │                  │                  │                    │
 │               │  user             │                  │                  │                    │
 │               │◀──access_token────│                  │                  │                    │
 │               │                   │                  │                  │                    │
 │──ask question▶│                   │                  │                  │                    │
 │               │──POST /api/agent──▶                  │                  │                    │
 │               │  Authorization:   │                  │                  │                    │
 │               │  Bearer {token}   │                  │                  │                    │
 │               │                   │                  │                  │                    │
 │               │                   │──validate JWT───▶│                  │                    │
 │               │                   │◀──claims─────────│                  │                    │
 │               │                   │                  │                  │                    │
 │               │                   │──entitlement     │                  │                    │
 │               │                   │  lookup (UPN)    │                  │                    │
 │               │                   │  → RepCode       │                  │                    │
 │               │                   │                  │                  │                    │
 │               │                   │──OBO exchange────▶                  │                    │
 │               │                   │  scope: https:// │                  │                    │
 │               │                   │  ai.azure.com/   │                  │                    │
 │               │                   │  .default        │                  │                    │
 │               │                   │◀──OBO token──────│                  │                    │
 │               │                   │  (aud=ai.azure   │                  │                    │
 │               │                   │   .com)          │                  │                    │
 │               │                   │                  │                  │                    │
 │               │                   │──POST /openai/conversations────────▶                    │
 │               │                   │◀──{ conversationId }───────────────│                    │
 │               │                   │                  │                  │                    │
 │               │                   │──POST /openai/responses────────────▶│                    │
 │               │                   │  { agent_reference:                │                    │
 │               │                   │    FabricOboAgent }                │                    │
 │               │                   │                  │                  │──query as user────▶│
 │               │                   │                  │                  │  (OBO identity     │
 │               │                   │                  │                  │   passthrough)     │
 │               │                   │                  │                  │◀──RLS-filtered─────│
 │               │                   │◀──{ status: completed,             │   rows             │
 │               │                   │    output: [answer + tool_calls] } │                    │
 │               │                   │                  │                  │                    │
 │               │◀──JSON response───│                  │                  │                    │
 │               │  { conversationId,│                  │                  │                    │
 │               │    assistantAnswer,                  │                  │                    │
 │               │    toolEvidence,  │                  │                  │                    │
 │               │    entitlement }  │                  │                  │                    │
 │◀──display─────│                   │                  │                  │                    │
```

## Key Security Properties

1. **User identity flows end-to-end**: The OBO flow ensures Foundry and Fabric see the actual user, not the app.
2. **RLS enforcement at Fabric**: Even if the entitlement service is bypassed, Fabric RLS enforces data boundaries.
3. **No raw tokens forwarded**: The API never sends the user's original token to Fabric. The OBO token exchange produces a new, scoped-down token that Foundry uses internally.
4. **Entitlement is advisory**: Used for UX/logging, not as a security boundary.
5. **Foundry Fabric tool uses identity passthrough**: Configured via the named agent — the Fabric Data Agent tool delegates the user identity.
6. **Named agent approach**: The agent (`FabricOboAgent`) is created by an admin. Users only need RBAC access to the Foundry project — the agent definition (with Fabric tool) is already configured.

## Tracing & Observability

- **CorrelationId**: Generated per request, logged across entitlement + Foundry calls.
- **Tool Evidence**: The v2 Responses API returns `fabric_dataagent_preview_call` items in the output proving tool execution.
- **Application Insights**: Foundry agent tracing sends tool invocation telemetry to Application Insights.
