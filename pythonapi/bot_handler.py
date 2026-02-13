# ════════════════════════════════════════════════════════════════
# Bot Handler — Teams / Copilot Studio Integration
#
# This module implements the Bot Framework protocol for the Python API.
# Unlike the SPA path (where the browser sends JWTs), the Bot path uses
# Azure Bot Service OAuth to get a user token, then does OBO exchange.
#
# Key difference from the naive "return a dict" approach:
#   The Bot Framework protocol requires replies to be SENT BACK via
#   the Bot Connector REST API at activity.serviceUrl, not returned
#   as the HTTP response body. The HTTP response must be 200 (empty).
#
# Equivalent of:
#   - BotController.cs + FabricOboBot.cs in .NET
#
# Docs:
#   https://learn.microsoft.com/azure/bot-service/rest-api/bot-framework-rest-connector-send-and-receive-messages
#   https://learn.microsoft.com/azure/bot-service/rest-api/bot-framework-rest-connector-authentication
# ════════════════════════════════════════════════════════════════
import json
import logging
import uuid

import httpx
import msal
from fastapi import Request, Response

from auth import OboTokenService
from config import AzureAdSettings, BotSettings
from entitlement_service import IEntitlementService
from foundry_agent_service import IFoundryAgentService

logger = logging.getLogger("fabricobo.bot")


# ════════════════════════════════════════════════════════════════
# Bot Connector Client — sends replies via the REST API
# ════════════════════════════════════════════════════════════════

class BotConnectorClient:
    """
    Sends replies back through the Bot Connector REST API.

    When the bot receives an activity, it must reply by POSTing to:
      {serviceUrl}/v3/conversations/{conversationId}/activities/{replyToId}

    This requires authenticating with the Bot Connector using the bot's
    app credentials (same client ID / secret as AzureAd config).
    """

    def __init__(self, bot_settings: BotSettings):
        self._bot_settings = bot_settings
        self._app: msal.ConfidentialClientApplication | None = None

    def _get_msal_app(self) -> msal.ConfidentialClientApplication:
        if self._app is None:
            self._app = msal.ConfidentialClientApplication(
                client_id=self._bot_settings.microsoft_app_id,
                client_credential=self._bot_settings.microsoft_app_password,
                authority=f"https://login.microsoftonline.com/{self._bot_settings.microsoft_app_tenant_id}",
            )
        return self._app

    async def _get_bot_token(self) -> str:
        """
        Get an access token for the Bot Connector service.
        Uses the bot's app credentials to authenticate.
        """
        app = self._get_msal_app()

        result = app.acquire_token_silent(
            scopes=["https://api.botframework.com/.default"],
            account=None,
        )
        if not result:
            result = app.acquire_token_for_client(
                scopes=["https://api.botframework.com/.default"],
            )

        if "access_token" not in result:
            error = result.get("error_description", result.get("error", "Unknown error"))
            raise RuntimeError(f"Failed to get bot connector token: {error}")

        return result["access_token"]

    async def send_activity(self, service_url: str, activity: dict) -> None:
        """
        Send a reply activity to the Bot Connector REST API.

        POST {serviceUrl}/v3/conversations/{conversationId}/activities/{replyToId}
        """
        conversation_id = activity.get("conversation", {}).get("id", "")
        reply_to_id = activity.get("replyToId", "")

        base = service_url.rstrip("/")
        if reply_to_id:
            url = f"{base}/v3/conversations/{conversation_id}/activities/{reply_to_id}"
        else:
            url = f"{base}/v3/conversations/{conversation_id}/activities"

        token = await self._get_bot_token()

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                url,
                json=activity,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code >= 400:
                logger.error(
                    "Bot Connector reply failed: %d %s — %s",
                    resp.status_code,
                    resp.reason_phrase,
                    resp.text[:500],
                )
            else:
                logger.debug("Bot reply sent successfully (status=%d)", resp.status_code)

    async def send_typing(self, incoming_activity: dict) -> None:
        """Send a typing indicator."""
        typing_activity = _create_reply(incoming_activity, "")
        typing_activity["type"] = "typing"
        typing_activity.pop("text", None)
        service_url = incoming_activity.get("serviceUrl", "")
        if service_url:
            await self.send_activity(service_url, typing_activity)

    async def send_text_reply(self, incoming_activity: dict, text: str) -> None:
        """Send a text message reply."""
        reply = _create_reply(incoming_activity, text)
        service_url = incoming_activity.get("serviceUrl", "")
        if service_url:
            await self.send_activity(service_url, reply)


# ════════════════════════════════════════════════════════════════
# OAuth token retrieval via Bot Service
# ════════════════════════════════════════════════════════════════

# Bot Framework Token Service — fixed endpoint for user token operations.
# This is NOT the same as activity.serviceUrl (the Bot Connector URL).
# Docs: https://learn.microsoft.com/azure/bot-service/rest-api/bot-framework-rest-connector-authentication
TOKEN_SERVICE_URL = "https://token.botframework.com"


class BotTokenClient:
    """
    Gets the user's cached OAuth token from Azure Bot Service.

    When an OAuth connection is configured in Azure Bot Service,
    the service handles the SSO/consent flow. This client retrieves
    the cached token via the Token Service (NOT the activity serviceUrl).
    """

    def __init__(self, connector: BotConnectorClient):
        self._connector = connector

    async def get_user_token(
        self,
        user_id: str,
        channel_id: str,
        connection_name: str,
        magic_code: str | None = None,
    ) -> str | None:
        """
        Retrieve user token from Azure Bot Service token store.

        GET https://token.botframework.com/api/usertoken/GetToken?userId={}&connectionName={}&channelId={}
        """
        url = f"{TOKEN_SERVICE_URL}/api/usertoken/GetToken"

        params: dict[str, str] = {
            "userId": user_id,
            "connectionName": connection_name,
            "channelId": channel_id,
        }
        if magic_code:
            params["code"] = magic_code

        token = await self._connector._get_bot_token()

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("token")
            elif resp.status_code == 404:
                return None
            else:
                logger.warning(
                    "GetUserToken failed: %d %s",
                    resp.status_code,
                    resp.text[:300],
                )
                return None

    async def get_sign_in_resource(
        self,
        connection_name: str,
    ) -> str | None:
        """Get sign-in URL for OAuth card."""
        url = f"{TOKEN_SERVICE_URL}/api/botsignin/GetSignInResource"

        params = {"connectionName": connection_name}
        token = await self._connector._get_bot_token()

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("signInLink")
            else:
                logger.warning(
                    "GetSignInResource failed: %d %s",
                    resp.status_code,
                    resp.text[:300],
                )
                return None


# ════════════════════════════════════════════════════════════════
# Main Bot Message Handler
# ════════════════════════════════════════════════════════════════

async def handle_bot_message(
    request: Request,
    bot_settings: BotSettings,
    azure_ad_settings: AzureAdSettings,
    foundry_service: IFoundryAgentService,
    entitlement_service: IEntitlementService,
) -> Response:
    """
    Handles incoming Bot Framework activities.

    Implements the same flow as FabricOboBot.cs:
      1. Receive activity from Bot Connector
      2. For message activities: get user token → OBO exchange → call Foundry
      3. Send reply via Bot Connector REST API
      4. Return HTTP 200 (empty body) to the Bot Connector
    """
    try:
        body = await request.json()
    except Exception:
        return Response(status_code=400)

    activity_type = body.get("type", "")
    correlation_id = uuid.uuid4().hex[:12]
    service_url = body.get("serviceUrl", "")

    logger.info(
        "[%s] Bot activity received: type=%s, channelId=%s",
        correlation_id,
        activity_type,
        body.get("channelId", "unknown"),
    )

    connector = BotConnectorClient(bot_settings)
    token_client = BotTokenClient(connector)
    connection_name = bot_settings.oauth_connection_name

    if activity_type == "message":
        question = (body.get("text") or "").strip()

        if not question:
            await connector.send_text_reply(body, "Please send me a question about your data.")
            return Response(status_code=200)

        logger.info(
            "[%s] Bot message from %s: '%s'",
            correlation_id,
            body.get("from", {}).get("name", "unknown"),
            question[:100],
        )

        # ──────────────────────────────────────────────────────
        # Step 1: Get user token via Azure Bot Service OAuth
        # Detect magic code (6-digit sign-in code)
        # ──────────────────────────────────────────────────────
        magic_code = None
        if len(question) == 6 and question.isdigit():
            magic_code = question
            logger.info("[%s] Detected magic code, completing sign-in", correlation_id)

        user_id = body.get("from", {}).get("id", "unknown")
        channel_id = body.get("channelId", "")

        user_token = await token_client.get_user_token(
            user_id=user_id,
            channel_id=channel_id,
            connection_name=connection_name,
            magic_code=magic_code,
        )

        if not user_token:
            # No cached token — send sign-in card
            logger.info("[%s] No token cached — sending sign-in card", correlation_id)
            sign_in_link = await token_client.get_sign_in_resource(
                connection_name=connection_name,
            )
            if sign_in_link:
                await _send_sign_in_card(connector, body, sign_in_link)
            else:
                await connector.send_text_reply(
                    body,
                    "Authentication is not configured. Please contact your administrator.",
                )
            return Response(status_code=200)

        # If the user just entered a magic code, prompt for a question
        if magic_code:
            logger.info("[%s] Sign-in completed via magic code", correlation_id)
            await connector.send_text_reply(
                body, "You're signed in! Please type your question now."
            )
            return Response(status_code=200)

        # ──────────────────────────────────────────────────────
        # Step 2: Send typing indicator
        # ──────────────────────────────────────────────────────
        await connector.send_typing(body)

        try:
            # ──────────────────────────────────────────────────
            # Step 3: Exchange user token for Foundry OBO token
            # ──────────────────────────────────────────────────
            obo_service = OboTokenService(azure_ad_settings)
            obo_token = await obo_service.exchange_token(user_token)
            logger.debug("[%s] OBO token acquired via bot path", correlation_id)

            # ──────────────────────────────────────────────────
            # Step 4: Entitlement lookup
            # ──────────────────────────────────────────────────
            from jose import jwt as jose_jwt
            try:
                unverified = jose_jwt.get_unverified_claims(user_token)
                upn = unverified.get("upn", unverified.get("preferred_username", ""))
                oid = unverified.get("oid", "")
            except Exception:
                upn = ""
                oid = ""

            entitlement = await entitlement_service.get_entitlement(upn, oid)

            # ──────────────────────────────────────────────────
            # Step 5: Call Foundry agent (same as SPA path)
            # ──────────────────────────────────────────────────
            agent_response = await foundry_service.run_agent(
                question=question,
                conversation_id=None,  # Let Foundry manage conversation IDs
                obo_access_token=obo_token,
                correlation_id=correlation_id,
            )

            logger.info(
                "[%s] Bot response: Status=%s",
                correlation_id,
                agent_response.status,
            )

            # ──────────────────────────────────────────────────
            # Step 6: Format and send the response
            # ──────────────────────────────────────────────────
            reply_text = _format_bot_response(agent_response)
            await connector.send_text_reply(body, reply_text)

        except Exception as ex:
            if "consent" in str(ex).lower():
                logger.warning("[%s] Consent required: %s", correlation_id, str(ex))
                await connector.send_text_reply(
                    body,
                    "I need additional permissions to access your data. "
                    "Please sign out and sign back in using the command 'signout'.",
                )
            else:
                logger.error("[%s] Bot processing error: %s", correlation_id, str(ex), exc_info=True)
                await connector.send_text_reply(
                    body,
                    "Sorry, I encountered an error processing your request. Please try again.",
                )

    elif activity_type == "conversationUpdate":
        members_added = body.get("membersAdded", [])
        recipient_id = body.get("recipient", {}).get("id", "")
        for member in members_added:
            if member.get("id") != recipient_id:
                await connector.send_text_reply(
                    body,
                    "Hello! I'm the Fabric Data Assistant. Ask me questions about your data "
                    "and I'll query it using your identity so you only see what you're authorized for.\n\n"
                    'Try: *"Show me all my accounts and their balances."*',
                )

    # Always return 200 to the Bot Connector
    return Response(status_code=200)


# ════════════════════════════════════════════════════════════════
# Helper Functions
# ════════════════════════════════════════════════════════════════

def _create_reply(activity: dict, text: str) -> dict:
    """Create a Bot Framework reply activity."""
    return {
        "type": "message",
        "text": text,
        "from": activity.get("recipient", {}),
        "recipient": activity.get("from", {}),
        "conversation": activity.get("conversation", {}),
        "replyToId": activity.get("id"),
    }


async def _send_sign_in_card(
    connector: BotConnectorClient,
    activity: dict,
    sign_in_link: str,
) -> None:
    """Send an OAuth sign-in card to the user."""
    reply = _create_reply(activity, "")
    reply["attachments"] = [
        {
            "contentType": "application/vnd.microsoft.card.hero",
            "content": {
                "title": "Sign In Required",
                "text": "Please sign in to access your data.",
                "buttons": [
                    {
                        "type": "signin",
                        "title": "Sign In",
                        "value": sign_in_link,
                    }
                ],
            },
        }
    ]
    service_url = activity.get("serviceUrl", "")
    if service_url:
        await connector.send_activity(service_url, reply)


def _format_bot_response(agent_response) -> str:
    """Format the Foundry agent response for display in Teams / Copilot Studio."""
    if agent_response.status != "completed" or not agent_response.assistant_answer:
        return agent_response.error or "I wasn't able to get an answer. Please try rephrasing your question."

    text = agent_response.assistant_answer

    if agent_response.tool_evidence and len(agent_response.tool_evidence) > 0:
        text += "\n\n---\n*Data retrieved via Fabric using your identity.*"

    return text
