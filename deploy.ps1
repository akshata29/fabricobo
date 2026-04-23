# FabricObo - Deploy to Azure App Service (Single Container)
#
# Builds a single Docker image containing the Python FastAPI backend and
# the compiled React SPA, pushes it to an existing Azure Container Registry,
# and deploys it as an Azure App Service Web App (Linux container).
#
# Prerequisites:
#   - Docker Desktop running
#   - Azure CLI (az) installed and logged in (or this script will prompt)
#   - pythonapi/.env populated from pythonapi/.env.example
#   - ACR already exists (use -AcrName to specify it)
#
# Usage:
#   .\deploy.ps1 `
#       -ResourceGroup  "my-rg" `
#       -AcrName        "myacr" `
#       -AppServicePlan "fabricobo-plan" `
#       -WebAppName     "fabricobo-app"
#
# Optional:
#   -Location   "eastus"   (default)
#   -ImageTag   "latest"   (default)
#   -Sku        "B2"       (default; use B1/B2/B3/P1v3 etc.)

param(
    [Parameter(Mandatory=$true)]  [string] $ResourceGroup,
    [Parameter(Mandatory=$true)]  [string] $AcrName,
    [Parameter(Mandatory=$true)]  [string] $AppServicePlan,
    [Parameter(Mandatory=$true)]  [string] $WebAppName,
    [Parameter(Mandatory=$false)] [string] $Location  = "eastus",
    [Parameter(Mandatory=$false)] [string] $ImageTag  = "latest",
    [Parameter(Mandatory=$false)] [string] $Sku       = "B2"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "FabricObo - Azure App Service Deployment" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ── Check Azure login ─────────────────────────────────────────
Write-Host "[1/8] Checking Azure login..." -ForegroundColor Yellow
$account = az account show 2>$null | ConvertFrom-Json
if (-not $account) {
    Write-Host "  Not logged in. Starting az login..." -ForegroundColor Red
    az login
    $account = az account show | ConvertFrom-Json
}
Write-Host "  Logged in: $($account.user.name) ($($account.name))" -ForegroundColor Green
Write-Host ""

# ── Ensure resource group ─────────────────────────────────────
Write-Host "[2/8] Checking resource group '$ResourceGroup'..." -ForegroundColor Yellow
if ((az group exists --name $ResourceGroup) -eq "false") {
    Write-Host "  Creating resource group in $Location..." -ForegroundColor Yellow
    az group create --name $ResourceGroup --location $Location | Out-Null
}
Write-Host "  Resource group: OK" -ForegroundColor Green
Write-Host ""

# ── Build and push Docker image ───────────────────────────────
Write-Host "[3/8] Building Docker image..." -ForegroundColor Yellow
$ImageName = "fabricobo"
$FullImage = "$AcrName.azurecr.io/${ImageName}:${ImageTag}"

az acr login --name $AcrName
if ($LASTEXITCODE -ne 0) { Write-Error "ACR login failed"; exit 1 }

docker build -f "$ScriptDir\Dockerfile" -t "${ImageName}:${ImageTag}" "$ScriptDir"
if ($LASTEXITCODE -ne 0) { Write-Error "Docker build failed"; exit 1 }

Write-Host "  Pushing image to ACR: $FullImage" -ForegroundColor Yellow
docker tag "${ImageName}:${ImageTag}" $FullImage
docker push $FullImage
if ($LASTEXITCODE -ne 0) { Write-Error "Docker push failed"; exit 1 }
Write-Host "  Image pushed: $FullImage" -ForegroundColor Green
Write-Host ""

# ── App Service Plan ──────────────────────────────────────────
Write-Host "[4/8] Checking App Service Plan '$AppServicePlan'..." -ForegroundColor Yellow
$planJson = az appservice plan show --name $AppServicePlan --resource-group $ResourceGroup 2>$null
if (-not $planJson) {
    Write-Host "  Creating App Service Plan (Linux, $Sku)..." -ForegroundColor Yellow
    az appservice plan create `
        --name           $AppServicePlan `
        --resource-group $ResourceGroup `
        --is-linux `
        --sku            $Sku `
        --location       $Location | Out-Null
    Write-Host "  Plan created." -ForegroundColor Green
} else {
    Write-Host "  Plan exists." -ForegroundColor Green
}
Write-Host ""

# ── Managed identity for ACR pull ────────────────────────────
# Admin passwords are unreliable in App Service; use system-assigned
# managed identity with AcrPull role instead.
Write-Host "[5/8] Configuring managed identity for ACR pull..." -ForegroundColor Yellow
$principalId = (az webapp identity assign `
    --name           $WebAppName `
    --resource-group $ResourceGroup `
    --query principalId -o tsv).Trim()

$acrId = (az acr show --name $AcrName --resource-group $ResourceGroup --query id -o tsv).Trim()

# Assign AcrPull (idempotent — harmless if already assigned)
$existingRole = az role assignment list --assignee $principalId --role AcrPull --scope $acrId --query "[0].id" -o tsv 2>$null
if (-not $existingRole) {
    az role assignment create --assignee $principalId --role AcrPull --scope $acrId | Out-Null
    Write-Host "  AcrPull role assigned to managed identity." -ForegroundColor Green
} else {
    Write-Host "  AcrPull role already assigned." -ForegroundColor Green
}
Write-Host ""

# ── Create / update Web App ───────────────────────────────────
$WebAppUrl = "https://$WebAppName.azurewebsites.net"
Write-Host "[6/8] Checking Web App '$WebAppName'..." -ForegroundColor Yellow
$webAppJson = az webapp show --name $WebAppName --resource-group $ResourceGroup 2>$null
if (-not $webAppJson) {
    Write-Host "  Creating Web App..." -ForegroundColor Yellow
    az webapp create `
        --resource-group $ResourceGroup `
        --plan           $AppServicePlan `
        --name           $WebAppName `
        --deployment-container-image-name $FullImage | Out-Null
    Write-Host "  Web App created." -ForegroundColor Green
} else {
    Write-Host "  Web App exists." -ForegroundColor Green
}

# Configure container image and enable managed identity ACR pull
$webAppResourceId = (az webapp show `
    --name           $WebAppName `
    --resource-group $ResourceGroup `
    --query id -o tsv).Trim()

az webapp config container set `
    --name           $WebAppName `
    --resource-group $ResourceGroup `
    --docker-custom-image-name  $FullImage `
    --docker-registry-server-url "https://$AcrName.azurecr.io" | Out-Null

# Enable acrUseManagedIdentityCreds so no password is needed
az rest `
    --method PATCH `
    --uri    "https://management.azure.com${webAppResourceId}?api-version=2022-03-01" `
    --body   '{"properties":{"siteConfig":{"acrUseManagedIdentityCreds":true}}}' `
    --headers "Content-Type=application/json" | Out-Null

Write-Host "  Container registry configured (managed identity)." -ForegroundColor Green
Write-Host ""

# ── Build app settings from pythonapi/.env ────────────────────
Write-Host "[7/8] Applying app settings..." -ForegroundColor Yellow

$settingsHash = [ordered]@{}

# App Service must know which port the container listens on
$settingsHash["WEBSITES_PORT"] = "8000"

$envFilePath = Join-Path $ScriptDir "pythonapi\.env"
if (Test-Path $envFilePath) {
    foreach ($line in [System.IO.File]::ReadAllLines($envFilePath)) {
        # Skip blank lines and comments
        if ($line -match '^\s*$' -or $line -match '^\s*#') { continue }

        $eqIdx = $line.IndexOf('=')
        if ($eqIdx -lt 1) { continue }

        $k = $line.Substring(0, $eqIdx).Trim()
        $v = $line.Substring($eqIdx + 1).Trim()

        # Strip surrounding single or double quotes
        if ($v.Length -ge 2) {
            if (($v[0] -eq '"'  -and $v[-1] -eq '"') -or
                ($v[0] -eq "'" -and $v[-1] -eq "'")) {
                $v = $v.Substring(1, $v.Length - 2)
            }
        }

        # WEBSITES_PORT is already set; don't overwrite from .env
        if ($k -eq "WEBSITES_PORT") { continue }

        $settingsHash[$k] = $v
    }
    Write-Host "  Loaded $($settingsHash.Count - 1) settings from pythonapi\.env" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "  WARNING: pythonapi\.env not found." -ForegroundColor Yellow
    Write-Host "  Copy pythonapi\.env.example -> pythonapi\.env, fill in values, then re-run." -ForegroundColor Yellow
    Write-Host "  Only minimal settings (WEBSITES_PORT) will be applied now." -ForegroundColor Yellow
    Write-Host ""
}

# ── Override deployment-specific values ───────────────────────
# CORS: frontend and API share the same origin, but set explicitly
$settingsHash["CORS_ALLOWED_ORIGINS"] = $WebAppUrl
Write-Host "  CORS_ALLOWED_ORIGINS  = $WebAppUrl" -ForegroundColor Gray

# FABRIC_DIRECT_API_PUBLIC_URL must point to the deployed URL so Foundry
# can call back to /mcp on this container.
if ($settingsHash.Contains("FABRIC_DIRECT_DATA_AGENT_URL") -and
    $settingsHash["FABRIC_DIRECT_DATA_AGENT_URL"] -ne "") {
    $settingsHash["FABRIC_DIRECT_API_PUBLIC_URL"] = $WebAppUrl
    Write-Host "  FABRIC_DIRECT_API_PUBLIC_URL = $WebAppUrl" -ForegroundColor Gray
}

# ── Write settings to a temp JSON and apply via ARM REST API ──
# Using az rest + PUT so that complex JSON values are never subject
# to shell argument escaping issues.
$tempFile = Join-Path $env:TEMP "fabricobo-appsettings-$(Get-Random).json"
try {
    @{ properties = $settingsHash } | ConvertTo-Json -Depth 3 |
        Set-Content -Path $tempFile -Encoding UTF8

    $resourceId = (az webapp show `
        --name           $WebAppName `
        --resource-group $ResourceGroup `
        --query id -o tsv).Trim()

    az rest `
        --method PUT `
        --uri    "https://management.azure.com${resourceId}/config/appsettings?api-version=2022-03-01" `
        --body   "@$tempFile" | Out-Null

    Write-Host "  $($settingsHash.Count) app settings applied." -ForegroundColor Green
} finally {
    Remove-Item $tempFile -ErrorAction SilentlyContinue
}
Write-Host ""

# ── Enable CI/CD webhook + restart ────────────────────────────
Write-Host "[8/8] Enabling CD webhook and restarting Web App..." -ForegroundColor Yellow
az webapp deployment container config `
    --name           $WebAppName `
    --resource-group $ResourceGroup `
    --enable-cd      true | Out-Null

az webapp restart --name $WebAppName --resource-group $ResourceGroup | Out-Null
Write-Host "  Web App restarted." -ForegroundColor Green
Write-Host ""

# ── Summary ───────────────────────────────────────────────────
Write-Host "========================================" -ForegroundColor Green
Write-Host "DEPLOYMENT COMPLETE" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Web App URL : $WebAppUrl" -ForegroundColor Cyan
Write-Host "  Image       : $FullImage" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Populate pythonapi\.env and re-run if you skipped that step." -ForegroundColor White
Write-Host "  2. In Entra ID, add '$WebAppUrl' as a Redirect URI (SPA type)" -ForegroundColor White
Write-Host "     on the SPA app registration." -ForegroundColor White
Write-Host "  3. Stream live logs:" -ForegroundColor White
Write-Host "     az webapp log tail --name $WebAppName --resource-group $ResourceGroup" -ForegroundColor Gray
Write-Host "  4. Health check: curl $WebAppUrl/health" -ForegroundColor Gray
Write-Host ""

