using FabricObo.Bot;
using FabricObo.Services;
using Microsoft.Bot.Builder;
using Microsoft.Bot.Builder.Integration.AspNet.Core;
using Microsoft.Bot.Connector.Authentication;
using Microsoft.Identity.Web;

var builder = WebApplication.CreateBuilder(args);

// Enable PII logging for debugging token issues (disable in production!)
Microsoft.IdentityModel.Logging.IdentityModelEventSource.ShowPII = true;

// ══════════════════════════════════════════════════════════════════
// 1. Authentication — Microsoft Identity Web
//
// Validates incoming JWT bearer tokens issued by Entra ID.
// Configures the OBO token cache so ITokenAcquisition can exchange
// the user's token for a Foundry-scoped token.
//
// Docs: https://learn.microsoft.com/entra/msal/dotnet/acquiring-tokens/web-apps-apis/on-behalf-of-flow
// ══════════════════════════════════════════════════════════════════
builder.Services
    .AddMicrosoftIdentityWebApiAuthentication(builder.Configuration, "AzureAd")
    .EnableTokenAcquisitionToCallDownstreamApi()
    .AddInMemoryTokenCaches();
// NOTE: For production, replace AddInMemoryTokenCaches() with
// AddDistributedTokenCaches() backed by Redis or SQL for multi-instance IIS.

// ══════════════════════════════════════════════════════════════════
// 2. Application services
// ══════════════════════════════════════════════════════════════════

// Foundry options from appsettings.json
builder.Services.Configure<FoundryOptions>(
    builder.Configuration.GetSection(FoundryOptions.SectionName));

// Entitlement service — stub for POC, swap for real DB implementation
builder.Services.AddSingleton<IEntitlementService, StubEntitlementService>();

// Foundry agent service — implements the Agents REST API workflow
builder.Services.AddScoped<IFoundryAgentService, FoundryAgentService>();

// ══════════════════════════════════════════════════════════════════
// 3. HttpClientFactory — proper socket management
//
// Named client "FoundryAgent" is used by FoundryAgentService.
// Base address and auth header are set per-call (OBO token varies).
//
// Docs: https://learn.microsoft.com/dotnet/core/extensions/httpclient-factory
// ══════════════════════════════════════════════════════════════════
builder.Services.AddHttpClient("FoundryAgent", client =>
{
    client.Timeout = TimeSpan.FromSeconds(180); // Agent runs can take time
    client.DefaultRequestHeaders.Add("Accept", "application/json");
});

// ══════════════════════════════════════════════════════════════════
// 4. Controllers + JSON options
// ══════════════════════════════════════════════════════════════════
builder.Services.AddControllers()
    .AddJsonOptions(opts =>
    {
        opts.JsonSerializerOptions.PropertyNamingPolicy =
            System.Text.Json.JsonNamingPolicy.CamelCase;
        opts.JsonSerializerOptions.DefaultIgnoreCondition =
            System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull;
    });

// Swagger for dev/testing
builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen();

// ══════════════════════════════════════════════════════════════════
// 4b. Bot Framework — Teams / Copilot Studio integration
//
// The CloudAdapter handles Bot Framework protocol authentication.
// FabricOboBot processes messages, performs SSO token exchange,
// then calls the same IFoundryAgentService used by the SPA path.
//
// Endpoint: POST /api/messages
//
// Docs: https://learn.microsoft.com/azure/bot-service/bot-builder-basics
// ══════════════════════════════════════════════════════════════════
// ConfigurationBotFrameworkAuthentication reads MicrosoftAppId/Password/Type/TenantId
// from config root. We bind the "Bot" section values to root-level keys so the
// existing nested config structure works without duplication.
var botSection = builder.Configuration.GetSection("Bot");
builder.Configuration["MicrosoftAppId"] = botSection["MicrosoftAppId"];
builder.Configuration["MicrosoftAppPassword"] = botSection["MicrosoftAppPassword"];
builder.Configuration["MicrosoftAppTenantId"] = botSection["MicrosoftAppTenantId"];
builder.Configuration["MicrosoftAppType"] = botSection["MicrosoftAppType"];

builder.Services.AddSingleton<BotFrameworkAuthentication, ConfigurationBotFrameworkAuthentication>();
builder.Services.AddSingleton<IBotFrameworkHttpAdapter>(sp =>
{
    var auth = sp.GetRequiredService<BotFrameworkAuthentication>();
    var logger = sp.GetRequiredService<ILogger<CloudAdapter>>();
    var adapter = new CloudAdapter(auth, logger);

    // Global error handler — logs errors and sends a friendly message to the user.
    // Without this, the CloudAdapter silently swallows exceptions.
    adapter.OnTurnError = async (turnContext, exception) =>
    {
        logger.LogError(exception, "Bot unhandled exception: {Message}", exception.Message);
        await turnContext.SendActivityAsync("Sorry, something went wrong. Please try again.");
    };

    return adapter;
});
builder.Services.AddSingleton<IBotOboTokenService, BotOboTokenService>();
builder.Services.AddTransient<IBot, FabricOboBot>();

// ══════════════════════════════════════════════════════════════════
// 5. Logging — structured logging with correlation IDs
// ══════════════════════════════════════════════════════════════════
builder.Logging.AddConsole();
builder.Logging.AddDebug();

// ══════════════════════════════════════════════════════════════════
// 6. CORS (for SPA development)
// ══════════════════════════════════════════════════════════════════
builder.Services.AddCors(options =>
{
    options.AddDefaultPolicy(policy =>
    {
        var origins = builder.Configuration.GetSection("Cors:AllowedOrigins").Get<string[]>()
                      ?? ["http://localhost:3000"];
        policy.WithOrigins(origins)
              .AllowAnyHeader()
              .AllowAnyMethod();
    });
});

var app = builder.Build();

// ══════════════════════════════════════════════════════════════════
// Middleware pipeline
// ══════════════════════════════════════════════════════════════════

// Debug: log raw Authorization header and body to diagnose Copilot Studio
// Also fix missing "Bearer " prefix from Copilot Studio
app.Use(async (context, next) =>
{
    if (context.Request.Path.StartsWithSegments("/api/agent"))
    {
        var authHeader = context.Request.Headers["Authorization"].FirstOrDefault() ?? "(none)";
        var preview = authHeader.Length > 60 ? authHeader[..60] + "..." : authHeader;
        app.Logger.LogWarning("[AUTH DEBUG] Path={Path}, Auth header length={Len}, preview={Preview}",
            context.Request.Path, authHeader.Length, preview);

        // If the header is a raw JWT (starts with eyJ) without "Bearer " prefix, fix it
        if (authHeader.StartsWith("eyJ"))
        {
            context.Request.Headers["Authorization"] = $"Bearer {authHeader}";
            app.Logger.LogWarning("[AUTH DEBUG] Added missing 'Bearer ' prefix to Authorization header");
        }

        // Log request body for debugging
        context.Request.EnableBuffering();
        using var reader = new StreamReader(context.Request.Body, leaveOpen: true);
        var body = await reader.ReadToEndAsync();
        context.Request.Body.Position = 0;
        app.Logger.LogWarning("[AUTH DEBUG] Request body: {Body}", body);
    }
    await next();
});

if (app.Environment.IsDevelopment())
{
    app.UseSwagger();
    app.UseSwaggerUI();
}

app.UseHttpsRedirection();
app.UseCors();

// Authentication must come before Authorization
app.UseAuthentication();
app.UseAuthorization();

app.MapControllers();

app.Run();
