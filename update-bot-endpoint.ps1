# Updates the Azure Bot Service messaging endpoint to your dev tunnel URL.
# Usage: .\update-bot-endpoint.ps1 -TunnelUrl "https://abc123.devtunnels.ms"

param(
    [Parameter(Mandatory=$true)]
    [string]$TunnelUrl
)

$endpoint = "$TunnelUrl/api/messages"

Write-Host ""
Write-Host "=== Updating Bot Service Endpoint ===" -ForegroundColor Cyan
Write-Host "  New endpoint: $endpoint" -ForegroundColor Yellow
Write-Host ""

az bot update `
    --resource-group astaipublic `
    --name FabricOboBot `
    --endpoint $endpoint `
    --query "{name:name, endpoint:properties.endpoint}" `
    -o table

Write-Host ""
Write-Host "Done! Bot Service will now route messages to your local API." -ForegroundColor Green
Write-Host "Make sure your API is running (startapi.bat) and the tunnel is active (start-tunnel.bat)." -ForegroundColor Gray
Write-Host ""
