namespace FabricObo.Models;

/// <summary>
/// Response returned to the client for every /api/agent call.
/// Contains the Foundry agent answer plus evidence of tool execution.
/// </summary>
public sealed record AgentResponse
{
    public required string Status { get; init; }
    public required string CorrelationId { get; init; }

    /// <summary>Conversation ID for multi-turn context (replaces thread ID in classic API).</summary>
    public string? ConversationId { get; init; }

    /// <summary>Response ID from the Foundry Responses API.</summary>
    public string? ResponseId { get; init; }

    /// <summary>The final text answer from the Foundry agent.</summary>
    public string? AssistantAnswer { get; init; }

    /// <summary>
    /// Summary of tool usage — proves that the Fabric tool was invoked.
    /// Docs: https://learn.microsoft.com/azure/ai-foundry/agents/how-to/tools/fabric
    /// </summary>
    public List<ToolUsageSummary>? ToolEvidence { get; init; }

    /// <summary>Entitlement data (advisory, not enforcement).</summary>
    public EntitlementResult? Entitlement { get; init; }

    /// <summary>Error details if the response failed.</summary>
    public string? Error { get; init; }
}

/// <summary>
/// Summary of a tool usage item from the v2 Responses API output.
/// The Responses API returns tool calls as output items in the response.
/// </summary>
public sealed class ToolUsageSummary
{
    /// <summary>Output item ID from the response.</summary>
    public required string ItemId { get; init; }

    /// <summary>Output item type (e.g. "fabric_dataagent_preview_call").</summary>
    public required string Type { get; init; }

    /// <summary>Status of the tool call.</summary>
    public required string Status { get; init; }

    /// <summary>Raw JSON snippet of the tool call, truncated for logging.</summary>
    public string? Detail { get; init; }
}
