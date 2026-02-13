using Microsoft.AspNetCore.Mvc;
using Microsoft.Bot.Builder;
using Microsoft.Bot.Builder.Integration.AspNet.Core;

namespace FabricObo.Bot;

/// <summary>
/// Endpoint for Azure Bot Service / Teams / Copilot Studio messages.
///
/// All Bot Framework protocol messages (activities) arrive at POST /api/messages.
/// The CloudAdapter handles protocol-level concerns (auth validation,
/// activity deserialization, response serialization); the IBot implementation
/// (FabricOboBot) handles the business logic.
///
/// This endpoint is SEPARATE from the SPA's /api/agent endpoint.
/// Both paths share the same IFoundryAgentService + OBO flow underneath.
///
///   SPA path:    POST /api/agent   → AgentController  → ITokenAcquisition OBO → FoundryAgentService
///   Bot path:    POST /api/messages → BotController    → BotOboTokenService OBO → FoundryAgentService
///
/// Docs: https://learn.microsoft.com/azure/bot-service/bot-builder-basics
/// </summary>
[Route("api/messages")]
[ApiController]
public class BotController : ControllerBase
{
    private readonly IBotFrameworkHttpAdapter _adapter;
    private readonly IBot _bot;

    public BotController(IBotFrameworkHttpAdapter adapter, IBot bot)
    {
        _adapter = adapter;
        _bot = bot;
    }

    /// <summary>
    /// Receives Bot Framework activities from Azure Bot Service.
    /// No [Authorize] attribute — the CloudAdapter handles Bot Framework
    /// authentication (validates the Bot Connector service token).
    /// </summary>
    [HttpPost]
    public async Task PostAsync()
    {
        await _adapter.ProcessAsync(Request, Response, _bot);
    }
}
