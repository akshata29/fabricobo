# ════════════════════════════════════════════════════════════════
# Authentication — JWT Validation & OBO Token Exchange
#
# Validates incoming JWT bearer tokens issued by Entra ID and
# performs OBO token exchange for the Foundry API.
#
# Equivalent of:
#   - Microsoft.Identity.Web JWT validation in .NET
#   - ITokenAcquisition.GetAccessTokenForUserAsync in .NET
#   - BotOboTokenService in .NET
#
# Docs:
#   https://learn.microsoft.com/entra/msal/python/
#   https://learn.microsoft.com/entra/identity-platform/v2-oauth2-on-behalf-of-flow
# ════════════════════════════════════════════════════════════════
import logging
from typing import Optional

import httpx
import msal
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from config import AzureAdSettings

logger = logging.getLogger("fabricobo.auth")

# Security scheme — extracts the Bearer token from the Authorization header
bearer_scheme = HTTPBearer(auto_error=True)

# ════════════════════════════════════════════════════════════════
# OIDC metadata cache — avoids fetching signing keys on every request
# ════════════════════════════════════════════════════════════════
_jwks_cache: Optional[dict] = None


async def _get_signing_keys(settings: AzureAdSettings) -> dict:
    """
    Fetches the OIDC signing keys from Entra ID's JWKS endpoint.
    Cached in-process for the lifetime of the application.
    """
    global _jwks_cache
    if _jwks_cache is not None:
        return _jwks_cache

    oidc_url = (
        f"{settings.instance}{settings.tenant_id}/v2.0/.well-known/openid-configuration"
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(oidc_url)
        resp.raise_for_status()
        jwks_uri = resp.json()["jwks_uri"]

        resp = await client.get(jwks_uri)
        resp.raise_for_status()
        _jwks_cache = resp.json()

    return _jwks_cache


class TokenClaims:
    """Parsed and validated JWT claims from the incoming request."""

    def __init__(self, claims: dict):
        self.raw_claims = claims
        self.upn = (
            claims.get("preferred_username")
            or claims.get("upn")
            or "unknown"
        )
        self.oid = (
            claims.get("http://schemas.microsoft.com/identity/claims/objectidentifier")
            or claims.get("oid")
            or "unknown"
        )
        self.display_name = claims.get("name") or self.upn


async def validate_token(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    settings: AzureAdSettings = Depends(),
) -> TokenClaims:
    """
    FastAPI dependency that validates the incoming JWT bearer token.

    Equivalent of [Authorize] + Microsoft.Identity.Web in .NET.
    Handles both v1 and v2 Entra ID tokens (Copilot Studio may send v1).
    """
    token = credentials.credentials
    # Debug: print to stdout to bypass any logger issues
    print(f"[AUTH DEBUG] Token length: {len(token)}, parts: {token.count('.')}, starts: {token[:40]}...")
    try:
        # Log token details for debugging
        logger.warning("Token length: %d, starts with: %s..., parts: %d",
                       len(token),
                       token[:30] if len(token) > 30 else token,
                       token.count('.'))

        jwks = await _get_signing_keys(settings)
        # Decode the header to find the signing key
        unverified_header = jwt.get_unverified_header(token)
        logger.debug("Token header: alg=%s, kid=%s", unverified_header.get("alg"), unverified_header.get("kid"))

        key = None
        for k in jwks.get("keys", []):
            if k["kid"] == unverified_header.get("kid"):
                key = k
                break

        if key is None:
            raise HTTPException(status_code=401, detail="Token signing key not found")

        # Accept both v1 and v2 issuers (Copilot Studio may send v1 tokens)
        valid_issuers = [
            f"{settings.instance}{settings.tenant_id}/v2.0",
            f"https://sts.windows.net/{settings.tenant_id}/",
            f"{settings.instance}{settings.tenant_id}",
        ]

        # Accept both api:// URI and raw GUID as audience
        valid_audiences = [
            settings.audience,
            settings.client_id,
            f"api://{settings.client_id}",
        ]

        # First try decoding without issuer/audience validation to log claims
        try:
            unverified_claims = jwt.decode(
                token, key, algorithms=["RS256"],
                options={"verify_aud": False, "verify_iss": False, "verify_at_hash": False},
            )
            logger.debug("Token iss=%s, aud=%s, upn=%s",
                         unverified_claims.get("iss"),
                         unverified_claims.get("aud"),
                         unverified_claims.get("preferred_username") or unverified_claims.get("upn"))
        except JWTError:
            pass

        # Now validate properly with flexible issuer/audience
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=valid_audiences,
            issuer=valid_issuers,
            options={"verify_at_hash": False},
        )

        return TokenClaims(claims)

    except JWTError as e:
        logger.warning("JWT validation failed: %s", str(e))
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


# ════════════════════════════════════════════════════════════════
# OBO Token Acquisition — MSAL Confidential Client
# ════════════════════════════════════════════════════════════════

# The downstream scope for OBO — same as in .NET AgentController
FOUNDRY_SCOPES = ["https://ai.azure.com/.default"]


class OboTokenService:
    """
    Performs On-Behalf-Of (OBO) token exchange using MSAL Python.

    Equivalent of:
      - ITokenAcquisition.GetAccessTokenForUserAsync in the SPA path
      - BotOboTokenService.ExchangeTokenAsync in the bot path

    Docs: https://learn.microsoft.com/entra/identity-platform/v2-oauth2-on-behalf-of-flow
    """

    def __init__(self, settings: AzureAdSettings):
        self._settings = settings
        self._app: msal.ConfidentialClientApplication | None = None

    def _get_app(self) -> msal.ConfidentialClientApplication:
        """Lazily create the MSAL app on first use."""
        if self._app is None:
            authority = f"{self._settings.instance.rstrip('/')}/{self._settings.tenant_id}"
            self._app = msal.ConfidentialClientApplication(
                client_id=self._settings.client_id,
                client_credential=self._settings.client_secret,
                authority=authority,
            )
        return self._app

    async def exchange_token(self, user_token: str) -> str:
        """
        Exchanges the user's incoming JWT for a Foundry-scoped OBO token.

        Args:
            user_token: The incoming JWT from the SPA or Bot Service OAuth.

        Returns:
            Access token scoped to https://ai.azure.com/.default

        Raises:
            HTTPException: If the OBO exchange fails (consent needed, etc.)
        """
        logger.debug("Performing OBO exchange")

        result = self._get_app().acquire_token_on_behalf_of(
            user_assertion=user_token,
            scopes=FOUNDRY_SCOPES,
        )

        if "access_token" in result:
            logger.debug("OBO exchange succeeded")
            return result["access_token"]

        error = result.get("error", "unknown_error")
        error_description = result.get("error_description", "No description")
        logger.warning("OBO exchange failed: %s — %s", error, error_description)

        if error == "interaction_required":
            raise HTTPException(
                status_code=401,
                detail={
                    "status": "obo_challenge",
                    "error": (
                        "Token acquisition requires user interaction (consent or re-auth). "
                        f"{error_description}"
                    ),
                },
            )

        raise HTTPException(
            status_code=500,
            detail={
                "status": "obo_error",
                "error": f"Failed to acquire OBO token: {error} — {error_description}",
            },
        )
