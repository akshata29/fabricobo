using FabricObo.Models;

namespace FabricObo.Services;

/// <summary>
/// Abstraction over Azure AI Foundry v2 Responses API.
/// Encapsulates conversation management and the Responses API call.
/// </summary>
public interface IFoundryAgentService
{
    /// <summary>
    /// Runs the v2 Foundry agent workflow:
    /// 1. Create conversation (or reuse existing)
    /// 2. Call Responses API with Fabric tool (synchronous)
    /// 3. Parse response — answer + tool evidence
    /// </summary>
    Task<AgentResponse> RunAgentAsync(
        string question,
        string? conversationId,
        string oboAccessToken,
        string correlationId,
        CancellationToken cancellationToken = default);
}
