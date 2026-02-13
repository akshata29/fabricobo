# ════════════════════════════════════════════════════════════════
# Models — Request / Response DTOs
#
# Equivalent of C# Models/ folder (AgentRequest, AgentResponse,
# EntitlementResult).
#
# Uses Pydantic for automatic serialization / validation,
# mirroring the .NET JsonSerializerOptions(CamelCase).
# ════════════════════════════════════════════════════════════════
from pydantic import BaseModel, ConfigDict
from typing import Optional


def _to_camel(name: str) -> str:
    """Convert snake_case to camelCase for JSON serialization."""
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


class AgentRequest(BaseModel):
    """
    Inbound request body from the client.
    Equivalent of FabricObo.Models.AgentRequest in .NET.
    """

    question: str
    """The user's natural-language question, forwarded to the Foundry agent."""

    conversation_id: Optional[str] = None
    """Optional conversation ID to continue a previous conversation."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=lambda s: _to_camel(s))


class ToolUsageSummary(BaseModel):
    """
    Summary of a tool usage item from the v2 Responses API output.
    Equivalent of FabricObo.Models.ToolUsageSummary in .NET.
    """

    item_id: str
    """Output item ID from the response."""

    type: str
    """Output item type (e.g. 'fabric_dataagent_preview_call')."""

    status: str
    """Status of the tool call."""

    detail: Optional[str] = None
    """Raw JSON snippet of the tool call, truncated for logging."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=lambda s: _to_camel(s))


class EntitlementResult(BaseModel):
    """
    Result of the entitlement DB lookup.
    Advisory only — enforcement is by Fabric RLS.
    Equivalent of FabricObo.Models.EntitlementResult in .NET.
    """

    upn: str
    """User principal name from the JWT."""

    oid: str
    """Object ID (oid) from the JWT."""

    rep_code: Optional[str] = None
    """Internal representative code mapped from the user."""

    role: Optional[str] = None
    """Human-readable role or permission level."""

    is_authorized: bool = True
    """Whether the user is authorized (advisory check)."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=lambda s: _to_camel(s))


class AgentResponse(BaseModel):
    """
    Response returned to the client for every /api/agent call.
    Equivalent of FabricObo.Models.AgentResponse in .NET.
    """

    status: str
    correlation_id: str
    conversation_id: Optional[str] = None
    response_id: Optional[str] = None
    assistant_answer: Optional[str] = None
    tool_evidence: Optional[list[ToolUsageSummary]] = None
    entitlement: Optional[EntitlementResult] = None
    error: Optional[str] = None

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=lambda s: _to_camel(s),
        json_schema_extra={"exclude_none": True},
    )
