# ════════════════════════════════════════════════════════════════
# FabricObo Python API — Main Application
#
# Equivalent of Program.cs + Controllers in .NET.
# Implements the same endpoints:
#   POST /api/agent     — SPA path (JWT → OBO → Foundry)
#   GET  /api/config    — SPA authentication config (public, no auth)
#   POST /api/messages  — Bot Framework endpoint (Teams / Copilot Studio)
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
import uuid
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from auth import OboTokenService, TokenClaims
from config import AzureAdSettings, BotSettings, CorsSettings, FoundrySettings, SpaAuthSettings
from entitlement_service import StubEntitlementService
from foundry_agent_service import FoundryAgentService
from models import AgentRequest, AgentResponse, EntitlementResult

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


# ════════════════════════════════════════════════════════════════
# FastAPI Application
# ════════════════════════════════════════════════════════════════

app = FastAPI(
    title="FabricObo API (Python)",
    description=(
        "Python implementation of the Fabric OBO API. "
        "Validates user JWTs, performs OBO token exchange, "
        "and calls the Azure AI Foundry Responses API with Fabric tool."
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
# Health check endpoint
# ════════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    """Simple health check endpoint."""
    return {"status": "healthy", "implementation": "python"}


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
