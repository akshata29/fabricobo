# Technical Guide — Fabric OBO Identity Passthrough

> **Audience**: Developers, architects, and technical stakeholders who need to understand how this POC works internally, what the gotchas are, and how to adapt it for production.

---

## Table of Contents

0. [Plain-English Primer — What Are App Registrations?](#0-plain-english-primer--what-are-app-registrations-and-why-do-we-need-them)
1. [How It Works — End to End](#1-how-it-works--end-to-end)
2. [Token Architecture Deep Dive](#2-token-architecture-deep-dive)
3. [The OBO Exchange — What Happens Internally](#3-the-obo-exchange--what-happens-internally)
4. [Named Agent vs Inline Tools](#4-named-agent-vs-inline-tools)
5. [Fabric Data Agent & Identity Passthrough](#5-fabric-data-agent--identity-passthrough)
6. [Row-Level Security (RLS) at the Fabric Layer](#6-row-level-security-rls-at-the-fabric-layer)
7. [API Permissions — Why Two Resource Apps](#7-api-permissions--why-two-resource-apps)
8. [The Entitlement Service — Advisory vs Enforcement](#8-the-entitlement-service--advisory-vs-enforcement)
9. [Gotchas, Pitfalls & Lessons Learned](#9-gotchas-pitfalls--lessons-learned)
10. [Tips for Customer Deployments](#10-tips-for-customer-deployments)
11. [Security Considerations](#11-security-considerations)
12. [Troubleshooting Reference](#12-troubleshooting-reference)
13. [API Reference](#13-api-reference)
14. [Glossary](#14-glossary)

---

## 0. Plain-English Primer — What Are App Registrations and Why Do We Need Them?

> **Skip this section if you're already comfortable with Entra ID, OAuth 2.0, and OBO flows.** This is written for team members who aren't auth specialists but need to understand the moving parts.

### Think of It Like a Building Security System

Imagine your office building has a security desk. You can't just walk in — you need a badge. But your badge only opens *your* floor; it doesn't open the server room or the executive suite.

That's essentially what Entra ID (formerly Azure AD) does for this app. It's the security desk, and the "badges" are **tokens** — short-lived digital passes that say who you are and what you're allowed to do.

### What Is an App Registration?

An **app registration** is like registering a new type of badge reader with building security. You're telling Entra ID: *"Hey, I have a new application. Here's its name and what it needs access to."*

We have **two** app registrations because we have **two** separate applications that talk to each other:

| App Registration | What It Represents | Real-World Analogy |
|---|---|---|
| **FabricObo-Client** (SPA) | The web page the user sees in their browser | The lobby badge reader — it identifies who you are |
| **FabricObo-API** (API) | The backend server that talks to Foundry & Fabric | The elevator system — it takes your identity and carries it to the right floor |

### How Does a User Get a Token?

Here's what happens when you click **"Sign In"** in the browser — in plain English:

1. **The browser opens a Microsoft login popup** — this is MSAL.js (a Microsoft library) talking to Entra ID. It says: *"Hi, I'm the FabricObo-Client app (client ID `<SPA_CLIENT_ID>`). A user wants to sign in."*

2. **The user types their username/password** (or uses SSO if already signed in). Entra ID verifies them.

3. **Entra ID creates Token #1** — a digital badge that says:
   - *Who*: "This is Fabric User A (`fabricusera@...`)"
   - *For what*: "This token is only valid for the FabricObo API" (`aud=api://<API_CLIENT_ID>`)
   - *What they can do*: `access_as_user`
   
   Think of it as: **"This badge lets Fabric User A through the lobby door, but only to reach the elevator (API). It doesn't work anywhere else."**

4. **The browser stores this token** and attaches it to every request it sends to the API — like showing your badge every time you approach the elevator.

### What Happens When You Ask a Question?

5. **The browser sends your question + Token #1 to the API.** The API checks the token: *"Is this a real badge? Is it valid? Is it for me?"* If yes, it reads who the user is from the token.

6. **The API needs to call Foundry (the AI service), but Token #1 doesn't work there** — it's like having a lobby badge but needing to get into a different building. So the API goes back to Entra ID and says:

   > *"I'm the FabricObo-API app. I have this user's lobby badge (Token #1). Can you give me a new badge that works for the Foundry building, but still has this user's name on it?"*

   This is the **OBO (On-Behalf-Of) exchange**. Entra ID verifies everything and creates **Token #2**:
   - *Who*: Still "Fabric User A" (same person!)
   - *For what*: "This token is valid for Azure AI Foundry" (`aud=https://ai.azure.com`)
   - *What they can do*: `user_impersonation`

   **The user's identity is preserved, but the badge now works in a different building.**

7. **The API sends your question + Token #2 to Foundry.** Foundry sees User A's identity and passes it through to Fabric, which uses it to enforce Row-Level Security.

### Why Can't the Browser Just Call Foundry Directly?

Because Token #1 (the browser's badge) says `aud=api://...` — it's only for the API. If you tried to show it at the Foundry building, the security desk would reject it with a 401 ("wrong badge"). The API is the trusted intermediary that exchanges it.

### Why Two App Registrations Instead of One?

**Security separation.** The SPA (browser app) is a **public client** — anyone can see its code (it's JavaScript). It has no secrets. The API is a **confidential client** — it has a secret key that only the server knows. The OBO exchange *requires* a client secret, which is why only the API (not the browser) can perform it.

| | SPA (FabricObo-Client) | API (FabricObo-API) |
|---|---|---|
| **Has a secret?** | No — it's JavaScript in the browser, anyone can see it | Yes — stored securely on the server |
| **Can do OBO?** | No — OBO requires a secret | Yes — uses its secret to prove identity to Entra ID |
| **Token it gets** | Token #1: "Let me talk to the API" | Token #2: "Let me talk to Foundry as this user" |

### The "SPA Platform" Gotcha

When you register the SPA app, you must tell Entra ID *what kind of app it is*. The redirect URI (`http://localhost:5173`) must be added under the **"Single-page application"** platform — not "Web" and not "Mobile/desktop". If you put it under "Web", Entra ID expects a server-side app with a secret, and the browser login will fail with error `AADSTS9002326`. See [Gotcha 1.5](#-gotcha-15-cross-origin-token-redemption-error-aadsts9002326) for the fix.

### Visual Summary

```
  YOU (User A)                        Entra ID (Security Desk)
    │                                        │
    │── "I want to sign in" ────────────────▶│
    │◀── Token #1 (badge for API only) ──────│
    │                                        │
    │── Question + Token #1 ──▶ API Server   │
    │                              │         │
    │                              │── "Exchange Token #1    │
    │                              │   for a Foundry badge   │
    │                              │   for this same user" ──▶
    │                              │◀── Token #2 ────────────│
    │                              │   (badge for Foundry,   │
    │                              │    still says User A)   │
    │                              │                         │
    │                              │── Question + Token #2 ──▶ Foundry ──▶ Fabric
    │                              │                                       (sees User A,
    │                              │◀── Answer (only User A's data) ◀─────  applies RLS)
    │◀── Answer ───────────────────│
```

---

## 1. How It Works — End to End

The POC proves that a user's identity can flow from a browser, through multiple Azure services, all the way to Microsoft Fabric — so that Fabric's Row-Level Security (RLS) filters data based on **who the user actually is**, not who the application is.

### The Five Hops

```
Browser SPA ──①──▶ ASP.NET Core API ──②──▶ Entra ID (OBO) ──③──▶ Foundry Responses API ──④──▶ Fabric Data Agent ──⑤──▶ Fabric Warehouse (RLS)
```

| Hop | What Happens |
|-----|-------------|
| **①** | SPA authenticates via MSAL.js popup. User gets a token with `aud=api://API_APP_ID` and `scp=access_as_user`. This token only works against the .NET API — it cannot be sent to Foundry or Fabric directly. |
| **②** | The API validates the JWT, extracts the user's UPN/OID for logging, then calls `ITokenAcquisition.GetAccessTokenForUserAsync` which performs the **OBO exchange** with Entra ID. |
| **③** | Entra returns a **new** token with `aud=https://ai.azure.com` and `scp=user_impersonation`. This token carries the user's identity but is scoped for the Foundry service. The `appid` claim changes from the SPA's client ID to the API's client ID. |
| **④** | The API sends the OBO token to the Foundry v2 Responses API with an `agent_reference` pointing to the pre-configured named agent (`FabricOboAgent`). Foundry uses the bearer token's identity when calling downstream tools. |
| **⑤** | The named agent's Fabric Data Agent tool queries the Fabric Warehouse. Fabric sees the **user's identity** (not the app identity) and applies RLS. The user only gets rows their RepCode is authorized to see. |

### What Makes This Work

- Every hop uses **delegated (user) permissions**, never application-only
- The OBO flow is the critical bridge — it transforms a "talk to my API" token into a "talk to Foundry as this user" token
- Fabric receives the user's identity through Foundry's identity passthrough mechanism
- RLS is enforced by Fabric at the query layer — the LLM never sees unauthorized data

---

## 2. Token Architecture Deep Dive

Understanding the token chain is the **single most important** thing for debugging this flow. Every failure we encountered during development traced back to a token audience or scope mismatch.

### Token 1: SPA → API (the "front-door" token)

```json
{
  "aud": "api://<API_CLIENT_ID>",                         // API app
  "iss": "https://login.microsoftonline.com/{tenant}/v2.0",
  "appid": "<SPA_CLIENT_ID>",                              // SPA app
  "scp": "access_as_user",
  "upn": "fabricusera@tenant.onmicrosoft.com",
  "oid": "<USER_OBJECT_ID>",
  "name": "Fabric User A"
}
```

**Key facts:**
- `aud` is the API app — this token is for **your** API, not Foundry
- `appid` is the SPA — identifies which client app requested the token
- `scp` is `access_as_user` — the custom scope you defined in "Expose an API"
- This token **cannot** be sent to Foundry (wrong audience → 401)

### Token 2: API → Foundry (the "OBO" token)

```json
{
  "aud": "https://ai.azure.com",                          // Foundry
  "iss": "https://login.microsoftonline.com/{tenant}/v2.0",
  "appid": "<API_CLIENT_ID>",                              // API app (NOT the SPA)
  "scp": "user_impersonation",
  "upn": "fabricusera@tenant.onmicrosoft.com",             // Same user!
  "oid": "<USER_OBJECT_ID>",                               // Same OID!
  "name": "Fabric User A"
}
```

**Key facts:**
- `aud` changed to `https://ai.azure.com` — this is the Azure Machine Learning Services app (resource ID `18a66f5f-dbdf-4c17-9dd7-1634712a9cbe`)
- `appid` changed to the **API app** — Foundry sees the API as the calling application
- `scp` changed to `user_impersonation` — the delegated permission you granted to the API app
- **The user identity (`upn`, `oid`, `name`) is preserved** — this is the whole point of OBO

### Why This Matters

```
❌ SPA token sent directly to Foundry → 401 (wrong audience)
❌ App-only token sent to Foundry → Fabric sees APP identity, not USER → RLS doesn't work
✅ OBO token sent to Foundry → Fabric sees USER identity → RLS filters correctly
```

---

## 3. The OBO Exchange — What Happens Internally

When the API calls `ITokenAcquisition.GetAccessTokenForUserAsync(["https://ai.azure.com/.default"])`, here's what happens under the hood:

```
API Server                           Entra ID
    │                                    │
    │── POST /oauth2/v2.0/token ────────▶│
    │   grant_type=urn:ietf:params:      │
    │     oauth:grant-type:jwt-bearer    │
    │   assertion={incoming_user_JWT}     │
    │   client_id={API_APP_ID}           │
    │   client_secret={API_SECRET}       │
    │   scope=https://ai.azure.com/      │
    │     .default                       │
    │   requested_token_use=on_behalf_of │
    │                                    │
    │◀── { access_token: {OBO_token},  ──│
    │     token_type: Bearer,            │
    │     expires_in: 3599 }             │
```

### Why `.default` scope?

The scope `https://ai.azure.com/.default` tells Entra: "Give me all the statically-consented delegated permissions for this resource." Since you admin-consented `user_impersonation` on the API app registration for the Azure Machine Learning Services resource, the OBO token comes back with `scp=user_impersonation`.

### Token Caching

The `ITokenAcquisition` middleware uses Microsoft Identity Web's built-in token cache. For this POC, we use `AddInMemoryTokenCaches()`. This means:

- Tokens are cached per-user per-scope
- Subsequent requests from the same user reuse the cached OBO token until it expires
- **For production**: Use `AddDistributedTokenCaches()` backed by Redis or SQL Server, especially if running multiple API instances

---

## 4. Named Agent vs Inline Tools

The Foundry v2 Responses API supports two modes. We use the **Named Agent** approach.

### Option A: Inline Tools (what we tried first)

```json
POST /openai/responses?api-version=2025-05-15-preview
{
  "model": "chat4o",
  "input": "Show me all accounts",
  "instructions": "You are a data assistant...",
  "tools": [{
    "type": "fabric_dataagent_preview",
    "fabric_dataagent_preview": {
      "project_connections": [{
        "project_connection_id": "/subscriptions/.../connections/fabricoboda"
      }]
    }
  }],
  "tool_choice": "required"
}
```

**Pros**: No pre-configuration needed, everything in one call.
**Cons**: Requires the caller to know the connection ID and tool configuration.

### Option B: Named Agent Reference (what we use)

First, create the agent once (admin operation):

```json
POST /agents?api-version=2025-05-15-preview
{
  "name": "FabricOboAgent",
  "definition": {
    "kind": "prompt",
    "model": "chat4o",
    "instructions": "You are a helpful data analysis assistant...",
    "tools": [{
      "type": "fabric_dataagent_preview",
      "fabric_dataagent_preview": {
        "project_connections": [{
          "project_connection_id": "/subscriptions/.../connections/fabricoboda"
        }]
      }
    }],
    "tool_choice": "auto"
  }
}
```

Then reference it in every call:

```json
POST /openai/responses?api-version=2025-05-15-preview
{
  "input": "Show me all accounts",
  "conversation": "conv_abc123",
  "tool_choice": "auto",
  "agent": {
    "name": "FabricOboAgent",
    "type": "agent_reference"
  }
}
```

**Pros**: 
- Cleaner API calls — no tool configuration in requests
- Agent created by admin, users just reference it
- Agent definition can be updated without code changes
- Consistent instructions and tool configuration

**Cons**: Requires initial admin setup via REST API (no portal UI for named agents yet).

### Why Named Agent Won

With inline tools, we encountered `"Create assistant failed"` errors when user tokens (non-admin) were used. The named agent approach worked because:

1. An admin creates the agent definition (one-time)
2. Users only need RBAC access to the Foundry project to **reference** the existing agent
3. Required RBAC: `Cognitive Services User` + `Azure AI Developer` on the Foundry resource

---

## 5. Fabric Data Agent & Identity Passthrough

### How Fabric Data Agent Works

A Fabric Data Agent is a published artifact in a Fabric workspace. It:

- Accepts natural language queries
- Translates them to SQL using the warehouse schema as context
- Executes the SQL against the Fabric Warehouse
- Returns results back to the caller

### Identity Passthrough

When the Foundry agent uses the `fabric_dataagent_preview` tool type, the tool is configured for **identity passthrough**. This means:

1. Foundry forwards the **user's identity** from the OBO token to Fabric
2. Fabric authenticates the user and applies workspace-level permissions
3. When SQL queries execute, RLS policies are evaluated against the **user's identity**
4. The Data Agent only sees rows the user is authorized to see

> **Critical**: Identity passthrough is not a feature you toggle on/off in the agent. It's inherent to how the `fabric_dataagent_preview` tool type works when called with a delegated (OBO) token. If you used an app-only token, Fabric would see the app identity, and RLS based on user UPN would fail.

### Data Agent Configuration

The Fabric Data Agent must be:
1. **Created** in the Fabric workspace pointing to the warehouse
2. **Configured** with the tables it can access (e.g., `Accounts`, `RepUserMapping`)
3. **Published** — unpublished agents cannot be connected to Foundry
4. **Connected** to the Foundry project as a Fabric connection

---

## 6. Row-Level Security (RLS) at the Fabric Layer

### How RLS Works in Fabric Warehouse

Fabric Warehouse RLS uses T-SQL security policies with predicate functions — the same pattern as SQL Server and Azure SQL.

```sql
-- 1. The predicate function
CREATE FUNCTION dbo.fn_SecurityPredicate(@RepCode NVARCHAR(50))
RETURNS TABLE
WITH SCHEMABINDING
AS
RETURN
    SELECT 1 AS fn_securitypredicate_result
    WHERE @RepCode IN (
        SELECT RepCode
        FROM dbo.RepUserMapping
        WHERE UserEmail = USER_NAME()
    )
    -- Admin bypass: users with Fabric admin roles see all data
    OR IS_MEMBER('db_owner') = 1;

-- 2. The security policy
CREATE SECURITY POLICY dbo.AccountFilter
    ADD FILTER PREDICATE dbo.fn_SecurityPredicate(RepCode) ON dbo.Accounts
    WITH (STATE = ON);
```

### How Identity Flows to RLS

1. User signs in as `fabricusera@tenant.onmicrosoft.com`
2. OBO token carries this UPN through to Foundry → Fabric
3. When the Data Agent executes a query, `USER_NAME()` returns `fabricusera@tenant.onmicrosoft.com`
4. The predicate function looks up the `RepUserMapping` table: fabricusera → REP001
5. The filter predicate only returns rows where `Accounts.RepCode = 'REP001'`
6. User A sees: Contoso Ltd, Northwind Traders, Adventure Works, Fabrikam Inc
7. User B (REP002) sees: Tailspin Toys, Wide World Importers, Proseware Inc

### Important: What RLS Does NOT Do

- RLS does **not** filter at the Foundry/AI layer — it filters at the **SQL query execution layer**
- RLS does **not** require the LLM to be aware of authorization — the LLM generates a query, and Fabric's query engine applies the filter transparently
- RLS is **not** prompt-based — it's a SQL Server security primitive that cannot be bypassed by prompt injection

This is precisely why this architecture is more secure than "tell the LLM to filter by user" approaches.

---

## 7. API Permissions — The Complete Picture

This section explains **every permission** on **every app registration**, what it does, and what breaks if you remove it. This is the single most common source of setup failures.

### Plain-English: What Are API Permissions?

When you create an app registration, it has **zero** access to anything by default. API permissions are how you say: *"This app is allowed to call that service, acting on behalf of the signed-in user."*

There are two types:
- **Delegated** = "Act on behalf of a user" — the app can only do what the user themselves could do. Used in this entire POC.
- **Application** = "Act as the app itself, with no user" — used for background services. **Not used in this POC** because we need the user's identity to flow through to Fabric for RLS.

**Admin consent** means a tenant admin pre-approves the permission for all users, so individual users don't see a consent popup.

---

### The Two App Registrations — Side by Side

| | **FabricObo-Client** (SPA) | **FabricObo-API** (API) |
|---|---|---|
| **App ID** | `01b70e26-c61e-4287-9f0d-f07b4ed3b66a` | `21260626-6004-4699-a7d0-0773cbcd6192` |
| **Display Name** | FabricObo-Client | FabricObo-API |
| **Type** | Public client (no secret) | Confidential client (has a secret) |
| **Platform** | Single-page application (SPA) | Web API (no redirect URIs) |
| **Redirect URIs** | `http://localhost:5173` (SPA platform) | None |
| **Has a client secret?** | No | Yes |
| **Sign-in audience** | Single tenant (`AzureADMyOrg`) | Single tenant (`AzureADMyOrg`) |
| **Exposes an API?** | No | Yes — scope `access_as_user` |
| **Purpose** | Identifies the browser app to Entra ID during login | Validates incoming JWTs, performs OBO exchange, calls Foundry |

---

### FabricObo-Client (SPA) — Permissions Explained

| Permission | Resource App | Type | Admin Consent | What It Does | What Breaks Without It |
|---|---|---|---|---|---|
| `access_as_user` | **FabricObo-API** (`21260626-...`) | Delegated | Granted | Allows the SPA to request a token scoped to the API. This is the "front-door" token — it lets the browser talk to your backend. | Login succeeds but the token has no valid scope for the API. The API returns 401 on every request. |
| `user_impersonation` | **Azure Machine Learning Services** (`18a66f5f-...`) | Delegated | Granted | *Not strictly required on the SPA*, but was added during development. The SPA never calls Foundry directly — the API does that. | Nothing breaks in the current flow. This is a leftover that could be removed for a cleaner setup. |

#### How the SPA Uses Its Permissions

```
1. User clicks "Sign In"
2. MSAL.js calls Entra ID: "I'm app 01b70e26..., I need scope api://21260626.../access_as_user"
3. Entra ID checks: Does this app have permission to request this scope? YES (access_as_user is granted)
4. Entra ID issues Token #1 with aud=api://21260626... and scp=access_as_user
5. SPA sends Token #1 to the API with every request
```

#### SPA Platform Configuration (Critical)

The redirect URI `http://localhost:5173` **must** be registered under the **Single-page application** platform, **not** "Web" or "Mobile/desktop":

| Platform | What It Means | Why SPA Must Use It |
|---|---|---|
| **Web** | Server-side app with a secret — uses authorization code flow *with a secret* | Browser JS has no secret → Entra rejects with `AADSTS9002326` |
| **Mobile and desktop** | Native app — uses device code or broker flows | Can cause Entra to misclassify the app's client type for localhost |
| **Single-page application** ✅ | Browser JS app — uses authorization code flow with **PKCE** (no secret needed) | This is what MSAL.js `loginPopup()` requires |

---

### FabricObo-API (API) — Permissions Explained

This is the app that does the heavy lifting. It has permissions on **two** Microsoft first-party services, plus it **exposes** a custom scope.

#### Permissions It Requests (outbound — "what can this app call?")

| Permission | Resource App | App ID | Type | Admin Consent | What It Does | What Breaks Without It |
|---|---|---|---|---|---|---|
| `user_impersonation` | **Azure Machine Learning Services** | `18a66f5f-dbdf-4c17-9dd7-1634712a9cbe` | Delegated | Granted | Allows the API to exchange the user's token (OBO) for a Foundry token scoped to `https://ai.azure.com`. This is the **core OBO permission** — without it, the token exchange fails. | OBO exchange fails with `AADSTS65001` (consent not granted). The API cannot get a Foundry token. Everything after login is broken. |
| `user_impersonation` | **Microsoft Cognitive Services** | `7d312290-28c8-473c-a0ed-8e53749b6d6d` | Delegated | Granted | Foundry's Responses API internally validates that the calling app has Cognitive Services permissions. This is in addition to the ML Services permission. | Some Foundry operations work, others fail with `"Create assistant failed"`. Named agent calls with user tokens are especially affected. |

> **Why two resource apps?** Azure AI Foundry sits across two service boundaries internally — the ML platform (`ai.azure.com`) and the cognitive/OpenAI infrastructure. They use different Entra apps for permission checks. Your screenshot shows exactly this: 1 entry for Azure Machine Learning Services + 3 entries for Microsoft Cognitive Services (the 3 entries are duplicates — only 1 is needed, but they don't cause harm).

#### Scopes It Exposes (inbound — "who can call me?")

| Scope Name | Scope URI | Who Can Consent | What It Does |
|---|---|---|---|
| `access_as_user` | `api://21260626-6004-4699-a7d0-0773cbcd6192/access_as_user` | Admins and users | Defines the "door" that the SPA knocks on. When the SPA requests this scope, Entra issues a token with `aud=api://21260626...` and `scp=access_as_user`. The API validates these claims in every incoming JWT. |

This scope was created under **Expose an API** in the portal. It's what connects the SPA to the API — without it, the SPA can't request a token that the API will accept.

#### Client Secret

The API has a client secret (`Jux8Q~...`) stored in `appsettings.Development.json`. This secret is used during the OBO exchange:

```
API → Entra ID: "I'm app 21260626..., here's my secret to prove it.
                 Please exchange this user's token for a Foundry token."
```

Without the secret, the OBO exchange fails — Entra ID requires proof that the calling app is who it claims to be.

> **Production note**: Use certificates instead of secrets. Certificates can't be accidentally committed to source control, and they don't expire as abruptly. See Microsoft's guidance on [credential best practices](https://learn.microsoft.com/entra/identity-platform/certificate-credentials).

---

### How the Permissions Chain Together

Here's the complete flow showing which permission is used at each step:

```
Step 1: SPA → Entra ID
  Permission used: SPA has "access_as_user" on FabricObo-API
  Result: Token #1 (aud=api://21260626..., scp=access_as_user)

Step 2: API validates Token #1
  Checks: aud matches my app ID? scp includes access_as_user? ✅

Step 3: API → Entra ID (OBO exchange)
  Permission used: API has "user_impersonation" on Azure ML Services (18a66f5f...)
  Proof: API presents client secret + user's Token #1
  Result: Token #2 (aud=https://ai.azure.com, scp=user_impersonation)

Step 4: API → Foundry Responses API
  Permission checked by Foundry: Token #2 has user_impersonation for ML Services ✅
  Permission checked by Foundry: API app has user_impersonation for Cognitive Services ✅
  Result: Foundry accepts the call, passes user identity to Fabric

Step 5: Foundry → Fabric
  Identity: User A's OID/UPN from Token #2 (preserved through OBO)
  Fabric applies RLS based on this identity
```

### What If a Permission Is Missing? — Failure Matrix

| Missing Permission | Where It Fails | Error You'll See |
|---|---|---|
| SPA → `access_as_user` on API | Step 1 (login) | `AADSTS65001: The user or admin has not consented to use the application` |
| API → `user_impersonation` on Azure ML Services | Step 3 (OBO) | `AADSTS65001: consent required` or `MicrosoftIdentityWebChallengeUserException` |
| API → `user_impersonation` on Cognitive Services | Step 4 (Foundry call) | `"Create assistant failed"` or `403 Forbidden` from Foundry |
| API → Client secret missing/expired | Step 3 (OBO) | `AADSTS7000215: Invalid client secret provided` |
| SPA redirect URI on wrong platform | Step 1 (login popup) | `AADSTS9002326: Cross-origin token redemption is permitted only for 'Single-Page Application'` |
| Admin consent not granted | Step 1 or 3 | `AADSTS65001: admin has not consented` (even if permission is listed, it must be consented) |

### Note on Duplicate Permissions in Your Setup

Your screenshot shows **3 entries** for `user_impersonation` under Microsoft Cognitive Services. This is a portal artifact — the permission was added multiple times (likely during troubleshooting). It doesn't cause harm, but you can clean it up:

1. Go to **API permissions** on the API app
2. Click the `...` menu next to two of the three Cognitive Services entries
3. Click **Remove permission**
4. Leave one `user_impersonation` entry
5. Re-grant admin consent

> **Gotcha**: If you only add one of the two resource apps (ML Services or Cognitive Services), some operations may work (e.g., inline tools with admin tokens) while others fail (e.g., named agents with user tokens). Always add both.

---

## 8. The Entitlement Service — Advisory vs Enforcement

### Design Philosophy

The `IEntitlementService` in this POC is explicitly **advisory, not enforcement**. Here's why:

```
Entitlement Service says "UserA = REP001"  →  Used for UX hints and logging
Fabric RLS says "UserA can see REP001 rows" →  Actually controls data access
```

### Why Not Enforce at the API Layer?

1. **Defense in depth**: Even if someone bypasses the entitlement check (bug, misconfiguration), Fabric RLS still blocks unauthorized data.
2. **Single source of truth**: Authorization rules live in one place (Fabric RLS), not scattered across multiple services.
3. **LLM unpredictability**: If you put authorization logic in the prompt or middleware, an adversarial prompt could potentially bypass it. RLS is a SQL engine primitive — immune to prompt injection.

### What the Entitlement Service IS Good For

- **UX enrichment**: Show the user their RepCode and role in the UI
- **Audit logging**: Log which rep code was associated with each request
- **Early rejection**: If a user shouldn't use the system at all (not just data filtering), the entitlement service can reject before making expensive Foundry calls

### When Do You Need a Real Entitlement Service?

| Use Case | Need Entitlement? | Why |
|---|---|---|
| Only certain users can use the app at all | **Yes** — gate at API layer | Reject early before spending resources on Foundry API calls |
| Show user's RepCode/role in the chat UI | **Yes** — advisory info | Currently working (the badges in the chat details panel) |
| Control what data users see | **No** — Fabric RLS handles this | Entitlement cannot enforce what Fabric returns; RLS is the only reliable boundary |
| Audit who accessed what | **Yes** — logging enrichment | RepCode in structured logs helps with compliance and tracing |
| Different UI features per role | **Yes** — role-based UX | e.g., Admins see an analytics dashboard, Advisors see a chat-only view |
| Rate limiting by user tier | **Yes** — business logic | e.g., Free-tier users get 10 questions/day, Premium gets unlimited |

### How It Works Today (Stub Implementation)

The current `StubEntitlementService` is a hardcoded dictionary — no database, no network calls:

```
Services/
├── IEntitlementService.cs      ← Interface (1 method)
├── StubEntitlementService.cs   ← Hardcoded dictionary (current)
```

**Current behavior:**
- Known users (User A, User B) → returns their RepCode + Role
- Unknown users → still returns `IsAuthorized = true` (lets them through)
- If the service throws an exception → the controller **catches it and continues** (Fabric RLS is the real gate)

This means today the entitlement service is purely cosmetic — removing it entirely would not change the security posture.

---

### Production Scenario A: Gate Access (Block Unauthorized Users)

**Goal**: Only users in your entitlement database can use the app. Everyone else gets a 403 Forbidden.

#### Step 1: Change `StubEntitlementService` to reject unknown users

**File**: `Services/StubEntitlementService.cs`

Change the unknown-user fallback from `IsAuthorized = true` to `IsAuthorized = false`:

```csharp
// BEFORE (current — lets everyone through):
return Task.FromResult(new EntitlementResult
{
    Upn = upn,
    Oid = oid,
    RepCode = null,
    Role = null,
    IsAuthorized = true  // Advisory; Fabric RLS is the real gate
});

// AFTER (blocks unknown users):
return Task.FromResult(new EntitlementResult
{
    Upn = upn,
    Oid = oid,
    RepCode = null,
    Role = null,
    IsAuthorized = false  // Reject users not in our mapping
});
```

#### Step 2: Enforce the flag in `AgentController`

**File**: `Controllers/AgentController.cs`

After the entitlement lookup (around the `_logger.LogInformation` line for entitlement), add a guard:

```csharp
// Existing code:
entitlement = await _entitlementService.GetEntitlementAsync(upn, oid);
_logger.LogInformation(
    "[{CorrelationId}] Entitlement: RepCode={RepCode}, Role={Role}, Authorized={Auth}",
    correlationId, entitlement.RepCode, entitlement.Role, entitlement.IsAuthorized);

// ADD THIS after the log line:
if (!entitlement.IsAuthorized)
{
    _logger.LogWarning(
        "[{CorrelationId}] Access denied for UPN={Upn} — not in entitlement database",
        correlationId, upn);
    return StatusCode(403, new AgentResponse
    {
        Status = "forbidden",
        CorrelationId = correlationId,
        Error = "You are not authorized to use this application. Contact your administrator."
    });
}
```

#### Step 3 (Optional): Handle the 403 in the frontend

**File**: `client-app/src/Chat.tsx`

In the `sendMessage` function, after `const data: AgentResponse = await response.json();`, the existing error handling already covers non-200 responses. A 403 will show the error message in the chat. For a better UX, you could add specific handling:

```tsx
if (response.status === 403) {
  // Redirect to a "not authorized" page, or show a modal
  setMessages((prev) => [
    ...prev,
    {
      role: "error",
      content: data.error || "You are not authorized to use this application.",
      timestamp: new Date(),
    },
  ]);
  return; // Don't continue processing
}
```

---

### Production Scenario B: Replace Stub with a Real Database

**Goal**: Look up user entitlements from a SQL database instead of a hardcoded dictionary.

#### Step 1: Create a new service implementation

**Create file**: `Services/SqlEntitlementService.cs`

```csharp
using FabricObo.Models;
using Microsoft.Data.SqlClient;
using Dapper;

namespace FabricObo.Services;

/// <summary>
/// Production entitlement service backed by a SQL database.
/// Queries the UserEntitlements table to map UPN → RepCode/Role.
/// </summary>
public sealed class SqlEntitlementService : IEntitlementService
{
    private readonly string _connectionString;
    private readonly ILogger<SqlEntitlementService> _logger;

    public SqlEntitlementService(IConfiguration config, ILogger<SqlEntitlementService> logger)
    {
        _connectionString = config.GetConnectionString("EntitlementDb")
            ?? throw new InvalidOperationException(
                "Missing 'ConnectionStrings:EntitlementDb' in configuration.");
        _logger = logger;
    }

    public async Task<EntitlementResult> GetEntitlementAsync(string upn, string oid)
    {
        using var conn = new SqlConnection(_connectionString);

        var row = await conn.QuerySingleOrDefaultAsync<EntitlementRow>(
            """
            SELECT RepCode, Role
            FROM dbo.UserEntitlements
            WHERE Upn = @Upn
            """,
            new { Upn = upn });

        if (row is null)
        {
            _logger.LogWarning("No entitlement found for UPN={Upn}, OID={Oid}", upn, oid);
            return new EntitlementResult
            {
                Upn = upn,
                Oid = oid,
                RepCode = null,
                Role = null,
                IsAuthorized = false  // Or true, depending on your policy
            };
        }

        return new EntitlementResult
        {
            Upn = upn,
            Oid = oid,
            RepCode = row.RepCode,
            Role = row.Role,
            IsAuthorized = true
        };
    }

    private sealed class EntitlementRow
    {
        public string? RepCode { get; init; }
        public string? Role { get; init; }
    }
}
```

#### Step 2: Add the NuGet packages

```bash
dotnet add package Dapper
dotnet add package Microsoft.Data.SqlClient
```

#### Step 3: Add the connection string to configuration

**File**: `appsettings.json`

```json
{
  "ConnectionStrings": {
    "EntitlementDb": "Server=your-server.database.windows.net;Database=EntitlementDb;Authentication=Active Directory Default;"
  }
}
```

#### Step 4: Register the new service in DI

**File**: `Program.cs`

```csharp
// BEFORE:
builder.Services.AddSingleton<IEntitlementService, StubEntitlementService>();

// AFTER:
builder.Services.AddScoped<IEntitlementService, SqlEntitlementService>();
```

> **Note**: Changed from `AddSingleton` to `AddScoped` because the SQL service opens a connection per request.

#### Step 5: Create the database table

```sql
CREATE TABLE dbo.UserEntitlements (
    Id          INT IDENTITY(1,1) PRIMARY KEY,
    Upn         NVARCHAR(256)  NOT NULL UNIQUE,
    RepCode     NVARCHAR(50)   NOT NULL,
    Role        NVARCHAR(100)  NOT NULL,
    CreatedAt   DATETIME2      DEFAULT GETUTCDATE()
);

-- Seed test data matching Fabric RLS RepUserMapping
INSERT INTO dbo.UserEntitlements (Upn, RepCode, Role) VALUES
    ('fabricusera@MngEnvMCAP152362.onmicrosoft.com', 'REP001', 'Advisor'),
    ('fabricuserb@MngEnvMCAP152362.onmicrosoft.com', 'REP002', 'Advisor');
```

> **Important**: Keep this table in sync with the Fabric `RepUserMapping` table. If they disagree, the entitlement service will show one RepCode in the UI while Fabric RLS enforces a different data filter.

---

### Production Scenario C: Enrich the Agent Prompt with User Context

**Goal**: Pass the user's RepCode/Role to the Foundry agent so the LLM can reference it in responses (e.g., *"As REP001, you manage 4 accounts."*).

#### Step 1: Modify `FoundryAgentService` to accept entitlement data

**File**: `Services/IFoundryAgentService.cs` — add entitlement to the method signature (or wrap in a context object):

```csharp
Task<AgentResponse> RunAgentAsync(
    string question,
    string? conversationId,
    string oboToken,
    string correlationId,
    EntitlementResult? entitlement,   // ← NEW
    CancellationToken ct);
```

#### Step 2: Prepend context to the user's question

**File**: `Services/FoundryAgentService.cs` — before sending the request to Foundry:

```csharp
// Enrich the question with user context (optional, for better LLM responses)
var enrichedQuestion = question;
if (entitlement?.RepCode != null)
{
    enrichedQuestion =
        $"[User context: RepCode={entitlement.RepCode}, Role={entitlement.Role}]\n{question}";
}
```

#### Step 3: Pass entitlement from the controller

**File**: `Controllers/AgentController.cs` — update the `RunAgentAsync` call:

```csharp
// BEFORE:
var agentResponse = await _foundryAgentService.RunAgentAsync(
    request.Question, request.ConversationId, oboToken, correlationId, cancellationToken);

// AFTER:
var agentResponse = await _foundryAgentService.RunAgentAsync(
    request.Question, request.ConversationId, oboToken, correlationId, entitlement, cancellationToken);
```

> **Caution**: Adding user context to the prompt is a UX improvement, **not a security measure**. The LLM could hallucinate or ignore the prepended context. Fabric RLS remains the enforcement boundary.

---

### Summary of All Files Changed Per Scenario

| File | Scenario A (Gate) | Scenario B (SQL DB) | Scenario C (Prompt) |
|---|---|---|---|
| `Services/StubEntitlementService.cs` | Modify (reject unknowns) | Remove (replaced) | No change |
| `Services/SqlEntitlementService.cs` | — | **Create** (new file) | — |
| `Services/IFoundryAgentService.cs` | — | — | Modify (add parameter) |
| `Services/FoundryAgentService.cs` | — | — | Modify (enrich question) |
| `Controllers/AgentController.cs` | Modify (add 403 guard) | No change | Modify (pass entitlement) |
| `Program.cs` | No change | Modify (swap DI registration) | No change |
| `appsettings.json` | No change | Modify (add connection string) | No change |
| `client-app/src/Chat.tsx` | Optional (403 UX) | No change | No change |
| `FabricObo.csproj` | No change | Modify (add NuGet packages) | No change |

---

## 9. Gotchas, Pitfalls & Lessons Learned

These are real issues we hit during development. Each one cost hours to diagnose.

### 🔴 Gotcha 1: SPA Token Cannot Be Sent Directly to Foundry

**Symptom**: 401 Unauthorized when calling Foundry with the SPA's token.

**Root Cause**: The SPA token has `aud=api://API_APP_ID` — it's audience-scoped for your API, not for Foundry. Foundry expects `aud=https://ai.azure.com`.

**Fix**: The API must perform the OBO exchange. Never send the SPA token directly to downstream services.

### 🔴 Gotcha 1.5: Cross-Origin Token Redemption Error (AADSTS9002326)

**Symptom**: Login popup fails with `AADSTS9002326: Cross-origin token redemption is permitted only for the 'Single-Page Application' client-type. Request origin: 'http://localhost:5173'`.

**Root Cause**: The SPA app registration's redirect URI (`http://localhost:5173`) is registered under the **Web** platform instead of the **Single-Page Application** platform. The Web platform expects a server-side confidential client and does not allow browser-based PKCE flows. Additionally, having conflicting redirect URIs on the **Mobile and desktop applications** (publicClient) platform for `http://localhost` can cause Entra ID to misclassify the app's client type.

**Fix**:
1. In the Azure Portal → App registrations → SPA app → **Authentication**
2. **Remove** `http://localhost:5173` from the **Web** platform (if present)
3. **Remove** any `http://localhost` entries from **Mobile and desktop applications** (publicClient) platform
4. Click **Add a platform** → choose **Single-page application** → add `http://localhost:5173`
5. Save

Or via CLI:
```bash
# Add SPA redirect URI
az ad app update --id <SPA_APP_ID> --set spa="{'redirectUris':['http://localhost:5173']}"

# Clear conflicting Web redirect URIs (if any)
az ad app update --id <SPA_APP_ID> --set web.redirectUris="[]"

# Clear conflicting publicClient redirect URIs
az ad app update --id <SPA_APP_ID> --set publicClient="{'redirectUris':[]}"
```

> **Important**: Entra ID changes can take 1-2 minutes to propagate. If the error persists immediately after the fix, try an incognito browser window.

### 🔴 Gotcha 2: "Create Assistant Failed" with User Tokens

**Symptom**: Admin tokens work, user tokens get `"Create assistant failed"` from Foundry.

**Root Cause (likely combination)**:
- Missing `user_impersonation` consent on the Cognitive Services resource app
- Insufficient RBAC on the Foundry resource (need both `Cognitive Services User` AND `Azure AI Developer`)
- Attempting inline tool mode — named agent approach is more reliable for non-admin users

**Fix**: 
1. Add both Azure ML Services AND Cognitive Services delegated permissions with admin consent
2. Assign both RBAC roles at the resource level
3. Use named agent approach (created by admin, referenced by user)

### 🔴 Gotcha 3: Fabric Data Agent Must Be Published

**Symptom**: Fabric connection in Foundry can't find the data agent, or tool calls fail silently.

**Root Cause**: The Data Agent was created but never published. Foundry can only connect to **published** Fabric Data Agents.

**Fix**: In the Fabric portal, open the Data Agent and click **Publish**. You must re-publish after any configuration changes.

### 🟡 Gotcha 4: RLS Function Must Handle Admin Bypass

**Symptom**: Admin users (workspace owners) can't see any data when testing.

**Root Cause**: The RLS predicate function requires the user's email to be in `RepUserMapping`. Admin/owner users typically aren't in this table.

**Fix**: Add `OR IS_MEMBER('db_owner') = 1` to the predicate function. This lets workspace admins bypass RLS for testing and debugging, while regular users are still filtered.

### 🟡 Gotcha 5: The `.default` Scope Behavior

**Symptom**: OBO token comes back with unexpected scopes, or scope validation fails.

**Root Cause**: Using a specific scope like `https://ai.azure.com/user_impersonation` instead of `https://ai.azure.com/.default`.

**Why**: The `.default` scope tells Entra to return **all statically-consented permissions** for the target resource. Since you admin-consented `user_impersonation`, the OBO token will include `scp=user_impersonation`. Using the specific permission as the scope won't work for v2 endpoints in OBO flows.

### 🟡 Gotcha 6: MFA Requirement with Device Code Flow

**Symptom**: When testing with `get-token-device.ps1` or similar scripts, MFA-enabled users fail to get tokens via device code flow.

**Root Cause**: Device code flow is considered a "less secure" flow. If your tenant requires MFA, it may block device code authentication.

**Fix**: Either:
- Temporarily adjust Conditional Access policies for testing
- Use browser-based interactive flow instead (which supports MFA natively)
- In the SPA, MSAL.js popup/redirect flows handle MFA automatically

### 🟡 Gotcha 7: Foundry API Version Matters

**Symptom**: Requests fail with 400 Bad Request, or tool types aren't recognized.

**Root Cause**: Using an old API version that doesn't support `fabric_dataagent_preview` or named agents.

**Fix**: Use `api-version=2025-05-15-preview` or later. The Fabric Data Agent tool type and named agent reference are preview features that require a recent API version.

### 🟢 Gotcha 8: Conversations API for Multi-Turn

**Symptom**: Follow-up questions lose context from previous turns.

**Root Cause**: Each call to the Responses API is stateless unless you provide a `conversation` ID.

**Fix**: 
1. Call `POST /openai/conversations` to get a conversation ID
2. Pass `"conversation": "{convId}"` in every subsequent Responses API call
3. Foundry maintains context across turns within the same conversation

### 🟢 Gotcha 9: Token Cache Location for IIS Deployments

**Symptom**: After ~1 hour or after app pool recycle, users get `MicrosoftIdentityWebChallengeUserException`.

**Root Cause**: `AddInMemoryTokenCaches()` stores OBO tokens in process memory. IIS app pool recycles clear this cache, and the middleware can't refresh the OBO token without user interaction.

**Fix**: For production IIS deployments, use `AddDistributedTokenCaches()` with Redis or SQL Server:

```csharp
.AddDistributedTokenCaches()
.AddStackExchangeRedisCache(options => {
    options.Configuration = "your-redis:6380";
});
```

---

## 10. Tips for Customer Deployments

### Planning Checklist

- [ ] Identify the Fabric workspace and warehouse that contain the customer's data
- [ ] Design the RLS predicate — which column identifies the user's data partition?
- [ ] Create the user-to-partition mapping table (e.g., `RepUserMapping`)
- [ ] Decide on the entitlement model — will you use a real database or another identity source?
- [ ] Plan RBAC assignments — who gets access to the Foundry project?
- [ ] Plan admin consent — a Global Admin or Privileged Role Admin must consent to the API permissions

### RBAC Roles Required

| Who | Role | Scope | Why |
|-----|------|-------|-----|
| All API users | `Cognitive Services User` | Foundry resource | Call the Responses API |
| All API users | `Azure AI Developer` | Foundry resource | Reference named agents |
| Named agent creator | `Cognitive Services User` + `Azure AI Developer` | Foundry resource | Create/update the named agent definition |
| Data consumers | `Contributor` or `Viewer` | Fabric workspace | Access the warehouse (RLS further filters) |

### Performance Considerations

1. **Foundry response time**: The v2 Responses API is synchronous. With Fabric Data Agent tool calls, expect **10-30 seconds** per request depending on query complexity.
2. **Token caching**: OBO tokens are valid for ~1 hour. The token cache avoids repeated OBO exchanges for the same user within that window.
3. **Concurrent users**: Each user gets their own OBO token and conversation. The API is stateless except for the token cache.
4. **Fabric Warehouse capacity**: RLS adds negligible overhead to query execution. The bottleneck is typically the natural language → SQL translation in the Data Agent.

### Scaling Notes

- The .NET API is stateless (aside from token cache) and can be horizontally scaled
- Each API instance needs access to the same distributed token cache
- Foundry manages agent concurrency internally — no connection pooling needed from the API
- The Fabric Warehouse scales with the Fabric capacity SKU

---

## 11. Security Considerations

### What This Architecture Gets Right

1. **No token forwarding**: The SPA token is never forwarded to downstream services. OBO produces a new, narrowly-scoped token.
2. **Deterministic authorization**: RLS is a SQL engine primitive — it cannot be bypassed by prompt injection, adversarial inputs, or LLM jailbreaks.
3. **Least privilege**: Each hop grants the minimum necessary permissions. The SPA can only talk to the API. The API can only call Foundry on behalf of the user.
4. **Audit trail**: Every request has a correlation ID, logged UPN/OID, entitlement data, and tool execution evidence.

### What to Watch For in Production

| Risk | Mitigation |
|------|-----------|
| Client secret in `appsettings.json` | Use Azure Key Vault or Managed Identity. Never commit secrets to source control. |
| In-memory token cache | Switch to distributed cache (Redis/SQL) for multi-instance deployments. |
| Overly permissive RLS | Test with multiple users. Verify that `USER_NAME()` returns the expected UPN in Fabric. |
| Stale Fabric Data Agent | If you change warehouse tables/schema, update and **re-publish** the Data Agent. |
| Missing admin consent | New users will fail with `consent_required` errors. Ensure admin consent is granted for all principals, not individual users. |
| CORS configuration | In production, restrict `AllowedOrigins` to your actual SPA domain. Don't leave `localhost` entries. |
| HTTPS enforcement | The OBO flow requires HTTPS. Never run the API over HTTP in any environment that handles real tokens. |

### Client Secret Management

For this POC, the client secret is in `appsettings.json`. For production:

```csharp
// Option 1: Azure Key Vault
builder.Configuration.AddAzureKeyVault(
    new Uri("https://your-vault.vault.azure.net/"),
    new DefaultAzureCredential());

// Option 2: Environment variable
"ClientSecret": "${FABRIC_OBO_CLIENT_SECRET}"

// Option 3: Managed Identity (eliminates secrets entirely)
// Configure the API app registration to use certificate credentials
// and the Azure hosting to use a managed identity for Key Vault access
```

---

## 12. Troubleshooting Reference

### Quick Diagnostics

| HTTP Status | Response Body | Likely Cause | Fix |
|-------------|--------------|-------------|-----|
| 401 | `Bearer error="invalid_token"` | Expired or wrong-audience JWT | Re-acquire token with correct scope |
| 401 | `obo_challenge` | OBO exchange needs consent | Grant admin consent on the API app |
| 500 | `obo_error` | OBO exchange failed | Check API app has `user_impersonation` on Azure ML Services + Cognitive Services |
| 200 | `status: "failed"` | Foundry agent couldn't process | Check named agent exists and has correct tool config |
| 200 | `status: "completed"` but no data | RLS filtering returned 0 rows | Verify user is in `RepUserMapping` table |
| 200 | `status: "completed"` with all data | RLS not enforced | Check security policy state is ON, predicate function is correct |

### Token Debugging

To inspect a token, paste it at [jwt.ms](https://jwt.ms) and check:

- `aud` — Must be `api://API_APP_ID` for the front-door token, `https://ai.azure.com` for the OBO token
- `scp` — Must be `access_as_user` (front-door) or `user_impersonation` (OBO)
- `appid` — Must be the SPA client ID (front-door) or the API client ID (OBO)
- `upn` — Must be the actual user UPN, not a service principal

### Verifying RLS in Fabric Directly

You can test RLS independently of the API by running SQL in the Fabric portal as different users:

```sql
-- Run as fabricusera → should see REP001 accounts only
SELECT * FROM dbo.Accounts;

-- Check what USER_NAME() returns (should be the user's UPN)
SELECT USER_NAME() AS CurrentUser;

-- Check the mapping table
SELECT * FROM dbo.RepUserMapping WHERE UserEmail = USER_NAME();
```

### Checking Named Agent Exists

```powershell
$token = az account get-access-token --scope "https://ai.azure.com/.default" --query accessToken -o tsv
$endpoint = "https://your-account.services.ai.azure.com/api/projects/your-project"

# List all agents
Invoke-RestMethod -Uri "$endpoint/agents?api-version=2025-05-15-preview" `
  -Headers @{ Authorization = "Bearer $token" }

# Get specific agent by name
Invoke-RestMethod -Uri "$endpoint/agents?api-version=2025-05-15-preview&name=FabricOboAgent" `
  -Headers @{ Authorization = "Bearer $token" }
```

---

## 13. API Reference

### POST /api/agent

The single endpoint exposed by the .NET API.

**Request:**
```json
{
  "question": "Show me all my accounts",
  "conversationId": "conv_abc123"  // optional, for multi-turn
}
```

**Headers:**
```
Authorization: Bearer {SPA_access_token}
Content-Type: application/json
```

**Success Response (200):**
```json
{
  "status": "completed",
  "correlationId": "a1b2c3d4e5f6",
  "conversationId": "conv_abc123",
  "responseId": "resp_xyz789",
  "assistantAnswer": "Here are your accounts:\n\n| Account | Balance | Region |\n|---|---|---|\n| Contoso Ltd | $1,250,000 | East |...",
  "toolEvidence": [
    {
      "itemId": "fc_001",
      "type": "fabric_dataagent_preview_call",
      "status": "completed",
      "detail": "{...truncated JSON...}"
    }
  ],
  "entitlement": {
    "upn": "fabricusera@tenant.onmicrosoft.com",
    "oid": "1d3ff50d-...",
    "repCode": "REP001",
    "role": "Advisor",
    "isAuthorized": true
  }
}
```

**Error Response (200 with error status):**
```json
{
  "status": "error",
  "correlationId": "a1b2c3d4e5f6",
  "error": "Foundry API error: 403 — Forbidden"
}
```

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `completed`, `failed`, `error`, `timeout`, `obo_challenge`, `obo_error` |
| `correlationId` | string | Unique ID for tracing across all logs |
| `conversationId` | string | Foundry conversation ID for multi-turn |
| `responseId` | string | Foundry Responses API response ID |
| `assistantAnswer` | string | The LLM's text answer (may contain markdown) |
| `toolEvidence` | array | Proof that Fabric tool was invoked |
| `entitlement` | object | Advisory user entitlement data |
| `error` | string | Error details (only on failure) |

---

## 14. Glossary

| Term | Definition |
|------|-----------|
| **OBO (On-Behalf-Of)** | An OAuth 2.0 flow where a middle-tier API exchanges an incoming user token for a new token scoped to a downstream service, while preserving the user's identity. |
| **RLS (Row-Level Security)** | A SQL security feature that transparently filters rows based on the executing user's identity. Applied at the query engine level — immune to application-layer bypasses. |
| **Named Agent** | A pre-configured agent definition in Azure AI Foundry. Created once by an admin via REST API. Referenced by name in Responses API calls. |
| **Agent Reference** | A JSON payload `{ "name": "...", "type": "agent_reference" }` used in the Responses API to reference a named agent instead of passing inline tools. |
| **Fabric Data Agent** | A published artifact in Microsoft Fabric that translates natural language queries to SQL and executes them against a Fabric Warehouse. |
| **Identity Passthrough** | The mechanism by which Foundry forwards the user's identity (from the OBO token) to the Fabric Data Agent tool, so Fabric sees the actual user, not the application. |
| **Entitlement Service** | An advisory service that maps user identity (UPN) to business permissions (RepCode). Not an enforcement boundary in this architecture. |
| **SPA (Single Page Application)** | The browser-based React application that authenticates users via MSAL.js and sends requests to the .NET API. |
| **Foundry Responses API** | The v2 synchronous API for Azure AI Foundry agents. Replaces the classic threads/messages/runs/polling pattern with a single POST that returns the complete answer. |
| **Fabric Connection** | A resource link in Azure AI Foundry that connects a Foundry project to a Fabric Data Agent. Created in the Foundry Management Center. |
| **`access_as_user`** | The custom delegated scope defined in the API app's "Expose an API" configuration. Granted to the SPA app registration. |
| **`user_impersonation`** | The delegated permission on Azure Machine Learning Services / Cognitive Services that allows the API app to call Foundry on behalf of the user. |
| **Correlation ID** | A unique identifier generated per API request, logged across all downstream calls for traceability. |
| **Tool Evidence** | Output items from the Responses API (type `fabric_dataagent_preview_call`) that prove the Fabric tool was invoked during agent processing. |
