# ════════════════════════════════════════════════════════════════
# FabricObo Python API — Main Application
#
# Equivalent of Program.cs + Controllers in .NET.
# Implements the same endpoints:
#   POST /api/agent     — SPA path (JWT → OBO → Foundry, built-in Fabric tool)
#   POST /api/agent/v2  — MCP theory path (JWT → OBO → Foundry → MCP → Fabric direct)
#   GET  /api/config    — SPA authentication config (public, no auth)
#   POST /api/messages  — Bot Framework endpoint (Teams / Copilot Studio)
#   /mcp                — MCP server (theory path: Foundry calls back here for Fabric data)
#
# Run with:
#   uvicorn main:app --host 0.0.0.0 --port 5180 --reload
#
# Docs:
#   https://fastapi.tiangolo.com/
#   https://learn.microsoft.com/entra/msal/python/
# ════════════════════════════════════════════════════════════════
import json
import logging
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from auth import OboTokenService, TokenClaims
from config import AzureAdSettings, BotSettings, CorsSettings, FabricDirectSettings, FoundrySettings, SpaAuthSettings
from entitlement_service import StubEntitlementService
from foundry_agent_service import FoundryAgentService
from mcp_foundry_service import McpFoundryAgentService
from models import AgentRequest, AgentResponse, EntitlementResult
import token_session_store

# ════════════════════════════════════════════════════════════════
# Configuration & Logging
# ════════════════════════════════════════════════════════════════

# Load .env file from this folder
load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("fabricobo")

# ════════════════════════════════════════════════════════════════
# Load settings — all from .env / environment variables
# ════════════════════════════════════════════════════════════════

azure_ad = AzureAdSettings()
foundry = FoundrySettings()
spa_auth = SpaAuthSettings()
bot = BotSettings()
cors = CorsSettings()
fabric_direct = FabricDirectSettings()

# Entitlement users — loaded from ENTITLEMENT_USERS_JSON env var
# Format: '[{"upn":"user@tenant.com","rep_code":"REP001","role":"Advisor"}]'
import os

_ent_json = os.getenv("ENTITLEMENT_USERS_JSON", "[]")
try:
    _entitlement_users = json.loads(_ent_json)
except json.JSONDecodeError:
    logger.warning("Failed to parse ENTITLEMENT_USERS_JSON, using empty list")
    _entitlement_users = []

# ════════════════════════════════════════════════════════════════
# Service instances
# ════════════════════════════════════════════════════════════════

obo_service = OboTokenService(azure_ad)
entitlement_service = StubEntitlementService(_entitlement_users)
foundry_service = FoundryAgentService(foundry)
mcp_foundry_service = McpFoundryAgentService(foundry, fabric_direct)


# ════════════════════════════════════════════════════════════════
# Startup: ensure FabricMcpAgent exists in Foundry
# ════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create / update the FabricMcpAgent in Foundry so it appears in the portal."""
    if fabric_direct.is_configured:
        try:
            service_token = await obo_service.acquire_service_token()
            agent_name = await mcp_foundry_service.ensure_agent(service_token)
            logger.info("FabricMcpAgent ready in Foundry: %s", agent_name)
        except Exception as exc:
            # Non-fatal: MCP path still works with inline tools as fallback
            logger.warning(
                "Could not ensure FabricMcpAgent at startup (will fall back to inline tools): %s", exc
            )
    yield
    # No cleanup needed


# ════════════════════════════════════════════════════════════════
# FastAPI Application
# ════════════════════════════════════════════════════════════════

app = FastAPI(
    title="FabricObo API (Python)",
    lifespan=lifespan,
    description=(
        "Python implementation of the Fabric OBO API. "
        "Validates user JWTs, performs OBO token exchange, "
        "and calls the Azure AI Foundry Responses API with Fabric tool. "
        "Also exposes /api/agent/v2 (MCP concurrency-theory path) and "
        "an MCP server at /mcp."
    ),
    version="1.0.0",
)

# CORS — equivalent of builder.Services.AddCors() in .NET
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors.get_origins_list(),
    allow_methods=["*"],
    allow_headers=["*"],
)


# ════════════════════════════════════════════════════════════════
# Dependency: Azure AD settings (injected into auth module)
# ════════════════════════════════════════════════════════════════

async def get_current_user(
    request: Request,
) -> TokenClaims:
    """Dependency that validates the JWT and returns parsed claims."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = auth_header[7:]

    from auth import _get_signing_keys
    from jose import jwt, JWTError

    try:
        jwks = await _get_signing_keys(azure_ad)
        unverified_header = jwt.get_unverified_header(token)
        key = None
        for k in jwks.get("keys", []):
            if k["kid"] == unverified_header.get("kid"):
                key = k
                break

        if key is None:
            raise HTTPException(status_code=401, detail="Token signing key not found")

        # Accept both v1 and v2 token issuers (Microsoft.Identity.Web does this automatically)
        # v1: https://sts.windows.net/{tenant}/
        # v2: https://login.microsoftonline.com/{tenant}/v2.0
        valid_issuers = [
            f"https://login.microsoftonline.com/{azure_ad.tenant_id}/v2.0",
            f"https://sts.windows.net/{azure_ad.tenant_id}/",
        ]

        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=azure_ad.audience,
            options={"verify_at_hash": False, "verify_iss": False},
        )

        # Manual issuer validation against both v1 and v2
        token_issuer = claims.get("iss", "")
        if token_issuer not in valid_issuers:
            raise JWTError(
                f"Invalid issuer. Got '{token_issuer}', "
                f"expected one of {valid_issuers}"
            )

        # Store the raw token in claims so we can use it for OBO exchange
        claims["_raw_token"] = token
        return TokenClaims(claims)

    except JWTError as e:
        logger.warning("JWT validation failed: %s", str(e))
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


# ════════════════════════════════════════════════════════════════
# GET /api/config — Public SPA configuration (no auth required)
#
# Equivalent of ConfigController.Get() in .NET.
# Returns non-secret values needed by the SPA to configure MSAL.js.
# ════════════════════════════════════════════════════════════════

@app.get("/api/config")
async def get_config():
    """Serve non-secret SPA configuration (tenant ID, client IDs)."""
    # Parse test users from SPA_TEST_USERS_JSON env var
    test_users = []
    try:
        test_users = json.loads(spa_auth.test_users_json)
    except json.JSONDecodeError:
        pass

    return {
        "tenantId": spa_auth.tenant_id,
        "spaClientId": spa_auth.spa_client_id,
        "apiClientId": spa_auth.api_client_id,
        "testUsers": test_users,
    }


# ════════════════════════════════════════════════════════════════
# POST /api/agent — Main agent endpoint (authenticated)
#
# Equivalent of AgentController.Post() in .NET.
# Flow: Validate JWT → Entitlement lookup → OBO exchange → Foundry call
# ════════════════════════════════════════════════════════════════

@app.post("/api/agent", response_model=AgentResponse, response_model_by_alias=True, response_model_exclude_none=True)
async def post_agent(
    request: AgentRequest,
    user: TokenClaims = Depends(get_current_user),
):
    """
    Accepts a user question, looks up entitlement, runs Foundry agent, returns results.
    """
    correlation_id = uuid.uuid4().hex[:12]
    upn = user.upn
    oid = user.oid

    logger.info(
        "[%s] Request from UPN=%s, OID=%s, Question='%s'",
        correlation_id,
        upn,
        oid,
        _truncate(request.question, 100),
    )

    # ──────────────────────────────────────────────────────────
    # Step 1: Entitlement lookup (advisory, not enforcement)
    # ──────────────────────────────────────────────────────────
    try:
        entitlement = await entitlement_service.get_entitlement(upn, oid)
        logger.info(
            "[%s] Entitlement: RepCode=%s, Role=%s, Authorized=%s",
            correlation_id,
            entitlement.rep_code,
            entitlement.role,
            entitlement.is_authorized,
        )
    except Exception as ex:
        logger.error("[%s] Entitlement service error: %s", correlation_id, str(ex))
        entitlement = EntitlementResult(
            upn=upn, oid=oid, is_authorized=True
        )

    # ──────────────────────────────────────────────────────────
    # Step 2: Acquire OBO token for Foundry Agents API
    # ──────────────────────────────────────────────────────────
    raw_token = user.raw_claims.get("_raw_token", "")
    if not raw_token:
        raise HTTPException(
            status_code=500,
            detail="Internal error: raw token not available for OBO exchange",
        )

    try:
        obo_token = await obo_service.exchange_token(raw_token)
        logger.debug("[%s] OBO token acquired for Foundry", correlation_id)
    except HTTPException:
        raise
    except Exception as ex:
        logger.error("[%s] OBO token acquisition failed: %s", correlation_id, str(ex))
        raise HTTPException(
            status_code=500,
            detail={
                "status": "obo_error",
                "correlationId": correlation_id,
                "error": f"Failed to acquire OBO token: {str(ex)}",
            },
        )

    # ──────────────────────────────────────────────────────────
    # Step 3: Call Foundry v2 Responses API
    # ──────────────────────────────────────────────────────────
    agent_response = await foundry_service.run_agent(
        question=request.question,
        conversation_id=request.conversation_id,
        obo_access_token=obo_token,
        correlation_id=correlation_id,
    )

    # Attach entitlement info
    agent_response = agent_response.model_copy(update={"entitlement": entitlement})

    logger.info(
        "[%s] Response: Status=%s, ConversationId=%s, ResponseId=%s",
        correlation_id,
        agent_response.status,
        agent_response.conversation_id,
        agent_response.response_id,
    )

    return agent_response


# ════════════════════════════════════════════════════════════════
# POST /api/messages — Bot Framework endpoint
#
# Equivalent of BotController.Post() in .NET.
# Handles Bot Framework protocol messages from Teams / Copilot Studio.
#
# Note: Full bot support requires the botbuilder-python SDK.
# This is a placeholder that shows the endpoint structure.
# For full Teams bot functionality, see the dotnetapi implementation.
# ════════════════════════════════════════════════════════════════

@app.post("/api/messages")
async def post_messages(request: Request):
    """
    Bot Framework endpoint for Teams / Copilot Studio messages.

    This endpoint receives Bot Framework activities. The full bot
    implementation with SSO, sign-in cards, and conversation management
    requires completing the bot adapter setup below.

    For a full production bot, consider using the dotnetapi implementation
    or completing the botbuilder-python integration.
    """
    try:
        from bot_handler import handle_bot_message

        return await handle_bot_message(request, bot, azure_ad, foundry_service, entitlement_service)
    except ImportError:
        logger.warning("Bot handler not configured — /api/messages returning 501")
        raise HTTPException(
            status_code=501,
            detail="Bot Framework support is not fully configured in the Python API. "
            "Use the .NET API (dotnetapi/) for full Teams/Copilot Studio integration.",
        )


# ════════════════════════════════════════════════════════════════
# POST /api/agent/v2 — MCP Theory Path (concurrency experiment)
#
# Alternative to /api/agent that replaces Foundry's built-in Fabric
# tool with a custom MCP server (fabric_mcp_server.py) that:
#   • Does a SECOND OBO exchange for the Fabric scope
#   • Creates a NEW Fabric thread per request
#   • Calls the Fabric Data Agent directly via OpenAI Assistants API
#
# Theory: if Fabric serialises at the thread level (not globally per-OID),
# new-thread-per-request enables concurrent same-user Fabric runs.
#
# Prerequisites:
#   FABRIC_DIRECT_DATA_AGENT_URL — your Fabric Data Agent URL
#   FABRIC_DIRECT_API_PUBLIC_URL — public URL of this API (dev: devtunnel URL)
# ════════════════════════════════════════════════════════════════

@app.post("/api/agent/v2", response_model=AgentResponse, response_model_by_alias=True, response_model_exclude_none=True)
async def post_agent_mcp(
    request: AgentRequest,
    user: TokenClaims = Depends(get_current_user),
):
    """
    MCP-based Fabric query path (theory test for same-user concurrency).

    Identical flow to /api/agent up to the OBO exchange, then diverges:
      1. Performs a second OBO exchange for the Fabric scope.
      2. Stores the Fabric token in the in-process session store.
      3. Calls Foundry with an inline MCP tool (not the built-in Fabric tool).
      4. Foundry calls back to /mcp (this API) to invoke query_fabric.
      5. The MCP tool retrieves the Fabric token and runs the query on a NEW thread.
    """
    if not fabric_direct.is_configured:
        raise HTTPException(
            status_code=503,
            detail=(
                "MCP theory path is not configured. "
                "Set FABRIC_DIRECT_DATA_AGENT_URL and FABRIC_DIRECT_API_PUBLIC_URL."
            ),
        )

    correlation_id = uuid.uuid4().hex[:12]
    upn = user.upn
    oid = user.oid

    logger.info(
        "[%s] MCP path request from UPN=%s Question='%s'",
        correlation_id,
        upn,
        _truncate(request.question, 100),
    )

    raw_token = user.raw_claims.get("_raw_token", "")
    if not raw_token:
        raise HTTPException(status_code=500, detail="Internal error: raw token not available for OBO exchange")

    # OBO #1 — Foundry scope (for the Foundry Responses API call)
    try:
        foundry_obo_token = await obo_service.exchange_token(raw_token)
    except HTTPException:
        raise
    except Exception as ex:
        raise HTTPException(status_code=500, detail={"status": "obo_error", "error": str(ex)})

    # OBO #2 — Fabric scope (for the MCP tool to call Fabric directly with RLS)
    try:
        fabric_obo_token = await obo_service.exchange_token_for_fabric(raw_token)
    except HTTPException:
        raise
    except Exception as ex:
        raise HTTPException(
            status_code=500,
            detail={"status": "fabric_obo_error", "error": f"Fabric OBO failed: {ex}"},
        )

    # Store Fabric token in the session store — the MCP tool retrieves it by this key.
    # The session_key itself (an opaque UUID) is the only value that flows through
    # Foundry's model context.
    session_key = str(uuid.uuid4())
    token_session_store.set_token(session_key, fabric_obo_token)
    logger.debug("[%s] Fabric token stored in session store. session_key=%s...", correlation_id, session_key[:8])

    agent_response = await mcp_foundry_service.run_agent(
        question=request.question,
        session_key=session_key,
        obo_access_token=foundry_obo_token,
        correlation_id=correlation_id,
        conversation_id=request.conversation_id,
    )

    # Eagerly evict the session key now that the Foundry round-trip is done
    token_session_store.delete_token(session_key)

    logger.info(
        "[%s] MCP path response: Status=%s ConversationId=%s",
        correlation_id,
        agent_response.status,
        agent_response.conversation_id,
    )
    return agent_response


# ════════════════════════════════════════════════════════════════
# /mcp  — MCP Server (mounted ASGI sub-app)
#
# This is the endpoint Foundry calls when the agent invokes the
# query_fabric MCP tool.  It is served by fabric_mcp_server.py.
#
# Only mounted when FABRIC_DIRECT settings are fully configured.
# The Mount is placed AFTER /api/* routes so it does not interfere.
# ════════════════════════════════════════════════════════════════

if fabric_direct.is_configured:
    try:
        from fabric_mcp_server import build_mcp_asgi_app
        mcp_asgi = build_mcp_asgi_app(fabric_direct)
        # path="/" inside the sub-app + mount at "/mcp" → endpoint is POST /mcp
        app.mount("/mcp", mcp_asgi)
        logger.info(
            "MCP server mounted at /mcp (Foundry MCP server_url: %s)",
            fabric_direct.mcp_server_url,
        )
    except ImportError as e:
        logger.warning(
            "fastmcp not installed — MCP server not mounted. "
            "Run: pip install fastmcp openai  (%s)",
            e,
        )
else:
    logger.info(
        "FABRIC_DIRECT settings not configured — /mcp endpoint not mounted. "
        "Set FABRIC_DIRECT_DATA_AGENT_URL and FABRIC_DIRECT_API_PUBLIC_URL to enable."
    )


# ════════════════════════════════════════════════════════════════
# Health check endpoint
# ════════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    """Simple health check endpoint."""
    return {
        "status": "healthy",
        "implementation": "python",
        "mcp_path_configured": fabric_direct.is_configured,
    }


# ════════════════════════════════════════════════════════════════
# Test history — POST/GET /api/tests
#
# Saves completed load-test sessions to pythonapi/data/ as JSON.
# ════════════════════════════════════════════════════════════════

_DATA_DIR = Path(__file__).parent / "data"
_TEST_ID_RE = re.compile(r'^[A-Z0-9]{6,16}$|^MCP-[A-Z0-9]{6,16}$')


@app.post("/api/tests", status_code=201)
async def save_test(
    request: Request,
    user: TokenClaims = Depends(get_current_user),
):
    """Save a completed load-test session JSON to the data/ folder."""
    body = await request.json()
    test_id = str(body.get("testId", "")).upper()
    if not _TEST_ID_RE.match(test_id):
        raise HTTPException(status_code=400, detail="Invalid testId")
    _DATA_DIR.mkdir(exist_ok=True)
    start_time = body.get("startTimeIso", "")
    safe_ts = start_time[:19].replace(":", "-").replace("T", "_") if start_time else "unknown"
    filename = f"load-test-{test_id}-{safe_ts}.json"
    filepath = _DATA_DIR / filename
    filepath.write_text(json.dumps(body, indent=2), encoding="utf-8")
    logger.info("Saved load test %s → %s", test_id, filepath.name)
    return {"saved": True, "testId": test_id, "filename": filename}


@app.get("/api/tests")
async def list_tests(
    user: TokenClaims = Depends(get_current_user),
    type: Optional[str] = None,
):
    """List saved test sessions. Pass ?type=chat or ?type=mcp to filter by type."""
    _DATA_DIR.mkdir(exist_ok=True)
    tests = []
    for f in sorted(_DATA_DIR.glob("load-test-*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            tests.append({
                "testId": data.get("testId"),
                "startTimeIso": data.get("startTimeIso"),
                "endTimeIso": data.get("endTimeIso"),
                "totalDurationMs": data.get("totalDurationMs"),
                "fabricSku": data.get("fabricSku"),
                "summary": data.get("summary"),
                "config": data.get("config"),
                "filename": f.name,
            })
        except Exception:
            pass
    if type == "mcp":
        tests = [t for t in tests if str(t.get("testId", "")).startswith("MCP-")]
    elif type == "chat":
        tests = [t for t in tests if not str(t.get("testId", "")).startswith("MCP-")]
    return tests


@app.get("/api/tests/{test_id}")
async def get_test(test_id: str, user: TokenClaims = Depends(get_current_user)):
    """Return the full JSON for a specific saved test session."""
    if not _TEST_ID_RE.match(test_id.upper()):
        raise HTTPException(status_code=400, detail="Invalid test_id")
    _DATA_DIR.mkdir(exist_ok=True)
    matches = list(_DATA_DIR.glob(f"load-test-{test_id.upper()}-*.json"))
    if not matches:
        raise HTTPException(status_code=404, detail=f"Test {test_id} not found")
    return json.loads(matches[0].read_text(encoding="utf-8"))


def _truncate(value: str, max_length: int) -> str:
    return value[:max_length] + "…" if len(value) > max_length else value


# ════════════════════════════════════════════════════════════════
# Entry point — run with: python main.py
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=5180,
        reload=True,
        log_level="info",
    )
