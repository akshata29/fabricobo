using FabricObo.Models;
using FabricObo.Services;
using Microsoft.Bot.Builder;
using Microsoft.Bot.Builder.Teams;
using Microsoft.Bot.Connector.Authentication;
using Microsoft.Bot.Schema;

namespace FabricObo.Bot;

/// <summary>
/// Bot that handles messages from Teams and Copilot Studio.
///
/// Auth flow (replaces the SPA path):
///   1. User sends message in Teams / Copilot Studio
///   2. Bot requests user token via Azure Bot Service OAuth connection
///      (configured to get a token for api://{API_CLIENT_ID}/access_as_user)
///   3. BotOboTokenService exchanges that for a Foundry-scoped OBO token
///   4. FoundryAgentService calls the Foundry Responses API with user identity
///
/// The Fabric RLS enforcement is identical to the SPA path — the OBO token
/// carries the user's identity through to Fabric.
///
/// Docs:
///   - Teams SSO: https://learn.microsoft.com/microsoftteams/platform/bots/how-to/authentication/bot-sso-overview
///   - Copilot Studio: https://learn.microsoft.com/microsoft-copilot-studio/advanced-bot-framework-composer
/// </summary>
public class FabricOboBot : TeamsActivityHandler
{
    private readonly IFoundryAgentService _foundryService;
    private readonly IEntitlementService _entitlementService;
    private readonly IBotOboTokenService _oboTokenService;
    private readonly ILogger<FabricOboBot> _logger;
    private readonly string _connectionName;

    public FabricOboBot(
        IFoundryAgentService foundryService,
        IEntitlementService entitlementService,
        IBotOboTokenService oboTokenService,
        IConfiguration config,
        ILogger<FabricOboBot> logger)
    {
        _foundryService = foundryService;
        _entitlementService = entitlementService;
        _oboTokenService = oboTokenService;
        _logger = logger;

        // The OAuth connection name configured in Azure Bot Service
        // that exchanges Teams SSO token for api://{ClientId}/access_as_user
        _connectionName = config["Bot:OAuthConnectionName"]
            ?? throw new InvalidOperationException("Bot:OAuthConnectionName is required");
    }

    /// <summary>
    /// Handles incoming messages from Teams or Copilot Studio.
    /// </summary>
    protected override async Task OnMessageActivityAsync(
        ITurnContext<IMessageActivity> turnContext,
        CancellationToken cancellationToken)
    {
        var correlationId = Guid.NewGuid().ToString("N")[..12];
        var question = turnContext.Activity.Text?.Trim();

        if (string.IsNullOrEmpty(question))
        {
            await turnContext.SendActivityAsync(
                "Please send me a question about your data.",
                cancellationToken: cancellationToken);
            return;
        }

        _logger.LogInformation(
            "[{CorrelationId}] Bot message from {From}, Channel={Channel}, Question='{Question}'",
            correlationId,
            turnContext.Activity.From?.Name ?? "unknown",
            turnContext.Activity.ChannelId,
            Truncate(question, 100));

        // ──────────────────────────────────────────────────────────
        // Step 1: Get user token via Azure Bot Service OAuth
        // The Bot Service handles the SSO flow with Teams/Copilot Studio.
        // The OAuth connection is configured to request the scope
        // api://{ClientId}/access_as_user — same scope the SPA requests.
        //
        // If the message is a 6-digit magic code from the sign-in flow,
        // pass it to GetUserTokenAsync to complete token redemption.
        // ──────────────────────────────────────────────────────────
        var userTokenClient = turnContext.TurnState.Get<UserTokenClient>();
        if (userTokenClient == null)
        {
            _logger.LogError("[{CorrelationId}] UserTokenClient not available", correlationId);
            await turnContext.SendActivityAsync(
                "Authentication is not configured. Please contact your administrator.",
                cancellationToken: cancellationToken);
            return;
        }

        // Detect if the message is a magic code (6-digit number from sign-in flow)
        string? magicCode = null;
        if (question.Length == 6 && int.TryParse(question, out _))
        {
            magicCode = question;
            _logger.LogInformation("[{CorrelationId}] Detected magic code, completing sign-in", correlationId);
        }

        var tokenResponse = await userTokenClient.GetUserTokenAsync(
            turnContext.Activity.From?.Id ?? "unknown",
            _connectionName,
            turnContext.Activity.ChannelId,
            magicCode,
            cancellationToken);

        if (tokenResponse?.Token == null)
        {
            _logger.LogInformation("[{CorrelationId}] No token cached — sending sign-in card", correlationId);
            await SendSignInCardAsync(turnContext, userTokenClient, cancellationToken);
            return;
        }

        // If the user just entered a magic code, they're now signed in — prompt for a question
        if (magicCode != null)
        {
            _logger.LogInformation("[{CorrelationId}] Sign-in completed via magic code", correlationId);
            await turnContext.SendActivityAsync(
                "You're signed in! Please type your question now.",
                cancellationToken: cancellationToken);
            return;
        }

        // ──────────────────────────────────────────────────────────
        // Step 2: Send typing indicator while processing
        // ──────────────────────────────────────────────────────────
        await turnContext.SendActivityAsync(
            new Activity { Type = ActivityTypes.Typing },
            cancellationToken);

        try
        {
            // ──────────────────────────────────────────────────────
            // Step 3: Exchange user token for Foundry OBO token
            // Same exchange the SPA path does via ITokenAcquisition
            // ──────────────────────────────────────────────────────
            var oboToken = await _oboTokenService.ExchangeTokenAsync(tokenResponse.Token);
            _logger.LogDebug("[{CorrelationId}] OBO token acquired via bot path", correlationId);

            // ──────────────────────────────────────────────────────
            // Step 4: Call Foundry agent (same service as SPA path)
            // ──────────────────────────────────────────────────────
            var agentResponse = await _foundryService.RunAgentAsync(
                question,
                GetConversationId(turnContext),
                oboToken,
                correlationId,
                cancellationToken);

            _logger.LogInformation(
                "[{CorrelationId}] Bot response: Status={Status}",
                correlationId, agentResponse.Status);

            // ──────────────────────────────────────────────────────
            // Step 5: Format and send the response
            // ──────────────────────────────────────────────────────
            var reply = FormatBotResponse(agentResponse);
            await turnContext.SendActivityAsync(reply, cancellationToken);
        }
        catch (InvalidOperationException ex) when (ex.Message.Contains("consent"))
        {
            _logger.LogWarning(ex, "[{CorrelationId}] Consent required", correlationId);
            await turnContext.SendActivityAsync(
                "I need additional permissions to access your data. " +
                "Please sign out and sign back in using the command 'signout'.",
                cancellationToken: cancellationToken);
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "[{CorrelationId}] Bot processing error", correlationId);
            await turnContext.SendActivityAsync(
                "Sorry, I encountered an error processing your request. Please try again.",
                cancellationToken: cancellationToken);
        }
    }

    /// <summary>
    /// Handles token response events — fired when Teams SSO or OAuth card
    /// sign-in completes and provides a token back to the bot.
    /// This enables seamless sign-in without manual popup handling.
    /// </summary>
    protected override async Task OnTokenResponseEventAsync(
        ITurnContext<IEventActivity> turnContext,
        CancellationToken cancellationToken)
    {
        _logger.LogDebug("Token response event received — SSO or sign-in completed");

        // The token response event fires after sign-in completes.
        // Prompt the user to re-send their question.
        await turnContext.SendActivityAsync(
            "You're now signed in! Please type your question again.",
            cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Handles sign-out requests — type "signout" to clear cached tokens.
    /// </summary>
    protected override async Task OnTeamsSigninVerifyStateAsync(
        ITurnContext<IInvokeActivity> turnContext,
        CancellationToken cancellationToken)
    {
        _logger.LogInformation("Sign-in verify state received");
        await turnContext.SendActivityAsync(
            new Activity
            {
                Type = ActivityTypesEx.InvokeResponse,
                Value = new InvokeResponse { Status = 200 }
            },
            cancellationToken);
    }

    /// <summary>
    /// Greets new members added to the conversation.
    /// </summary>
    protected override async Task OnMembersAddedAsync(
        IList<ChannelAccount> membersAdded,
        ITurnContext<IConversationUpdateActivity> turnContext,
        CancellationToken cancellationToken)
    {
        foreach (var member in membersAdded)
        {
            if (member.Id != turnContext.Activity.Recipient.Id)
            {
                await turnContext.SendActivityAsync(
                    "Hello! I'm the Fabric Data Assistant. Ask me questions about your data " +
                    "and I'll query it using your identity so you only see what you're authorized for.\n\n" +
                    "Try: *\"Show me all my accounts and their balances.\"*",
                    cancellationToken: cancellationToken);
            }
        }
    }

    // ═══════════════════════════════════════════════════════════════
    // Private helpers
    // ═══════════════════════════════════════════════════════════════

    /// <summary>
    /// Sends an OAuth sign-in card when no cached token is available.
    /// In Teams this triggers the SSO popup; in other channels it shows a sign-in button.
    /// </summary>
    private async Task SendSignInCardAsync(
        ITurnContext turnContext,
        UserTokenClient userTokenClient,
        CancellationToken cancellationToken)
    {
        var signInResource = await userTokenClient.GetSignInResourceAsync(
            _connectionName,
            turnContext.Activity,
            null, // final redirect
            cancellationToken);

        var card = new HeroCard
        {
            Title = "Sign In Required",
            Text = "Please sign in to access your data.",
            Buttons =
            [
                new CardAction
                {
                    Type = ActionTypes.Signin,
                    Title = "Sign In",
                    Value = signInResource.SignInLink
                }
            ]
        };

        var reply = MessageFactory.Attachment(card.ToAttachment());
        await turnContext.SendActivityAsync(reply, cancellationToken);
    }

    /// <summary>
    /// Returns the Foundry conversation ID for multi-turn sessions.
    /// We pass null to let Foundry create and manage its own conversation IDs,
    /// since Bot Framework conversation IDs are not valid Foundry identifiers.
    /// </summary>
    private static string? GetConversationId(ITurnContext turnContext)
    {
        // Don't pass the Bot Framework conversation ID to Foundry —
        // its format (e.g. "JNI6VEKpyk686IHT65T7UB-us") is rejected.
        // Passing null lets Foundry create a new conversation each time.
        // TODO: For multi-turn support, store the Foundry-returned
        // conversationId in bot state and map it to the BF conversation.
        return null;
    }

    /// <summary>
    /// Formats the Foundry agent response for display in Teams / Copilot Studio.
    /// </summary>
    private static IActivity FormatBotResponse(AgentResponse response)
    {
        if (response.Status != "completed" || string.IsNullOrEmpty(response.AssistantAnswer))
        {
            return MessageFactory.Text(
                response.Error ?? "I wasn't able to get an answer. Please try rephrasing your question.");
        }

        // Build a response with the answer and optional tool evidence
        var text = response.AssistantAnswer;

        if (response.ToolEvidence?.Count > 0)
        {
            text += "\n\n---\n*Data retrieved via Fabric using your identity.*";
        }

        return MessageFactory.Text(text);
    }

    private static string Truncate(string value, int max)
        => value.Length <= max ? value : value[..max] + "…";
}

/// <summary>
/// Token exchange request model for Teams SSO deserialization.
/// </summary>
public sealed class TokenExchangeInvokeRequest
{
    public string? Id { get; set; }
    public string? ConnectionName { get; set; }
    public string? Token { get; set; }
}
