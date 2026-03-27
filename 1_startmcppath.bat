@echo off
REM ============================================================
REM  startmcppath.bat  -  Start the MCP Theory path (v2)
REM
REM  Architecture under test:
REM    Browser -> API (/api/agent/v2) -> Azure AI Foundry
REM            -> /mcp (inline MCP tool)
REM            -> Fabric Data Agent (new thread per request)
REM            -> RLS results -> Browser
REM
REM  Why a separate script?
REM    1. One extra pip package is required (openai).
REM    2. Foundry must be able to call back to /mcp on a PUBLIC URL,
REM       so the dev tunnel MUST be running before you start the API.
REM    3. Two extra env vars must be set in pythonapi\.env:
REM         FABRIC_DIRECT_DATA_AGENT_URL  - Fabric Data Agent endpoint
REM         FABRIC_DIRECT_API_PUBLIC_URL  - your tunnel URL
REM
REM  Quick start:
REM    1. Run start-tunnel.bat in a separate window, note the URL
REM    2. Fill in the two env vars in pythonapi\.env (see below)
REM    3. Run THIS script - installs deps, starts API
REM    4. Open the client app, click the "MCP Theory" tab
REM ============================================================

echo.
echo ============================================================
echo   Fabric OBO POC  -  MCP Theory Path (v2) Launcher
echo ============================================================
echo.

REM -- Step 1: Verify the .env file has the required vars ------
echo [1/3] Checking pythonapi\.env for required MCP variables...
echo.

IF NOT EXIST "pythonapi\.env" (
  echo ERROR: pythonapi\.env not found.
  echo        Copy pythonapi\.env.example and fill in all values first.
  echo.
  pause
  exit /b 1
)

findstr /i "FABRIC_DIRECT_DATA_AGENT_URL" "pythonapi\.env" > NUL 2>&1
IF ERRORLEVEL 1 (
  echo WARNING: FABRIC_DIRECT_DATA_AGENT_URL is not set in pythonapi\.env
  echo          Add the line:
  echo            FABRIC_DIRECT_DATA_AGENT_URL=https://api.fabric.microsoft.com/v1/workspaces/^<id^>/dataagentruntimes/^<id^>/openai
  echo.
  echo Press any key to continue anyway ^(MCP path will be disabled^)...
  pause > NUL
)

findstr /i "FABRIC_DIRECT_API_PUBLIC_URL" "pythonapi\.env" > NUL 2>&1
IF ERRORLEVEL 1 (
  echo WARNING: FABRIC_DIRECT_API_PUBLIC_URL is not set in pythonapi\.env
  echo          This must be set to the public tunnel URL so Foundry can
  echo          call back to /mcp on your local API, e.g.:
  echo            FABRIC_DIRECT_API_PUBLIC_URL=https://abc123.devtunnels.ms
  echo.
  echo          Tunnel setup:
  echo            - Run start-tunnel.bat in a SEPARATE window first
  echo            - Copy the https://....devtunnels.ms URL it prints
  echo            - Add FABRIC_DIRECT_API_PUBLIC_URL=^<that URL^> to pythonapi\.env
  echo            - Re-run this script
  echo.
  echo Press any key to continue anyway ^(MCP path will be disabled^)...
  pause > NUL
)

echo .env check done.
echo.

REM -- Step 2: Install / upgrade the openai package ------------
echo [2/3] Installing/upgrading openai package...
echo       This is safe to run every time - pip skips already-current packages.
echo.

pip install "openai>=1.50.0" --quiet --upgrade
IF ERRORLEVEL 1 (
  echo.
  echo ERROR: pip install failed.
  echo        Make sure you are in the correct virtual environment
  echo        and that pip is available on the PATH.
  pause
  exit /b 1
)

echo Packages ready.
echo.

REM -- Step 3: Start the API (MCP server is embedded in FastAPI)
echo [3/3] Starting Python API (FastAPI + embedded /mcp endpoint)...
echo       The MCP server is built on Starlette - no extra packages needed.
echo.
echo   API:         http://localhost:5180
echo   Health:      http://localhost:5180/health
echo   MCP server:  POST http://localhost:5180/mcp
echo.
echo Press Ctrl+C to stop.
echo.

cd pythonapi && python main.py
