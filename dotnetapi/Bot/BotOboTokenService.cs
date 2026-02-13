using Microsoft.Identity.Client;

namespace FabricObo.Bot;

/// <summary>
/// Performs On-Behalf-Of (OBO) token exchange for the bot path.
///
/// When a user interacts via Teams or Copilot Studio, the Azure Bot Service
/// OAuth connection provides a user access token scoped to this API
/// (api://{ClientId}/access_as_user). This service exchanges that token
/// for a Foundry-scoped token using MSAL's AcquireTokenOnBehalfOf.
///
/// This is the bot-path equivalent of ITokenAcquisition.GetAccessTokenForUserAsync
/// used in the SPA/controller path.
///
/// Docs: https://learn.microsoft.com/entra/identity-platform/v2-oauth2-on-behalf-of-flow
/// </summary>
public interface IBotOboTokenService
{
    /// <summary>
    /// Exchanges a user assertion token (from Azure Bot Service OAuth) for a
    /// Foundry-scoped access token via OBO flow.
    /// </summary>
    /// <param name="userToken">The user token from Azure Bot Service OAuth connection.</param>
    /// <returns>Access token scoped to https://ai.azure.com/.default</returns>
    Task<string> ExchangeTokenAsync(string userToken);
}

/// <summary>
/// MSAL-based implementation of OBO token exchange for the bot path.
///
/// Uses ConfidentialClientApplication with the same Entra app registration
/// (ClientId + ClientSecret) as the SPA path. The only difference is that
/// the incoming user assertion comes from the Bot Service OAuth connection
/// instead of from the SPA's MSAL.js.
/// </summary>
public sealed class BotOboTokenService : IBotOboTokenService
{
    private readonly IConfidentialClientApplication _msalApp;
    private readonly ILogger<BotOboTokenService> _logger;

    /// <summary>
    /// The downstream scope for OBO — same as used in AgentController.
    /// "https://ai.azure.com/.default" targets the Azure AI Foundry service.
    /// </summary>
    private static readonly string[] FoundryScopes = ["https://ai.azure.com/.default"];

    public BotOboTokenService(IConfiguration config, ILogger<BotOboTokenService> logger)
    {
        _logger = logger;

        var clientId = config["AzureAd:ClientId"]
            ?? throw new InvalidOperationException("AzureAd:ClientId is required");
        var clientSecret = config["AzureAd:ClientSecret"]
            ?? throw new InvalidOperationException("AzureAd:ClientSecret is required");
        var tenantId = config["AzureAd:TenantId"]
            ?? throw new InvalidOperationException("AzureAd:TenantId is required");
        var instance = config["AzureAd:Instance"] ?? "https://login.microsoftonline.com/";

        _msalApp = ConfidentialClientApplicationBuilder
            .Create(clientId)
            .WithClientSecret(clientSecret)
            .WithAuthority($"{instance}{tenantId}")
            .Build();
    }

    public async Task<string> ExchangeTokenAsync(string userToken)
    {
        _logger.LogDebug("Performing OBO exchange for bot-originated request");

        try
        {
            var result = await _msalApp
                .AcquireTokenOnBehalfOf(FoundryScopes, new UserAssertion(userToken))
                .ExecuteAsync();

            _logger.LogDebug("OBO exchange succeeded, token expires at {Expiry}", result.ExpiresOn);
            return result.AccessToken;
        }
        catch (MsalUiRequiredException ex)
        {
            _logger.LogWarning(ex, "OBO exchange failed — user consent or interaction required");
            throw new InvalidOperationException(
                "The user needs to grant consent for Foundry access. " +
                "Please sign out and sign back in to the bot.", ex);
        }
        catch (MsalServiceException ex)
        {
            _logger.LogError(ex, "OBO exchange failed — MSAL service error");
            throw new InvalidOperationException(
                $"Token exchange failed: {ex.Message}", ex);
        }
    }
}
