@echo off
REM ══════════════════════════════════════════════════════════════
REM Start Dev Tunnel for Teams Bot testing
REM
REM This script:
REM   1. Creates a dev tunnel pointing to your local API (port 5180)
REM   2. Outputs the public URL
REM   3. Updates the Azure Bot Service messaging endpoint
REM
REM Prerequisites:
REM   - Azure CLI logged in (az login)
REM   - Dev Tunnels CLI installed (winget install Microsoft.devtunnel)
REM   - devtunnel user login (first time only)
REM
REM Usage: Run this BEFORE starting the API with startapi.bat
REM ══════════════════════════════════════════════════════════════

echo.
echo === Fabric OBO Bot - Dev Tunnel Setup ===
echo.

SET DEVTUNNEL="%LOCALAPPDATA%\Microsoft\WinGet\Packages\Microsoft.devtunnel_Microsoft.Winget.Source_8wekyb3d8bbwe\devtunnel.exe"

REM Login to dev tunnels (will use cached credentials if already logged in)
echo [1/3] Ensuring dev tunnel login...
%DEVTUNNEL% user login

echo.
echo [2/3] Starting dev tunnel on port 5180 (anonymous access for Bot Framework)...
echo        Press Ctrl+C to stop the tunnel when done testing.
echo.
echo IMPORTANT: Copy the tunnel URL from below and note it.
echo The bot endpoint will be: {tunnel-url}/api/messages
echo.

%DEVTUNNEL% host -p 5180 --allow-anonymous
