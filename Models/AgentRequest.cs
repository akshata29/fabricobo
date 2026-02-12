namespace FabricObo.Models;

/// <summary>
/// Inbound request body from the client.
/// </summary>
public sealed class AgentRequest
{
    /// <summary>
    /// The user's natural-language question, forwarded to the Foundry agent.
    /// Example: "Show me all my accounts and their balances."
    /// </summary>
    public required string Question { get; init; }

    /// <summary>
    /// Optional conversation ID to continue a previous conversation.
    /// If null, a new conversation is created.
    /// (Replaces ThreadId from the classic API.)
    /// </summary>
    public string? ConversationId { get; init; }
}
