using FabricObo.Services;
using Microsoft.Identity.Web;

var builder = WebApplication.CreateBuilder(args);

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
