# Fabric OBO Identity Passthrough POC

End-to-end proof-of-concept demonstrating user identity passthrough from a browser SPA, through a Web API, via Azure AI Foundry Agent Service (v2 Responses API), to Microsoft Fabric — with Row-Level Security (RLS) enforcement at the Fabric Warehouse layer.

**Choose your backend language:** The API is available in both .NET and Python.

## Identity Flow

```
User → SPA (MSAL.js) → Web API (JWT + OBO) → Foundry Named Agent → Fabric Data Agent → RLS
```

## Quick Start

1. **Follow the setup guide** — [docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md) covers everything from Entra ID app registrations through Fabric RLS configuration.

2. **Choose your API implementation:**

   ### Option A: .NET API (`dotnetapi/`)
   ```bash
   cd dotnetapi
   # Edit appsettings.json with your Tenant ID, Client ID, etc.
   dotnet restore
   dotnet run --launch-profile http
   ```

   ### Option B: Python API (`pythonapi/`)
   ```bash
   cd pythonapi
   python -m venv .venv && .venv\Scripts\activate
   pip install -r requirements.txt
   # Copy config: cp ../dotnetapi/appsettings.json ./appsettings.json
   # Or: cp .env.example .env  (and fill in values)
   python main.py
   ```

3. **Run the frontend**:
   ```bash
   cd client-app
   npm install
   npm run dev
   ```
4. **Test** — Open http://localhost:5173, sign in as User A or User B, and ask about accounts.

## Project Structure

```
├── dotnetapi/                    # .NET 9.0 Web API implementation
│   ├── FabricObo.csproj          # Project file
│   ├── Program.cs                # DI, auth, middleware setup
│   ├── appsettings.json          # Configuration
│   ├── web.config                # IIS hosting config
│   ├── Controllers/
│   │   ├── AgentController.cs    # POST /api/agent — main endpoint
│   │   └── ConfigController.cs   # GET /api/config — SPA config
│   ├── Services/
│   │   ├── FoundryAgentService.cs    # v2 Responses API implementation
│   │   ├── StubEntitlementService.cs # POC stub entitlement
│   │   └── ...
│   ├── Models/                   # Request/Response DTOs
│   └── Bot/                      # Teams / Copilot Studio bot
│
├── pythonapi/                    # Python (FastAPI) API implementation
│   ├── main.py                   # FastAPI app + routes
│   ├── config.py                 # Settings / configuration
│   ├── auth.py                   # JWT validation + OBO exchange
│   ├── foundry_agent_service.py  # v2 Responses API client
│   ├── entitlement_service.py    # Stub entitlement lookup
│   ├── bot_handler.py            # Bot Framework handler
│   ├── models.py                 # Request/Response DTOs
│   └── requirements.txt         # Python dependencies
│
├── client-app/                   # React SPA (MSAL.js + Vite)
│   └── src/
│       ├── authConfig.ts         # MSAL configuration
│       ├── App.tsx               # Login panel with test user buttons
│       └── Chat.tsx              # Chat UI with metadata display
│
├── Bot/                          # (moved into dotnetapi/)
├── scripts/                      # SQL scripts for Fabric RLS setup
├── teams-manifest/               # Teams app manifest
└── docs/                         # Architecture & setup guides
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
