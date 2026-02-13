# Teams & Copilot Studio Integration Guide

This guide explains how to use the Fabric OBO agent from **Microsoft Teams** and **Microsoft Copilot Studio** instead of (or alongside) the SPA frontend.

## Architecture Overview

The SPA and Bot paths share the same backend services — only the **token acquisition** differs:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        FabricObo Backend                                │
│                                                                         │
│  SPA Path                              Bot Path                         │
│  ────────                              ────────                         │
│  POST /api/agent                       POST /api/messages               │
│  ┌──────────────┐                      ┌──────────────────┐             │
│  │AgentController│                      │BotController     │             │
│  │  [Authorize]  │                      │  (Bot Framework) │             │
│  └──────┬───────┘                      └──────┬───────────┘             │
│         │                                      │                        │
│  ITokenAcquisition                     BotOboTokenService               │
│  (Microsoft.Identity.Web)              (MSAL direct OBO)                │
│         │                                      │                        │
│         └──────────────┬───────────────────────┘                        │
│                        │                                                │
│              ┌─────────▼──────────┐                                     │
│              │ IFoundryAgentService│                                     │
│              │ (shared)            │                                     │
│              └─────────┬──────────┘                                     │
│                        │ OBO Token (user identity)                      │
│                        ▼                                                │
│              Azure AI Foundry Responses API                             │
│                        │                                                │
│                        ▼                                                │
│              Microsoft Fabric (RLS enforced per user)                   │
└─────────────────────────────────────────────────────────────────────────┘
```

The key insight: **The OBO token carries the user's identity regardless of whether it came from a SPA or a bot.** Fabric RLS enforcement is identical in both paths.

---

## Option 1: Microsoft Teams Bot

### Prerequisites
- Existing Entra app registration (already configured for the SPA path)
- Azure Bot Service resource
- Microsoft Teams admin access

### Step 1: Update the Entra App Registration

Your existing app registration (`21260626-...`) needs a few additions for the bot:

1. Go to **Azure Portal → Entra ID → App registrations → your app**

2. **Add Bot Framework redirect URIs** under **Authentication → Web**:
   ```
   https://token.botframework.com/.auth/web/redirect
   ```

3. **Expose the API** (should already be done):
   - Application ID URI: `api://21260626-6004-4699-a7d0-0773cbcd6192`
   - Scope: `access_as_user`

4. **Add API permissions** (if not already present):
   - Microsoft Graph → `User.Read` (delegated)
   - Microsoft Graph → `openid`, `profile`, `email` (delegated)

5. **Under "Expose an API" → Authorized client applications**, add:
   ```
   1fec8e78-bce4-4aaf-ab1b-5451cc387264  (Teams desktop/mobile)
   5e3ce6c0-2b1f-4285-8d4b-75ee78787346  (Teams web)
   ```
   This enables Teams SSO — users don't see a login popup.

### Step 2: Create Azure Bot Service Resource

1. Go to **Azure Portal → Create a resource → Azure Bot**

2. Configure:
   | Setting | Value |
   |---------|-------|
   | Bot handle | `FabricOboBot` |
   | Pricing | F0 (free for dev) |
   | Microsoft App ID | Use existing: `21260626-6004-4699-a7d0-0773cbcd6192` |
   | App type | Single Tenant |
   | Tenant ID | `37f28838-9a79-4b20-a28a-c7d8a85e4eda` |

3. After creation, go to **Settings → Configuration**:
   - **Messaging endpoint**: `https://your-api-domain.com/api/messages`
   - For local dev, use [Dev Tunnels](https://learn.microsoft.com/azure/developer/dev-tunnels/overview) or ngrok

### Step 3: Configure OAuth Connection (Critical!)

This is the step that replaces the SPA's MSAL.js login:

1. In Azure Bot Service → **Settings → Configuration → OAuth Connection Settings**

2. Click **Add Setting** and configure:
   | Setting | Value |
   |---------|-------|
   | Name | `FabricOboConnection` |
   | Service Provider | `Azure Active Directory v2` |
   | Client ID | `21260626-6004-4699-a7d0-0773cbcd6192` |
   | Client Secret | (your client secret) |
   | Tenant ID | `37f28838-9a79-4b20-a28a-c7d8a85e4eda` |
   | Scopes | `api://21260626-6004-4699-a7d0-0773cbcd6192/access_as_user` |

3. Click **Test Connection** to verify. You should get a token back.

> **Why this works**: The OAuth connection asks for a token scoped to your API (`access_as_user`). This is the **same token** the SPA acquires via MSAL.js. The bot then passes it to `BotOboTokenService`, which performs the same OBO exchange to get a Foundry-scoped token.

### Step 4: Enable Teams Channel

1. In Azure Bot Service → **Channels → Microsoft Teams**
2. Click **Apply** (accept terms)
3. The bot is now reachable from Teams

### Step 5: Create Teams App Manifest

Create a `teams-manifest/manifest.json`:

```json
{
  "$schema": "https://developer.microsoft.com/en-us/json-schemas/teams/v1.17/MicrosoftTeams.schema.json",
  "manifestVersion": "1.17",
  "version": "1.0.0",
  "id": "21260626-6004-4699-a7d0-0773cbcd6192",
  "developer": {
    "name": "Your Org",
    "websiteUrl": "https://your-domain.com",
    "privacyUrl": "https://your-domain.com/privacy",
    "termsOfUseUrl": "https://your-domain.com/terms"
  },
  "name": {
    "short": "Fabric Data Assistant",
    "full": "Fabric Data Assistant with OBO"
  },
  "description": {
    "short": "Query your Fabric data securely",
    "full": "Ask questions about your data. Uses On-Behalf-Of flow to ensure you only see data you're authorized for via Fabric Row-Level Security."
  },
  "icons": {
    "outline": "outline.png",
    "color": "color.png"
  },
  "accentColor": "#4F6BED",
  "bots": [
    {
      "botId": "21260626-6004-4699-a7d0-0773cbcd6192",
      "scopes": ["personal", "team", "groupChat"],
      "supportsFiles": false,
      "isNotificationOnly": false
    }
  ],
  "permissions": ["identity", "messageTeamMembers"],
  "validDomains": ["token.botframework.com", "your-api-domain.com"],
  "webApplicationInfo": {
    "id": "21260626-6004-4699-a7d0-0773cbcd6192",
    "resource": "api://21260626-6004-4699-a7d0-0773cbcd6192"
  }
}
```

> **Note**: The `webApplicationInfo` section is what enables **Teams SSO**. It tells Teams to silently acquire a token for your app's resource URI.

### Step 6: Install in Teams

1. Zip the manifest + icons into a `.zip` file
2. In Teams → **Apps → Upload a custom app**
3. Select the zip
4. Start chatting! The bot will authenticate via SSO

### Step 7: Local Development with Dev Tunnels

For testing locally:

```bash
# Using VS Code Dev Tunnels extension or CLI
devtunnel create --allow-anonymous
devtunnel port create -p 5000
devtunnel host
```

Update the Bot Service messaging endpoint to your tunnel URL:
```
https://<tunnel-id>.devtunnels.ms/api/messages
```

---

## Option 2: Copilot Studio

Copilot Studio can connect to your bot in **two ways**:

### Option 2a: Direct HTTP Action (Simplest — No Bot Changes Needed)

Call your existing SPA API endpoint directly from Copilot Studio:

1. In Copilot Studio → **Settings → Security → Authentication**
   - Select **Manual** (not "No authentication")
   - Provider: **Azure Active Directory v2**
   - Client ID: Create a NEW Entra app registration for Copilot Studio
   - Scopes: `api://21260626-6004-4699-a7d0-0773cbcd6192/access_as_user`
   - Token exchange URL (for SSO): `api://21260626-6004-4699-a7d0-0773cbcd6192/botid-{CopilotStudioBotId}`

2. Create a **Topic** with an **HTTP Request** action:
   | Setting | Value |
   |---------|-------|
   | URL | `https://your-api-domain.com/api/agent` |
   | Method | POST |
   | Headers | `Authorization: Bearer {System.User.AccessToken}` |
   | Body | `{"question": "{UserQuestion}"}` |

3. Parse the JSON response and display `assistantAnswer` to the user.

> **Key**: Copilot Studio's `System.User.AccessToken` contains a user token for your API scope. Your existing `[Authorize]` + OBO flow handles everything — **zero code changes** to your API.

### Option 2b: Bot Framework Connector (Use the New Bot Endpoint)

Connect Copilot Studio to your bot via the Bot Framework:

1. In Copilot Studio → **Settings → Agent Transfers → Bot Framework Skill**

2. Enter the skill manifest URL for your bot (you'll need to create a skill manifest)

3. Copilot Studio sends messages to your `/api/messages` endpoint via Bot Framework protocol

4. The bot handles auth, OBO, and Foundry calls as implemented above

---

## Configuration Summary

### appsettings.json — Bot Section

```json
"Bot": {
    "MicrosoftAppId": "<same as AzureAd:ClientId>",
    "MicrosoftAppPassword": "<same as AzureAd:ClientSecret>",
    "MicrosoftAppTenantId": "<same as AzureAd:TenantId>",
    "MicrosoftAppType": "SingleTenant",
    "OAuthConnectionName": "FabricOboConnection"
}
```

The `OAuthConnectionName` must match what you configured in Azure Bot Service.

---

## How the Auth Flow Works (Detailed)

### SPA Path (existing)
```
User → Browser → MSAL.js login → token for api://{ClientId}/access_as_user
     → POST /api/agent (Bearer token)
     → AgentController validates JWT
     → ITokenAcquisition.GetAccessTokenForUserAsync (OBO exchange)
     → Foundry token with user identity
     → FoundryAgentService → Fabric (RLS applied)
```

### Teams Bot Path (new)
```
User → Teams message → Bot Framework → POST /api/messages
     → FabricOboBot receives activity
     → UserTokenClient.GetUserTokenAsync (Bot OAuth connection)
       → Bot Service exchanges Teams SSO token for api://{ClientId}/access_as_user token
     → BotOboTokenService.ExchangeTokenAsync (MSAL OBO exchange)
     → Foundry token with user identity
     → FoundryAgentService → Fabric (RLS applied)
```

### Copilot Studio Direct Path (no code changes)
```
User → Copilot Studio → Entra auth (gets user token for API scope)
     → HTTP Action: POST /api/agent (Bearer token)
     → AgentController validates JWT (same as SPA)
     → ITokenAcquisition OBO exchange (same as SPA)
     → Foundry token with user identity
     → FoundryAgentService → Fabric (RLS applied)
```

---

## Troubleshooting

### "No token cached — sending sign-in card"
- The OAuth connection is not returning a token silently
- Verify Teams SSO is configured (`webApplicationInfo` in manifest)
- Check that the OAuth connection's scopes match your API

### "OBO exchange failed — user consent or interaction required"
- The user hasn't consented to Foundry access
- Admin consent may be needed: go to Entra → API permissions → Grant admin consent

### "Token exchange failed" (MSAL error)
- Check that `AzureAd:ClientId` and `AzureAd:ClientSecret` are correct
- Verify the OAuth connection test works in Azure Bot Service
- Check AADSTS error code in logs for specific guidance

### Bot Framework authentication errors (401/403)
- Ensure `Bot:MicrosoftAppId` matches the bot registration
- For single-tenant, verify `Bot:MicrosoftAppTenantId` is set
- Messaging endpoint must use HTTPS

---

## Security Considerations

1. **RLS enforcement is identical**: Both paths produce the same OBO token carrying the user's identity. Fabric RLS doesn't know or care whether the request originated from a SPA or a bot.

2. **Token scope chain**: Teams SSO → `access_as_user` → OBO → `https://ai.azure.com/.default` → Fabric. The user's identity flows through every hop.

3. **Single-tenant vs Multi-tenant**: Using `SingleTenant` means only users in your tenant can use the bot. For partners/customers, use `MultiTenant` and handle tenant validation.

4. **No service-to-service shortcuts**: The bot does NOT use client credentials flow. Every request flows through the user's identity via OBO. This is by design — it's the whole point of the architecture.
