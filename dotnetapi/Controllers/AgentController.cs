using FabricObo.Models;
using FabricObo.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Identity.Web;

namespace FabricObo.Controllers;

/// <summary>
/// Main API controller — receives an authenticated user request, performs entitlement
/// lookup, acquires an OBO token, and calls the v2 Foundry Responses API.
///
/// Auth flow:
///   1. SPA acquires token for scope "api://{API_CLIENT_ID}/access_as_user"
///   2. This controller validates that JWT
///   3. Uses ITokenAcquisition.GetAccessTokenForUserAsync to exchange for OBO token
///      scoped to "https://ai.azure.com/.default"
///   4. Passes OBO token to FoundryAgentService for Responses API call
///
/// Docs: https://learn.microsoft.com/entra/msal/dotnet/acquiring-tokens/web-apps-apis/on-behalf-of-flow
/// </summary>
[ApiController]
[Route("api/[controller]")]
[Authorize] // Requires valid JWT bearer token
public class AgentController : ControllerBase
{
    private readonly IEntitlementService _entitlementService;
    private readonly IFoundryAgentService _foundryAgentService;
    private readonly ITokenAcquisition _tokenAcquisition;
    private readonly ILogger<AgentController> _logger;

    /// <summary>
    /// The scope used for the OBO exchange.
    /// "https://ai.azure.com/.default" is the correct scope for the v2 Foundry
    /// Agents Responses API. Maps to the Azure Machine Learning Services app.
    /// The .default suffix causes Entra to return all statically-consented
    /// delegated permissions for the downstream resource.
    /// </summary>
    private static readonly string[] FoundryScopes = ["https://ai.azure.com/.default"];

    public AgentController(
        IEntitlementService entitlementService,
        IFoundryAgentService foundryAgentService,
        ITokenAcquisition tokenAcquisition,
        ILogger<AgentController> logger)
    {
        _entitlementService = entitlementService;
        _foundryAgentService = foundryAgentService;
        _tokenAcquisition = tokenAcquisition;
        _logger = logger;
    }

    /// <summary>
    /// POST /api/agent
    /// Accepts a user question, looks up entitlement, runs Foundry agent, returns results.
    /// </summary>
    [HttpPost]
    [ProducesResponseType(typeof(AgentResponse), StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status401Unauthorized)]
    [ProducesResponseType(StatusCodes.Status500InternalServerError)]
    public async Task<IActionResult> Post(
        [FromBody] AgentRequest request,
        CancellationToken cancellationToken)
    {
        // Generate a correlation ID for tracing across all downstream calls
        var correlationId = Guid.NewGuid().ToString("N")[..12];

        // ──────────────────────────────────────────────────────────
        // Extract user claims from the validated JWT
        // "preferred_username" = UPN, "oid" = object ID
        // ──────────────────────────────────────────────────────────
        var upn = User.GetDisplayName()
                  ?? User.FindFirst("preferred_username")?.Value
                  ?? User.FindFirst("upn")?.Value
                  ?? "unknown";

        var oid = User.FindFirst("http://schemas.microsoft.com/identity/claims/objectidentifier")?.Value
                  ?? User.FindFirst("oid")?.Value
                  ?? "unknown";

        _logger.LogInformation(
            "[{CorrelationId}] Request from UPN={Upn}, OID={Oid}, Question='{Question}'",
            correlationId, upn, oid, Truncate(request.Question, 100));

        // ──────────────────────────────────────────────────────────
        // Step 1: Entitlement lookup (advisory, not enforcement)
        // Maps UPN → RepCode for logging & optional UX hints.
        // Enforcement is by Fabric RLS — see docs/SECURITY.md.
        // ──────────────────────────────────────────────────────────
        EntitlementResult entitlement;
        try
        {
            entitlement = await _entitlementService.GetEntitlementAsync(upn, oid);
            _logger.LogInformation(
                "[{CorrelationId}] Entitlement: RepCode={RepCode}, Role={Role}, Authorized={Auth}",
                correlationId, entitlement.RepCode, entitlement.Role, entitlement.IsAuthorized);
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "[{CorrelationId}] Entitlement service error", correlationId);
            // Continue even if entitlement fails — Fabric RLS is the real gate
            entitlement = new EntitlementResult
            {
                Upn = upn,
                Oid = oid,
                IsAuthorized = true // Advisory: let Fabric decide
            };
        }

        // ──────────────────────────────────────────────────────────
        // Step 2: Acquire OBO token for Foundry Agents API
        //
        // ITokenAcquisition.GetAccessTokenForUserAsync performs the
        // On-Behalf-Of (OBO) flow: exchanges the incoming user JWT
        // for a new token scoped to the downstream resource.
        //
        // Docs: https://learn.microsoft.com/entra/identity-platform/v2-oauth2-on-behalf-of-flow
        // ──────────────────────────────────────────────────────────
        string oboToken;
        try
        {
            oboToken = await _tokenAcquisition.GetAccessTokenForUserAsync(
                FoundryScopes,
                user: User);

            _logger.LogDebug("[{CorrelationId}] OBO token acquired for Foundry", correlationId);
        }
        catch (MicrosoftIdentityWebChallengeUserException ex)
        {
            // This occurs when consent is needed or the token cache is empty.
            // The middleware will return a 401 with claims challenge.
            _logger.LogWarning(ex,
                "[{CorrelationId}] OBO token acquisition failed — consent/challenge needed", correlationId);
            return Unauthorized(new AgentResponse
            {
                Status = "obo_challenge",
                CorrelationId = correlationId,
                Error = "Token acquisition requires user interaction (consent or re-auth). " +
                        "Check WWW-Authenticate header for claims."
            });
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "[{CorrelationId}] OBO token acquisition failed", correlationId);
            return StatusCode(500, new AgentResponse
            {
                Status = "obo_error",
                CorrelationId = correlationId,
                Error = $"Failed to acquire OBO token: {ex.Message}"
            });
        }

        // ──────────────────────────────────────────────────────────
        // Step 3: Call Foundry v2 Responses API
        // A single synchronous POST to /openai/responses with the
        // Fabric tool attached. No polling needed — the response
        // includes the complete answer.
        //
        // The Fabric tool uses identity passthrough (OBO) — the
        // bearer token carries the user's identity through to
        // Fabric, where data access is scoped to the user.
        //
        // Docs: https://learn.microsoft.com/azure/ai-foundry/agents/how-to/tools/fabric
        // ──────────────────────────────────────────────────────────
        var agentResponse = await _foundryAgentService.RunAgentAsync(
            request.Question,
            request.ConversationId,
            oboToken,
            correlationId,
            cancellationToken);

        // Attach entitlement info to the response
        agentResponse = agentResponse with { Entitlement = entitlement };

        _logger.LogInformation(
            "[{CorrelationId}] Response: Status={Status}, ConversationId={ConversationId}, ResponseId={ResponseId}",
            correlationId, agentResponse.Status, agentResponse.ConversationId, agentResponse.ResponseId);

        return Ok(agentResponse);
    }

    private static string Truncate(string value, int max)
        => value.Length <= max ? value : value[..max] + "…";
}
