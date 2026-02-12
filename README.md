# Fabric OBO Identity Passthrough POC

End-to-end proof-of-concept demonstrating user identity passthrough from a browser SPA, through an ASP.NET Core Web API, via Azure AI Foundry Agent Service (v2 Responses API), to Microsoft Fabric — with Row-Level Security (RLS) enforcement at the Fabric Warehouse layer.

## Identity Flow

```
User → SPA (MSAL.js) → ASP.NET Core API (JWT + OBO) → Foundry Named Agent → Fabric Data Agent → RLS
```

## Quick Start

1. **Follow the setup guide** — [docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md) covers everything from Entra ID app registrations through Fabric RLS configuration.
2. **Edit `appsettings.json`** — Fill in Tenant ID, Client ID, Client Secret, Foundry endpoint, Agent Name.
3. **Build and run the API**:
   ```bash
   dotnet restore
   dotnet run --launch-profile https
   ```
4. **Run the frontend**:
   ```bash
   cd client-app
   npm install
   npm run dev
   ```
5. **Test** — Open http://localhost:5173, sign in as User A or User B, and ask about accounts.

## Project Structure

```
FabricObo.csproj              # .NET 9.0 Web API project
Program.cs                    # DI, auth, middleware setup
appsettings.json              # Configuration
web.config                    # IIS hosting config

Controllers/
  AgentController.cs          # POST /api/agent — main endpoint

Services/
  IEntitlementService.cs      # Entitlement interface
  StubEntitlementService.cs   # POC stub (mirrors Fabric RLS mapping)
  IFoundryAgentService.cs     # Foundry agent interface
  FoundryAgentService.cs      # v2 Responses API implementation

Models/
  AgentRequest.cs             # Inbound request DTO
  AgentResponse.cs            # Response DTO with tool evidence
  EntitlementResult.cs        # Entitlement lookup result

client-app/                   # React SPA (MSAL.js + Vite)
  src/authConfig.ts           # MSAL configuration
  src/App.tsx                 # Login panel with test user buttons
  src/Chat.tsx                # Chat UI with metadata display

docs/
  ARCHITECTURE.md             # Architecture, sequence diagram, token flow
  SETUP_GUIDE.md              # Comprehensive step-by-step setup guide
  TECHNICAL_GUIDE.md          # Technical deep-dive for customer discussions
```

## Key Design Decisions

- **OBO (On-Behalf-Of)**: The API never impersonates users or fabricates identity. The Entra OBO flow produces a delegated token that carries the user's identity downstream.
- **Fabric RLS is the enforcement boundary**: The entitlement service is advisory. Even if bypassed, users only see data that Fabric RLS permits.
- **Named Agent pattern**: A pre-configured agent (`FabricOboAgent`) with Fabric Data Agent tool — users don't need to know tool configuration, just reference the agent.
- **No LLM-based authorization**: Agent prompts do not contain authorization logic. Data filtering is done by Fabric RLS, a deterministic infrastructure control.
- **Token audience chain**: SPA token (`aud=api://API_APP`) → OBO exchange → Foundry token (`aud=https://ai.azure.com`) → Fabric sees the user's identity.

## Documentation

| Document | Purpose |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design, sequence diagram, token flow detail |
| [SETUP_GUIDE.md](docs/SETUP_GUIDE.md) | Complete step-by-step setup and reproduction guide |
| [TECHNICAL_GUIDE.md](docs/TECHNICAL_GUIDE.md) | Technical deep-dive, gotchas, tips, and internals |
