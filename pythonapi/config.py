# ════════════════════════════════════════════════════════════════
# Configuration — Pydantic Settings
#
# Loads all configuration from .env file or environment variables.
# Equivalent of appsettings.json + IConfiguration in .NET.
#
# Docs: https://docs.pydantic.dev/latest/concepts/pydantic_settings/
# ════════════════════════════════════════════════════════════════
from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional


class AzureAdSettings(BaseSettings):
    """Entra ID (Azure AD) configuration for JWT validation and OBO flow."""

    instance: str = "https://login.microsoftonline.com/"
    tenant_id: str = ""
    client_id: str = ""
    client_secret: str = ""
    audience: str = ""
    scopes: str = "access_as_user"

    model_config = {"env_prefix": "AZUREAD_"}


class FoundrySettings(BaseSettings):
    """Azure AI Foundry Agent configuration (v2 Responses API)."""

    project_endpoint: str = ""
    model_deployment_name: str = "chat4o"
    fabric_connection_id: Optional[str] = None
    agent_name: Optional[str] = None
    instructions: str = (
        "You are a helpful data analysis assistant. "
        "For any questions about data, accounts, sales, or reports, use the Fabric tool. "
        "Always provide clear, concise answers based on the data returned."
    )
    api_version: str = "2025-05-15-preview"
    response_timeout_seconds: int = 180

    model_config = {"env_prefix": "FOUNDRY_"}


class SpaAuthSettings(BaseSettings):
    """SPA authentication config served via /api/config (public values, not secrets)."""

    tenant_id: str = ""
    spa_client_id: str = ""
    api_client_id: str = ""

    # Test users as JSON string, e.g. '[{"label":"User A","upn":"a@t.com","description":"REP001"}]'
    test_users_json: str = "[]"

    model_config = {"env_prefix": "SPA_"}


class BotSettings(BaseSettings):
    """Bot Framework configuration for Teams / Copilot Studio."""

    microsoft_app_id: str = ""
    microsoft_app_password: str = ""
    microsoft_app_tenant_id: str = ""
    microsoft_app_type: str = "SingleTenant"
    oauth_connection_name: str = ""

    model_config = {"env_prefix": "BOT_"}


class CorsSettings(BaseSettings):
    """CORS configuration."""

    allowed_origins: str = "http://localhost:3000,http://localhost:5173"

    model_config = {"env_prefix": "CORS_"}

    def get_origins_list(self) -> list[str]:
        """Parse comma-separated origins into a list."""
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


class FabricDirectSettings(BaseSettings):
    """
    Settings for the direct Fabric Data Agent path (MCP concurrency-theory test).

    This path replaces Foundry's built-in fabric_dataagent_preview tool with a
    custom MCP server that calls the Fabric Assistants API directly and creates a
    NEW Fabric thread per request — potentially unlocking concurrent same-user runs.

    Required env vars:
      FABRIC_DIRECT_DATA_AGENT_URL  — Published URL of your Fabric Data Agent, e.g.:
                                       https://{tenant}.pbidedicated.windows.net/
                                        aiskills/{workspace}/aiassistant/{agent}/openai
      FABRIC_DIRECT_API_PUBLIC_URL  — Public URL of this API (for Foundry to call back
                                       to /mcp).  In dev: your devtunnel URL.
                                       In production: your deployed API URL.
    """

    # Full URL of the published Fabric Data Agent (same URL used in fabric_data_agent_client)
    data_agent_url: str = ""

    # Public root URL of this API — Foundry connects to {api_public_url}/mcp as the MCP server
    # Dev: https://your-tunnel-subdomain.devtunnels.ms
    # Prod: https://your-api.azurewebsites.net
    api_public_url: str = ""

    # Timeout (seconds) for a single Fabric Assistants API round-trip through the MCP path
    query_timeout_seconds: int = 120

    # Name of the Foundry agent that will be created/updated at startup with the MCP tool.
    # This agent shows up in the Foundry portal with full trace/log visibility.
    # Override via FABRIC_DIRECT_MCP_AGENT_NAME env var.
    mcp_agent_name: str = "FabricMcpAgent"

    model_config = {"env_prefix": "FABRIC_DIRECT_"}

    @property
    def mcp_server_url(self) -> str:
        """Full URL Foundry uses to reach the MCP server."""
        return self.api_public_url.rstrip("/") + "/mcp"

    @property
    def is_configured(self) -> bool:
        """True if the minimum required settings are present."""
        return bool(self.data_agent_url and self.api_public_url)
