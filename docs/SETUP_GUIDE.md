# Complete Setup Guide — Fabric OBO Identity Passthrough POC

This guide documents **every step** required to reproduce the end-to-end OBO identity passthrough for Fabric Row-Level Security (RLS) enforcement through Azure AI Foundry.

> **Architecture**: SPA → ASP.NET Core Web API → OBO Token Exchange → Azure AI Foundry Responses API → Named Agent with Fabric Data Agent Tool → Fabric Warehouse with RLS → Per-user filtered data.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Entra ID App Registrations](#2-entra-id-app-registrations)
3. [Test Users & MFA](#3-test-users--mfa)
4. [Fabric Workspace & Warehouse](#4-fabric-workspace--warehouse)
5. [Fabric RLS Configuration](#5-fabric-rls-configuration)
6. [Fabric Data Agent](#6-fabric-data-agent)
7. [Azure AI Foundry Project](#7-azure-ai-foundry-project)
8. [Fabric Connection in Foundry](#8-fabric-connection-in-foundry)
9. [Named Agent Creation](#9-named-agent-creation)
10. [RBAC Assignments](#10-rbac-assignments)
11. [.NET API Configuration & Build](#11-net-api-configuration--build)
12. [Frontend SPA Setup](#12-frontend-spa-setup)
13. [End-to-End Testing](#13-end-to-end-testing)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. Prerequisites

| Prerequisite | Detail |
|---|---|
| **Azure Subscription** | With ability to create AI Services and Fabric resources |
| **Microsoft Fabric Capacity** | F2 or higher (or Trial capacity) — required for Warehouses and Data Agents |
| **Entra ID Tenant** | With admin access to create app registrations and grant consent |
| **.NET 9 SDK** | [Download](https://dotnet.microsoft.com/download/dotnet/9.0) |
| **Node.js 18+** | For the SPA frontend |
| **Azure CLI** | `az login` for admin token operations |
| **Fabric Admin** | Portal access at https://app.fabric.microsoft.com |
| **Foundry Admin** | Portal access at https://ai.azure.com |

---

## 2. Entra ID App Registrations

You need **two** app registrations: one for the API (confidential client) and one for the SPA (public client).

### 2a. API App Registration (`FabricObo-API`)

1. **Azure Portal** → Entra ID → App registrations → **New registration**
2. **Name**: `FabricObo-API`
3. **Supported account types**: Single tenant
4. **Redirect URI**: Leave blank (Web API)
5. Click **Register**
6. Note the **Application (client) ID** — this is `{API_CLIENT_ID}`

#### Expose an API

1. Go to **Expose an API**
2. Click **Set** next to Application ID URI → accept the default `api://{API_CLIENT_ID}`
3. Click **+ Add a scope**:
   - **Scope name**: `access_as_user`
   - **Who can consent**: Admins and users
   - **Admin consent display name**: Access FabricObo API as user
   - **Admin consent description**: Allows the app to access the FabricObo API on behalf of the signed-in user
   - **State**: Enabled
4. Click **Add scope**

#### Client Secret

1. Go to **Certificates & secrets** → **+ New client secret**
2. **Description**: `FabricObo Secret`
3. **Expires**: 24 months (or your policy)
4. Click **Add**
5. **Copy the secret value immediately** — you cannot retrieve it later. Store as `{API_CLIENT_SECRET}`.

#### API Permissions (Critical for OBO)

1. Go to **API permissions** → **+ Add a permission**
2. Click **APIs my organization uses** → search for **Azure Machine Learning Services**
   - (**App ID**: `18a66f5f-dbdf-4c17-9dd7-1634712a9cbe`)
3. Select **Delegated permissions** → check **`user_impersonation`**
4. Click **Add permissions**
5. **Repeat** for **Microsoft Cognitive Services** (`7d312290-28c8-473c-a0ed-8e53749b6d6d`):
   - Delegated → `user_impersonation`
6. Click **Grant admin consent for {tenant}** → Confirm

> **Why both?** Azure AI Foundry services use both the Machine Learning Services (audience: `https://ai.azure.com`) and Cognitive Services APIs. The OBO exchange targets `https://ai.azure.com/.default`, which maps to the Machine Learning Services app. The Cognitive Services permission ensures the OBO token can also call Foundry's underlying cognitive endpoints.

#### Verify Admin Consent

After granting consent, you should see green checkmarks next to all three permissions:
- `Microsoft Graph` > `User.Read` (default)
- `Azure Machine Learning Services` > `user_impersonation` ✅ Granted
- `Microsoft Cognitive Services` > `user_impersonation` ✅ Granted

### 2b. SPA App Registration (`FabricObo-Client`)

1. **Azure Portal** → Entra ID → App registrations → **New registration**
2. **Name**: `FabricObo-Client`
3. **Supported account types**: Single tenant
4. **Redirect URI**: Select **Single-page application (SPA)**
   - **URI**: `http://localhost:5173`
5. Click **Register**
6. Note the **Application (client) ID** — this is `{SPA_CLIENT_ID}`

#### Additional Redirect URIs

1. Go to **Authentication** → under **Single-page application**
2. Add these URIs:
   - `http://localhost:3000`
   - `http://localhost:5173`
   - `http://localhost:5173/redirect` (if needed)

#### Enable Public Client

1. Under **Authentication** → **Advanced settings**
2. Set **Allow public client flows** → **Yes**
3. Click **Save**

> This enables the device code flow for command-line testing.

#### Add Native Client Redirect URIs (Optional — for device code flow testing)

1. Under **Authentication** → click **+ Add a platform** → **Mobile and desktop applications**
2. Check:
   - `https://login.microsoftonline.com/common/oauth2/nativeclient`
   - `msal{SPA_CLIENT_ID}://auth`
   - `http://localhost`
3. Click **Configure**

#### API Permissions

1. Go to **API permissions** → **+ Add a permission**
2. Click **My APIs** → select **FabricObo-API**
3. Check **`access_as_user`** (delegated)
4. Click **Add permissions**
5. Click **Grant admin consent for {tenant}** → Confirm

---

## 3. Test Users & MFA

Create at least two test users to prove RLS separation.

### Create Users

```powershell
# Using Microsoft Graph PowerShell or Azure Portal
# User A
$passwordProfile = @{
    password = "YourStrongPassword!123"
    forceChangePasswordNextSignIn = $false
}

# Create via Azure Portal: Entra ID → Users → + New user → Create new user
# User A: fabricusera@{yourdomain}.onmicrosoft.com
# User B: fabricuserb@{yourdomain}.onmicrosoft.com
```

Via **Azure Portal** → Entra ID → Users → **+ New user**:

| Field | User A | User B |
|---|---|---|
| User principal name | `fabricusera` | `fabricuserb` |
| Display name | Fabric UserA | Fabric UserB |
| Password | (your choice) | (your choice) |
| Usage location | United States | United States |

### Configure MFA

Each user must complete MFA registration (Foundry requires MFA):

1. Open an InPrivate/Incognito browser
2. Go to https://portal.azure.com
3. Sign in as the test user
4. Complete MFA setup (Microsoft Authenticator or SMS)
5. Repeat for each user

> **Important**: If MFA isn't configured, the device code flow and MSAL popup login will fail with `AADSTS50076` (MFA required).

---

## 4. Fabric Workspace & Warehouse

### Create Workspace

1. Go to https://app.fabric.microsoft.com
2. Click **Workspaces** → **+ New workspace**
3. **Name**: `{your-workspace-name}` (e.g., `FabricOboPOC`)
4. Assign a Fabric capacity (F2 or Trial)
5. Click **Create**
6. Note the **Workspace ID** from the URL: `https://app.fabric.microsoft.com/groups/{WORKSPACE_ID}`

### Add Users to Workspace

1. In the workspace, click **Manage access** (gear icon)
2. Add both test users as **Contributors** (or higher):
   - `fabricusera@{domain}`
   - `fabricuserb@{domain}`
3. Also add your API app's service principal:
   - Search for `FabricObo-API` → add as **Contributor**

> **Why Contributor?** The Data Agent requires at least Contributor access for identity passthrough to work.

### Create Warehouse

1. In the workspace, click **+ New item** → **Warehouse**
2. **Name**: `FabricOboPOC`
3. Click **Create**

### Create Tables & Insert Data

Open the warehouse SQL editor and run:

```sql
-- Accounts table
CREATE TABLE dbo.Accounts (
    AccountId   INT           NOT NULL PRIMARY KEY,
    AccountName NVARCHAR(100) NOT NULL,
    Balance     DECIMAL(18,2) NOT NULL,
    CreatedDate DATE          NOT NULL,
    Region      NVARCHAR(50)  NOT NULL,
    RepCode     NVARCHAR(20)  NOT NULL
);

-- Rep-to-user mapping table (used by RLS)
CREATE TABLE dbo.RepUserMapping (
    RepCode   NVARCHAR(20)  NOT NULL,
    UserEmail NVARCHAR(256) NOT NULL,
    PRIMARY KEY (RepCode, UserEmail)
);

-- Insert sample accounts
INSERT INTO dbo.Accounts VALUES
(1, 'Contoso Ltd',           250000.00, '2024-01-15', 'East',    'REP001'),
(2, 'Northwind Traders',     180000.00, '2024-03-22', 'West',    'REP001'),
(3, 'Adventure Works',       320000.00, '2024-06-10', 'Central', 'REP001'),
(4, 'Fabrikam Inc',          150000.00, '2024-09-05', 'East',    'REP001'),
(5, 'Tailspin Toys',         175000.00, '2025-01-25', 'West',    'REP002'),
(6, 'Wide World Importers',  420000.00, '2025-02-14', 'East',    'REP002'),
(7, 'Proseware Inc',          88000.00, '2025-05-01', 'Central', 'REP002');

-- Map users to rep codes
INSERT INTO dbo.RepUserMapping VALUES
('REP001', 'fabricusera@{yourdomain}.onmicrosoft.com'),
('REP002', 'fabricuserb@{yourdomain}.onmicrosoft.com');
```

> Replace `{yourdomain}` with your actual tenant domain.

---

## 5. Fabric RLS Configuration

### Create RLS Function & Policy

In the warehouse SQL editor, run:

```sql
-- Security predicate function
-- Admins (Fabric Workspace Admins) bypass RLS; regular users see only their data
CREATE FUNCTION dbo.fn_SecurityPredicate(@RepCode NVARCHAR(20))
RETURNS TABLE
WITH SCHEMABINDING
AS
RETURN
    SELECT 1 AS result
    WHERE
        -- Admin bypass: if the user is a workspace admin, show all rows
        IS_MEMBER('db_owner') = 1
        OR
        -- Normal user: only show rows matching their rep code
        @RepCode IN (
            SELECT rum.RepCode
            FROM dbo.RepUserMapping AS rum
            WHERE rum.UserEmail = USER_NAME()
        );

-- Apply RLS policy to the Accounts table
CREATE SECURITY POLICY dbo.AccountsFilter
    ADD FILTER PREDICATE dbo.fn_SecurityPredicate(RepCode) ON dbo.Accounts
    WITH (STATE = ON);
```

### Verify RLS Works

1. Open the warehouse SQL editor
2. Connect as your admin account:
   ```sql
   SELECT * FROM dbo.Accounts;  -- Should see all 7 rows (admin bypass)
   ```
3. To test as a specific user (you'll verify this through the Data Agent later)

---

## 6. Fabric Data Agent

### Create the Data Agent

1. In the Fabric workspace, click **+ New item** → **Data Agent** (under AI section)
2. **Name**: `fabricoboda` (or your preferred name)
3. Click **Create**

### Configure the Data Agent

1. Open the Data Agent
2. **Add a data source**:
   - Click **Add a data source** or **Select data**
   - Browse to your workspace → `FabricOboPOC` warehouse
   - Select both tables: `dbo.Accounts` and `dbo.RepUserMapping`
3. **Set instructions** (optional but recommended):
   ```
   You are a data assistant for account management.
   When asked about accounts, query the Accounts table.
   When asked about rep assignments, query the RepUserMapping table.
   Always provide account names, balances, and regions in your answers.
   ```
4. **Test** the Data Agent in the Fabric portal chat:
   - Ask "Give me a list of all accounts"
   - Verify the response returns data

### Publish the Data Agent

1. Click **Publish** in the top toolbar
2. Confirm the publish action
3. Wait for status to show **Published**

> **Critical**: The Data Agent **must be published** before it can be used from Foundry.

4. Note the **Artifact ID** from the URL:
   `https://app.fabric.microsoft.com/groups/{WORKSPACE_ID}/aiskills/{ARTIFACT_ID}`

---

## 7. Azure AI Foundry Project

### Create AI Services Resource

1. **Azure Portal** → Create a resource → search **Azure AI Services**
2. **Name**: `{your-ai-services-name}`
3. **Region**: Choose a region that supports Fabric Data Agent tool (e.g., East US 2)
4. **Pricing tier**: Standard S0
5. Click **Create**

### Create Foundry Project

1. Go to https://ai.azure.com
2. Click **+ New project**
3. **Project name**: `{your-project-name}`
4. **Hub/Resource**: Select the AI Services resource you created
5. Click **Create**
6. Note the **Project endpoint** from Settings → Overview:
   ```
   https://{account}.services.ai.azure.com/api/projects/{project}
   ```

### Deploy a Model

1. In the Foundry project, go to **Model catalog**
2. Deploy **gpt-4o** (or equivalent):
   - **Deployment name**: `chat4o` (or your choice)
   - **Deployment type**: Standard/Global Standard
3. Note the **deployment name** for configuration

---

## 8. Fabric Connection in Foundry

### Create the Connection

1. In the Foundry portal, go to your project
2. Navigate to **Management center** → **Connected resources**
3. Click **+ New connection** → Select **Microsoft Fabric**
4. Fill in:
   - **Workspace ID**: `{WORKSPACE_ID}` (from Step 4)
   - **Artifact ID**: `{ARTIFACT_ID}` (from Step 6 — the Data Agent ID)
5. Save the connection
6. Note the **connection name** (e.g., `fabricoboda`)
7. The full **Connection ID** will be:
   ```
   /subscriptions/{SUB_ID}/resourceGroups/{RG}/providers/Microsoft.CognitiveServices/accounts/{ACCOUNT}/projects/{PROJECT}/connections/{CONNECTION_NAME}
   ```

---

## 9. Named Agent Creation

### Create the Agent via REST API

The named agent must be created by an admin using a token obtained via Azure CLI.

```powershell
# Step 1: Sign in as a Foundry admin
az login --tenant {TENANT_ID}

# Step 2: Get a Foundry token
$token = az account get-access-token --scope "https://ai.azure.com/.default" --query accessToken -o tsv

# Step 3: Create the named agent
$endpoint = "https://{ACCOUNT}.services.ai.azure.com/api/projects/{PROJECT}"
$connectionId = "/subscriptions/{SUB_ID}/resourceGroups/{RG}/providers/Microsoft.CognitiveServices/accounts/{ACCOUNT}/projects/{PROJECT}/connections/{CONNECTION_NAME}"

$body = @{
    name = "FabricOboAgent"
    definition = @{
        kind = "prompt"
        model = "chat4o"
        instructions = "You are a helpful data analysis assistant. For any questions about data, accounts, sales, or reports, use the Fabric tool. Always provide clear, concise answers based on the data returned."
        tools = @(
            @{
                type = "fabric_dataagent_preview"
                fabric_dataagent_preview = @{
                    project_connections = @(
                        @{
                            project_connection_id = $connectionId
                        }
                    )
                }
            }
        )
        tool_choice = "required"
    }
} | ConvertTo-Json -Depth 10

# Save body to file (avoids PowerShell escaping issues)
$body | Out-File agent-body.json -Encoding UTF8

# Create the agent
curl.exe -s -X POST "$endpoint/openai/agents?api-version=2025-05-15-preview" `
    -H "Authorization: Bearer $token" `
    -H "Content-Type: application/json" `
    -d "@agent-body.json"
```

Expected response:
```json
{
  "id": "FabricOboAgent",
  "version": 1,
  "name": "FabricOboAgent",
  ...
}
```

### Verify the Agent

```powershell
# Test with admin token
$testBody = '{"input":"Give me a list of all accounts","tool_choice":"auto","agent":{"name":"FabricOboAgent","type":"agent_reference"}}'
$testBody | Out-File test-body.json -Encoding UTF8

curl.exe -s -X POST "$endpoint/openai/responses?api-version=2025-05-15-preview" `
    -H "Authorization: Bearer $token" `
    -H "Content-Type: application/json" `
    -d "@test-body.json"
```

You should see all 7 accounts (admin bypasses RLS).

---

## 10. RBAC Assignments

### On the AI Services Resource

Both test users need access to the Foundry project. In Azure Portal:

1. Go to the **AI Services resource** → **Access control (IAM)**
2. Add **role assignments** for each test user:

| User | Role | Scope |
|---|---|---|
| fabricusera | **Cognitive Services User** | AI Services resource |
| fabricuserb | **Cognitive Services User** | AI Services resource |
| fabricusera | **Azure AI Developer** | AI Services resource |
| fabricuserb | **Azure AI Developer** | AI Services resource |
| FabricObo-API (SP) | **Cognitive Services User** | AI Services resource |

> **Cognitive Services User** allows calling the Responses API. **Azure AI Developer** enables agent operations.

### On the Fabric Workspace

Both users need **Contributor** access (done in Step 4). Verify:

1. Go to the Fabric workspace → **Manage access**
2. Confirm both users have **Contributor** or higher
3. Confirm the API service principal has **Contributor** or higher

---

## 11. .NET API Configuration & Build

### appsettings.json

```json
{
  "AzureAd": {
    "Instance": "https://login.microsoftonline.com/",
    "TenantId": "{TENANT_ID}",
    "ClientId": "{API_CLIENT_ID}",
    "ClientSecret": "{API_CLIENT_SECRET}",
    "Audience": "api://{API_CLIENT_ID}",
    "Scopes": "access_as_user"
  },
  "Foundry": {
    "ProjectEndpoint": "https://{ACCOUNT}.services.ai.azure.com/api/projects/{PROJECT}",
    "ModelDeploymentName": "chat4o",
    "FabricConnectionId": "/subscriptions/{SUB_ID}/resourceGroups/{RG}/providers/Microsoft.CognitiveServices/accounts/{ACCOUNT}/projects/{PROJECT}/connections/{CONNECTION_NAME}",
    "AgentName": "FabricOboAgent",
    "ApiVersion": "2025-05-15-preview",
    "ResponseTimeoutSeconds": 180
  },
  "Cors": {
    "AllowedOrigins": [
      "http://localhost:3000",
      "http://localhost:5173"
    ]
  },
  "Logging": {
    "LogLevel": {
      "Default": "Information",
      "Microsoft.AspNetCore": "Warning",
      "Microsoft.Identity": "Warning",
      "FabricObo": "Debug"
    }
  },
  "AllowedHosts": "*"
}
```

### Update the Entitlement Stub

In `Services/StubEntitlementService.cs`, update the user map to match your test users:

```csharp
private static readonly Dictionary<string, (string RepCode, string Role)> UserMap = new(StringComparer.OrdinalIgnoreCase)
{
    ["fabricusera@{yourdomain}.onmicrosoft.com"] = ("REP001", "Advisor"),
    ["fabricuserb@{yourdomain}.onmicrosoft.com"] = ("REP002", "Advisor"),
};
```

### Build & Run

```powershell
cd d:\repos\fabricobo
dotnet build
dotnet run --launch-profile https
```

The API starts at `https://localhost:7180`.

---

## 12. Frontend SPA Setup

### Install & Run

```powershell
cd d:\repos\fabricobo\client-app
npm install
npm run dev
```

Opens at `http://localhost:5173`.

### Configure the SPA

Edit `client-app/src/authConfig.ts` with your values:
- `{TENANT_ID}` — your Entra tenant ID
- `{SPA_CLIENT_ID}` — the SPA app client ID
- `{API_CLIENT_ID}` — the API app client ID

---

## 13. End-to-End Testing

### Test via the SPA

1. Open `http://localhost:5173` in a browser
2. Click **Sign in as User A** → sign in as `fabricusera@{domain}`
3. Ask: "Give me a list of all accounts"
4. **Expected**: 4 accounts (Contoso Ltd, Northwind Traders, Adventure Works, Fabrikam Inc) — all REP001
5. Click **Sign Out** → **Sign in as User B** → sign in as `fabricuserb@{domain}`
6. Ask the same question
7. **Expected**: 3 accounts (Tailspin Toys, Wide World Importers, Proseware Inc) — all REP002

### Test via curl (Alternative)

```powershell
# Get a token for User A (device code flow)
.\get-foundry-token.ps1 -User fabricusera
# Sign in at the URL shown

# Use the saved token
$tokenA = Get-Content token-fabricusera.txt -Raw
$body = '{"question":"Give me a list of all accounts"}'
$body | Out-File e2e-body.json -Encoding UTF8 -NoNewline

curl.exe -sk -X POST "https://localhost:7180/api/agent" `
    -H "Authorization: Bearer $tokenA" `
    -H "Content-Type: application/json" `
    -d "@e2e-body.json"
```

### Expected Results

| User | RepCode | Accounts | Count |
|---|---|---|---|
| fabricusera | REP001 | Contoso Ltd, Northwind Traders, Adventure Works, Fabrikam Inc | 4 |
| fabricuserb | REP002 | Tailspin Toys, Wide World Importers, Proseware Inc | 3 |

---

## 14. Troubleshooting

### Common Issues

| Symptom | Cause | Fix |
|---|---|---|
| `AADSTS50076` — MFA required | User hasn't completed MFA setup | Sign in to portal.azure.com as the user and complete MFA |
| `AADSTS65001` — consent needed | API permissions not consented | Go to API app → API permissions → Grant admin consent |
| `401 Unauthorized` from API | Token has wrong audience | Ensure SPA requests token with scope `api://{API_CLIENT_ID}/access_as_user` |
| `Create assistant failed` | Token acquired by wrong client app | The token reaching Foundry must have `aud=https://ai.azure.com` — this comes from the OBO exchange in the API, not from direct SPA tokens |
| `Tool execution failed` | Fabric connection not set up correctly, or Data Agent not published | Verify connection in Foundry portal, verify Data Agent is published |
| `No data returned` | RLS function or mapping incorrect | Check `RepUserMapping` table has correct email addresses (case-sensitive for some tenants) |
| OBO token acquisition fails | Missing `user_impersonation` permission on Azure ML Services | Add delegated permission + grant admin consent |
| Agent not found | Named agent not created or wrong name | Re-create with `POST /openai/agents` using admin token |

### Key Debugging Tips

1. **Decode JWT tokens** to check `aud`, `appid`, `scp`, and `upn` claims:
   ```powershell
   $token = "eyJ0eXA..."
   $payload = $token.Split('.')[1]
   switch ($payload.Length % 4) { 2 { $payload += '==' } 3 { $payload += '=' } }
   [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($payload)) | ConvertFrom-Json
   ```

2. **Token audience expectations**:
   - SPA → API token: `aud = api://{API_CLIENT_ID}`
   - API → Foundry OBO token: `aud = https://ai.azure.com`
   - The SPA token is **not** a Foundry token — it goes through OBO

3. **Test Fabric Data Agent directly** in the Fabric portal:
   - Sign in as a test user
   - Open the Data Agent
   - Ask "Give me list of accounts"
   - This confirms Fabric-side access and RLS independently

4. **Check admin consent status** via Graph API:
   ```powershell
   $graphToken = az account get-access-token --resource https://graph.microsoft.com -o tsv --query accessToken
   Invoke-RestMethod -Uri "https://graph.microsoft.com/v1.0/servicePrincipals?`$filter=appId eq '{API_CLIENT_ID}'&`$select=id" `
       -Headers @{Authorization="Bearer $graphToken"} | Select-Object -ExpandProperty value
   ```

---

## Reference Values (Your Deployment)

> **Note:** Replace the placeholder values below with your own Entra ID app registrations,
> Foundry project details, and Fabric resources. See `appsettings.example.json` for the
> configuration file template.

| Item | Value |
|---|---|
| Tenant ID | `<YOUR_TENANT_ID>` |
| Tenant Domain | `<YOUR_TENANT>.onmicrosoft.com` |
| API App ID | `<YOUR_API_CLIENT_ID>` |
| SPA App ID | `<YOUR_SPA_CLIENT_ID>` |
| API Scope | `api://<YOUR_API_CLIENT_ID>/access_as_user` |
| Foundry Account | `<YOUR_FOUNDRY_ACCOUNT>` |
| Foundry Project | `<YOUR_FOUNDRY_PROJECT>` |
| Project Endpoint | `https://<YOUR_FOUNDRY_ACCOUNT>.services.ai.azure.com/api/projects/<YOUR_FOUNDRY_PROJECT>` |
| Model Deployment | `chat4o` (GPT-4o) |
| Named Agent | `<YOUR_AGENT_NAME>` |
| Fabric Workspace | `<YOUR_FABRIC_WORKSPACE>` |
| Warehouse | `<YOUR_WAREHOUSE_NAME>` |
| Data Agent | `<YOUR_DATA_AGENT_NAME>` |
| Connection ID | `/subscriptions/<SUB_ID>/resourceGroups/<RG>/providers/Microsoft.CognitiveServices/accounts/<ACCOUNT>/projects/<PROJECT>/connections/<CONNECTION>` |
| User A | `<usera>@<YOUR_TENANT>.onmicrosoft.com` (REP001, test user) |
| User B | `<userb>@<YOUR_TENANT>.onmicrosoft.com` (REP002, test user) |
| API Version | `2025-05-15-preview` |
