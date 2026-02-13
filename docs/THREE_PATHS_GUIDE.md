# Three Paths to Fabric OBO — Complete Reference Guide

> **All three paths produce the same OBO token carrying the user's identity → Fabric RLS works identically.**

This document details the three ways to access the Foundry Agent with OBO identity passthrough. All paths share the same backend (`IFoundryAgentService`, `FoundryAgentService`) and differ only in how the user's token is acquired.

---

## Architecture Overview

```
                    ┌────────────────────────────────────────────────────────────┐
                    │                   Entra App Registration                  │
                    │         21260626-6004-4699-a7d0-0773cbcd6192              │
                    │   Scope: api://21260626-.../access_as_user                │
                    │   Tenant: 37f28838-9a79-4b20-a28a-c7d8a85e4eda           │
                    └────────────────┬───────────────────────────────────────────┘
                                     │
                 ┌───────────────────┼───────────────────────────┐
                 │                   │                           │
      ┌──────────▼──────┐  ┌────────▼─────────┐   ┌────────────▼────────────┐
      │  Path 1: SPA    │  │  Path 2: Teams   │   │  Path 3: Copilot       │
      │  (React + MSAL) │  │  Bot Framework   │   │  Studio                │
      │                 │  │                   │   │  (HTTP or Skill)       │
      └────────┬────────┘  └────────┬──────────┘   └────────────┬───────────┘
               │                    │                            │
      POST /api/agent      POST /api/messages         POST /api/agent (HTTP)
      [Authorize] JWT      CloudAdapter auth           OR /api/messages (Skill)
               │                    │                            │
     ITokenAcquisition     BotOboTokenService         Bearer token passthrough
     (Microsoft.Identity.Web)  (MSAL direct)          OR CloudAdapter
               │                    │                            │
               └────────────────────┼────────────────────────────┘
                                    │
                          ┌─────────▼──────────┐
                          │ IFoundryAgentService│
                          │ (shared)            │
                          └─────────┬──────────┘
                                    │ OBO Token (user identity)
                                    ▼
                  Azure AI Foundry Responses API
                                    │
                                    ▼
                  Microsoft Fabric (RLS enforced per user)
```

---

## Quick Comparison

| Aspect | Path 1: SPA | Path 2: Teams Bot | Path 3: Copilot Studio |
|--------|-------------|-------------------|----------------------|
| **Endpoint** | `POST /api/agent` | `POST /api/messages` | `/api/agent` (HTTP) or `/api/messages` (Skill) |
| **Auth method** | MSAL.js → JWT Bearer | Bot Service OAuth + SSO | Copilot auth → Bearer token OR Bot Framework |
| **Token exchange** | `ITokenAcquisition` | `BotOboTokenService` | Existing controller OR `BotOboTokenService` |
| **Code changes** | None (baseline) | Already implemented | **Zero** (HTTP) or **Zero** (Skill) |
| **User experience** | Web browser | Teams chat | Copilot Studio chat |
| **SSO** | MSAL popup/redirect | Teams silent SSO | Copilot auth flow |
| **Best for** | Custom web UI | Teams-native users | No-code/low-code admins |

---

## Path 1: SPA (Existing — Baseline)

**Status: ✅ Fully implemented and working**

This is the original path. The React SPA uses MSAL.js to authenticate the user, acquires a token for `api://21260626-.../access_as_user`, and sends it as a Bearer token to the API.

### How It Works

1. User opens SPA at `http://localhost:5173`
2. MSAL.js acquires token scoped to `api://21260626-6004-4699-a7d0-0773cbcd6192/access_as_user`
3. SPA calls `POST /api/agent` with `Authorization: Bearer <token>`
4. `AgentController` (decorated with `[Authorize]`) validates the JWT
5. `ITokenAcquisition.GetAccessTokenForUserAsync(["https://ai.azure.com/.default"])` performs OBO exchange
6. `FoundryAgentService.RunAgentAsync()` calls Foundry with the OBO token
7. Fabric RLS scopes data to the signed-in user

### Files Involved

| File | Role |
|------|------|
| `client-app/src/authConfig.ts` | MSAL.js configuration (loaded from `/api/config`) |
| `client-app/src/App.tsx` | Auth flow UI |
| `client-app/src/Chat.tsx` | Chat UI, calls `/api/agent` |
| `Controllers/AgentController.cs` | `[Authorize]` API endpoint, OBO via `ITokenAcquisition` |
| `Services/FoundryAgentService.cs` | Shared Foundry Responses API caller |
| `Program.cs` | DI for `AddMicrosoftIdentityWebApiAuthentication` + `EnableTokenAcquisitionToCallDownstreamApi` |

### Key Config (appsettings.json)

```json
{
  "AzureAd": {
    "Instance": "https://login.microsoftonline.com/",
    "TenantId": "37f28838-9a79-4b20-a28a-c7d8a85e4eda",
    "ClientId": "21260626-6004-4699-a7d0-0773cbcd6192",
    "ClientSecret": "<secret>",
    "Audience": "api://21260626-6004-4699-a7d0-0773cbcd6192",
    "Scopes": "access_as_user"
  }
}
```

### To Test

```bash
# Terminal 1: Start API
.\startapi.bat

# Terminal 2: Start frontend
.\startfrontend.bat

# Open http://localhost:5173, sign in, ask a question
```

---

## Path 2: Teams Bot (Implemented)

**Status: ✅ Implemented — ready for testing**

A Bot Framework bot registered in Azure Bot Service. Users chat in Teams (or Web Chat), the bot handles SSO + OBO internally, then calls the same `IFoundryAgentService`.

### How It Works

1. User sends message in Teams / Web Chat
2. Bot Framework routes the message to `POST /api/messages`
3. `CloudAdapter` authenticates the Bot Framework protocol message
4. `FabricOboBot.OnMessageActivityAsync()` is called
5. `UserTokenClient.GetUserTokenAsync()` retrieves the user's token from the Bot Service OAuth connection
   - If no token is cached, a sign-in card is sent (Teams SSO or magic code flow)
6. `BotOboTokenService.ExchangeTokenAsync()` performs OBO exchange via MSAL
7. `FoundryAgentService.RunAgentAsync()` calls Foundry with the OBO token
8. Bot formats and sends the response back to the user

### Files Involved

| File | Role |
|------|------|
| `Bot/BotController.cs` | `POST /api/messages` endpoint (no `[Authorize]` — CloudAdapter handles auth) |
| `Bot/FabricOboBot.cs` | `TeamsActivityHandler` — SSO, magic code, OBO exchange, Foundry call |
| `Bot/BotOboTokenService.cs` | MSAL-based OBO exchange (`IBotOboTokenService`) |
| `Services/FoundryAgentService.cs` | Shared Foundry Responses API caller (same as SPA path) |
| `Program.cs` | DI for `CloudAdapter`, `BotFrameworkAuthentication`, `IBotOboTokenService`, `IBot` |
| `teams-manifest/manifest.json` | Teams app manifest with bot + SSO config |

### Key Config (appsettings.json)

```json
{
  "Bot": {
    "MicrosoftAppId": "21260626-6004-4699-a7d0-0773cbcd6192",
    "MicrosoftAppPassword": "<same-as-AzureAd:ClientSecret>",
    "MicrosoftAppTenantId": "37f28838-9a79-4b20-a28a-c7d8a85e4eda",
    "MicrosoftAppType": "SingleTenant",
    "OAuthConnectionName": "FabricOboConnection"
  }
}
```

### Azure Resources Required

| Resource | Details |
|----------|---------|
| **Azure Bot Service** | Name: `FabricOboBot`, Resource Group: `astaipublic`, Tier: F0, Type: SingleTenant |
| **OAuth Connection** | Name: `FabricOboConnection`, Provider: `Aadv2`, Scopes: `api://21260626-6004-4699-a7d0-0773cbcd6192/access_as_user openid profile` |
| **Teams Channel** | Enabled on the Azure Bot resource |

### Entra App Additions (already applied)

These additions were made to the existing Entra app registration and do **not** break the SPA path:

1. **Redirect URI** (Web platform): `https://token.botframework.com/.auth/web/redirect`
2. **Pre-authorized clients** (on the `access_as_user` scope):
   - `1fec8e78-bce4-4aaf-ab1b-5451cc387264` — Teams desktop/mobile
   - `5e3ce6c0-2b1f-4285-8d4b-75ee78787346` — Teams web
   - `01b70e26-c61e-4287-9f0d-f07b4ed3b66a` — SPA client (already pre-authorized)

### To Test via Azure Portal Web Chat

```bash
# 1. Start the dev tunnel (need to install devtunnel if not already)
.\start-tunnel.bat

# 2. Update the bot endpoint to the tunnel URL
.\update-bot-endpoint.ps1 -TunnelUrl https://<your-tunnel-url>

# 3. Start the API
.\startapi.bat

# 4. Go to Azure Portal → FabricOboBot → Test in Web Chat
# 5. Type a message → sign-in card appears → complete sign-in → ask your question
```

### To Test in Teams (when you have sideload access)

```bash
# 1. Start the dev tunnel and API (same as above)
# 2. In Teams → Apps → Upload a custom app → Upload FabricOboBot.zip
#    The zip is at: teams-manifest/FabricOboBot.zip
# 3. Open the bot in Teams → it should SSO silently (no magic code needed)
```

### Bot Auth Flow Diagram

```
User in Teams                 Azure Bot Service              Your API (localhost:5180)
─────────────                 ─────────────────              ─────────────────────────
  │                                 │                                │
  │  "Show me my accounts"          │                                │
  │ ───────────────────────────────>│                                │
  │                                 │  POST /api/messages            │
  │                                 │ ──────────────────────────────>│
  │                                 │                                │
  │                                 │      UserTokenClient           │
  │                                 │      .GetUserTokenAsync()      │
  │                                 │                                │
  │                    [If no token cached]                           │
  │  <── Sign-in card ──────────────│<───────────────────────────────│
  │                                 │                                │
  │  SSO (Teams) or magic code      │                                │
  │ ───────────────────────────────>│                                │
  │                                 │  Token exchange event          │
  │                                 │ ──────────────────────────────>│
  │                                 │                                │
  │                    [Token now cached]                             │
  │  "You're signed in!"           │<───────────────────────────────│
  │ <───────────────────────────────│                                │
  │                                 │                                │
  │  "Show me my accounts"          │                                │
  │ ───────────────────────────────>│  POST /api/messages            │
  │                                 │ ──────────────────────────────>│
  │                                 │                                │
  │                                 │  GetUserToken ✓                │
  │                                 │  BotOboTokenService.Exchange   │
  │                                 │  FoundryAgentService.RunAgent  │
  │                                 │                                │
  │  "Here are your accounts..."    │<───────────────────────────────│
  │ <───────────────────────────────│                                │
```

---

## Path 3: Copilot Studio

**Status: 🔧 Ready to configure — zero code changes needed**

There are **two sub-options** for connecting Copilot Studio. Choose based on your preference:

### Path 3A: Copilot Studio via HTTP Action (Recommended)

This approach uses Copilot Studio's **HTTP Request** action to call your existing `POST /api/agent` endpoint directly. Copilot Studio handles user authentication and passes the user's token as a Bearer header. **Your existing `AgentController` handles everything — zero code changes.**

#### Prerequisites

- Microsoft Copilot Studio license (included with many M365 plans)
- Your API must be publicly accessible (dev tunnel or deployed)
- Copilot Studio must be in the same Entra tenant (`37f28838-...`)

#### Step-by-Step Setup

##### 1. Create a New Copilot

1. Go to [https://copilotstudio.microsoft.com](https://copilotstudio.microsoft.com)
2. Click **Create** → **New copilot**
3. Name it: **Fabric Data Assistant**
4. Select your environment
5. Click **Create**

##### 2. Configure Authentication

This is the critical step — Copilot Studio must authenticate users and pass their token to your API.

1. In your copilot, go to **Settings** → **Security** → **Authentication**
2. Select **Authenticate with Microsoft**
3. Click **Manual** (to configure custom OAuth)
4. Fill in:

   | Setting | Value |
   |---------|-------|
   | **Service provider** | `Azure Active Directory v2` |
   | **Client ID** | `21260626-6004-4699-a7d0-0773cbcd6192` |
   | **Client secret** | *(same as AzureAd:ClientSecret in appsettings.json)* |
   | **Tenant ID** | `37f28838-9a79-4b20-a28a-c7d8a85e4eda` |
   | **Scopes** | `api://21260626-6004-4699-a7d0-0773cbcd6192/access_as_user` |

5. Click **Save**
6. Note the **Token Variable** name (usually `System.User.AccessToken` or similar)

> **Important:** After saving, Copilot Studio will show you a redirect URI. You **must** add this redirect URI to your Entra app registration under **Authentication → Web → Redirect URIs**. The format is typically:
> ```
> https://token.botframework.com/.auth/web/redirect
> ```
> This is already added from the Teams bot setup. If Copilot Studio shows a different URI, add that too.

##### 3. Create a Topic with HTTP Action

1. Go to **Topics** → **Create** → **Topic** → **From blank**
2. Name it: **Ask Fabric**
3. Add a **Trigger phrase** node with phrases like:
   - "Show me my accounts"
   - "What are my balances"
   - "Query my data"
   - "Tell me about my clients"
4. Add a **Question** node:
   - **Prompt**: "What would you like to know about your data?"
   - **Save as**: Variable `UserQuestion` (type: string)
5. Add an **HTTP Request** action node:

   | Setting | Value |
   |---------|-------|
   | **Method** | `POST` |
   | **URL** | `https://<your-tunnel-or-deployed-url>/api/agent` |
   | **Headers** | `Content-Type`: `application/json` |
   | **Headers** | `Authorization`: `Bearer {System.User.AccessToken}` |
   | **Body** | See below |
   | **Response data type** | Create from sample (paste the sample below) |

   **Request Body:**
   ```json
   {
     "question": "{UserQuestion}"
   }
   ```

   **Response Sample** (paste in "Create from sample"):
   ```json
   {
     "status": "success",
     "assistantAnswer": "Here are your accounts...",
     "conversationId": "conv_abc123",
     "responseId": "resp_xyz789",
     "correlationId": "abc123def456",
     "toolEvidence": [
       {
         "toolName": "fabric_dataagent",
         "status": "completed"
       }
     ]
   }
   ```

6. Add a **Message** node after the HTTP action:
   - Text: `{Topic.httpResponse.assistantAnswer}`
   - (Where `httpResponse` is the variable name you gave to the HTTP response)

7. **Save** and **Publish** the topic

##### 4. Test in Copilot Studio

1. Click **Test** in the bottom-left panel
2. Type one of your trigger phrases
3. Copilot will authenticate you (first time) and then call your API
4. You should see the Foundry agent response with RLS-scoped data

##### 5. Deploy Your API

For production, your API must be publicly accessible:

- **Option A**: Deploy to Azure App Service
- **Option B**: Use a persistent Azure Dev Tunnel (for testing)

Update the URL in the HTTP action to match your deployed endpoint.

#### How the Token Flows (Path 3A)

```
Copilot Studio                         Your API
──────────────                         ────────
     │                                     │
     │  User authenticates in Copilot      │
     │  (OAuth to Entra → gets token       │
     │   for api://.../access_as_user)     │
     │                                     │
     │  POST /api/agent                    │
     │  Authorization: Bearer <user-token> │
     │  {"question": "Show my accounts"}   │
     │ ──────────────────────────────────> │
     │                                     │
     │          AgentController            │
     │          [Authorize] validates JWT  │
     │          ITokenAcquisition → OBO    │
     │          FoundryAgentService.Run()  │
     │                                     │
     │  200 OK                             │
     │  {"assistantAnswer": "..."}         │
     │ <────────────────────────────────── │
     │                                     │
     │  Shows response to user             │
```

> **Why this works with zero code changes**: Copilot Studio sends the exact same Bearer token format that the SPA sends. Your `AgentController` doesn't care whether the token came from MSAL.js in the browser or from Copilot Studio's OAuth — it's a valid JWT with the user's identity, and `ITokenAcquisition` performs the same OBO exchange.

---

### Path 3B: Copilot Studio via Bot Framework Skill

This approach connects Copilot Studio to your bot's `POST /api/messages` endpoint. Copilot Studio acts as the **parent bot**, and your `FabricOboBot` is registered as a **skill**. The auth flow goes through Bot Service OAuth (same as Teams).

#### When to Use This Instead of 3A

- You want Copilot Studio to embed the full bot experience (sign-in cards, typing indicators)
- You want to reuse the Bot Framework SSO flow rather than HTTP actions
- You want multi-turn conversation state managed by the bot

#### Step-by-Step Setup

##### 1. Register Your Bot as a Skill

1. Go to **Azure Portal** → **FabricOboBot** → **Settings** → **Configuration**
2. Under **Messaging endpoint**, ensure it points to your tunnel or deployed URL:
   ```
   https://<your-url>/api/messages
   ```
3. Under **Channels**, ensure your bot is accessible

##### 2. Add a Skill Manifest

Create a skill manifest file so Copilot Studio can discover your bot's capabilities.

Create `wwwroot/manifests/skill-manifest.json` in your project:

```json
{
  "$schema": "https://schemas.botframework.com/schemas/skills/v2.2/skill-manifest.json",
  "name": "FabricDataAssistant",
  "version": "1.0.0",
  "description": "Query Fabric data with OBO identity passthrough",
  "publisherName": "FabricObo",
  "endpoints": [
    {
      "name": "default",
      "protocol": "BotFrameworkV3",
      "description": "Default endpoint",
      "endpointUrl": "https://<your-url>/api/messages",
      "msAppId": "21260626-6004-4699-a7d0-0773cbcd6192"
    }
  ],
  "activities": {
    "message": {
      "type": "message",
      "description": "Send a natural language question about Fabric data"
    }
  }
}
```

##### 3. Connect in Copilot Studio

1. In Copilot Studio, go to **Settings** → **Skills**
2. Click **Add a skill**
3. Enter the skill manifest URL:
   ```
   https://<your-url>/manifests/skill-manifest.json
   ```
4. Copilot Studio will validate the connection
5. Create a **Topic** that triggers the skill:
   - Add a trigger phrase (e.g., "query fabric data")
   - Add a **Skill** action node → select your registered skill
   - The skill will handle authentication via the OAuth connection

##### 4. Entra App Configuration for Skills

When Copilot Studio calls your bot as a skill, the incoming requests come from Copilot Studio's app ID. You need to allow it:

1. Go to **Azure Portal** → **Entra ID** → **App registrations** → `21260626-...`
2. Go to **Expose an API**
3. Under **Authorized client applications**, add the Copilot Studio bot's app ID
   - You'll find this in Copilot Studio → Settings → Details → **Bot app ID**
4. Grant it the `access_as_user` scope

> **Note:** Path 3B reuses the same `FabricOboBot`, `BotOboTokenService`, and `FabricOboConnection` OAuth connection that you already set up for Teams (Path 2). No additional code changes are needed.

---

## Entra App Registration — Complete State

After all three paths are configured, your Entra app (`21260626-6004-4699-a7d0-0773cbcd6192`) should have:

### Authentication → Redirect URIs

| Platform | URI | Used By |
|----------|-----|---------|
| SPA | `http://localhost:5173` | Path 1 (SPA dev) |
| SPA | `http://localhost:3000` | Path 1 (SPA alt) |
| Web | `https://token.botframework.com/.auth/web/redirect` | Paths 2 & 3 (Bot/Copilot) |

### Expose an API

| Setting | Value |
|---------|-------|
| **Application ID URI** | `api://21260626-6004-4699-a7d0-0773cbcd6192` |
| **Scope** | `access_as_user` (Admin + User consent) |

### Pre-authorized Client Applications

| Client ID | App | Scope |
|-----------|-----|-------|
| `1fec8e78-bce4-4aaf-ab1b-5451cc387264` | Teams desktop/mobile | `access_as_user` |
| `5e3ce6c0-2b1f-4285-8d4b-75ee78787346` | Teams web | `access_as_user` |
| `01b70e26-c61e-4287-9f0d-f07b4ed3b66a` | SPA client | `access_as_user` |

### API Permissions (Delegated)

| API | Permission | Status |
|-----|-----------|--------|
| Microsoft Graph | `User.Read` | Granted |
| Azure AI Services (ai.azure.com) | `user_impersonation` | Granted |

---

## Troubleshooting

### Common Issues Across All Paths

| Error | Cause | Fix |
|-------|-------|-----|
| `AADSTS65001: consent required` | User hasn't consented to the downstream scope | Admin-consent the `ai.azure.com/.default` permission in Entra |
| `CapacityNotActive` | Fabric capacity is paused | Resume the Fabric capacity in Azure Portal |
| `Create assistant failed` | Foundry agent config issue | Check `Foundry:AgentName` matches a real agent in Foundry portal |
| `OBO token acquisition failed` | Client secret expired or wrong | Rotate secret in Entra, update `AzureAd:ClientSecret` and `Bot:MicrosoftAppPassword` |

### Path 2 (Teams Bot) Issues

| Error | Cause | Fix |
|-------|-------|-----|
| `401 Unauthorized` on `/api/messages` | Bot Framework auth — wrong AppId/Password | Check `Bot:MicrosoftAppId` and `Bot:MicrosoftAppPassword` match Entra |
| Sign-in card but no magic code | OAuth connection misconfigured | In Azure Portal → Bot → Configuration → check `FabricOboConnection` scopes |
| `Malformed identifier` from Foundry | Bot Framework conversation ID passed to Foundry | Ensure `GetConversationId()` returns `null` in `FabricOboBot.cs` |
| `502 Bad Gateway` in Web Chat | API not running or wrong endpoint | Check dev tunnel is running, bot endpoint URL is correct |

### Path 3 (Copilot Studio) Issues

| Error | Cause | Fix |
|-------|-------|-----|
| `401` from HTTP action | Token not being passed correctly | Ensure `Authorization: Bearer {System.User.AccessToken}` header is set |
| `AADSTS700024` | Copilot Studio isn't pre-authorized for `access_as_user` scope | Add Copilot Studio's app ID as a pre-authorized client in Entra |
| `403` from your API | JWT audience mismatch | Ensure Copilot Studio OAuth is configured with scope `api://21260626-.../access_as_user` |
| Empty response | HTTP action response parsing | Verify the response data type matches the actual JSON returned by `/api/agent` |

---

## Quick Reference: Config Locations

| What | Where | Notes |
|------|-------|-------|
| Entra app settings | `appsettings.json` → `AzureAd` | Shared by all paths |
| Bot Framework settings | `appsettings.json` → `Bot` | Paths 2 & 3B only |
| Foundry agent config | `appsettings.json` → `Foundry` | Shared by all paths |
| OAuth connection | Azure Portal → FabricOboBot → Configuration | Paths 2 & 3B only |
| Copilot Studio auth | Copilot Studio → Settings → Security → Auth | Path 3A only |
| Teams manifest | `teams-manifest/manifest.json` | Path 2 (Teams sideload) |
| Dev tunnel | `start-tunnel.bat` | Local testing for Paths 2 & 3 |
| Bot endpoint updater | `update-bot-endpoint.ps1` | Updates Azure Bot Service endpoint |

---

## Decision Guide: Which Path Should I Use?

```
Do you need a custom web UI?
  ├── Yes → Path 1: SPA
  └── No
        └── Do your users already live in Teams?
              ├── Yes → Path 2: Teams Bot
              └── No
                    └── Do you want a no-code/low-code setup?
                          ├── Yes → Path 3A: Copilot Studio via HTTP (simplest)
                          └── No → Path 3B: Copilot Studio via Bot Framework Skill
```

**You can run all three paths simultaneously** — they share the same Entra app, same API, same Foundry agent, and same Fabric data with the same RLS enforcement.
