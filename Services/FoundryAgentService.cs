using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using FabricObo.Models;
using Microsoft.Extensions.Options;

namespace FabricObo.Services;

/// <summary>
/// Configuration for the Foundry Agent Service (v2 Responses API).
/// Bound from appsettings.json "Foundry" section.
/// </summary>
public sealed class FoundryOptions
{
    public const string SectionName = "Foundry";

    /// <summary>
    /// Foundry project endpoint, e.g. "https://myaccount.services.ai.azure.com/api/projects/myproject"
    /// Docs: https://learn.microsoft.com/azure/ai-foundry/quickstarts/get-started-code
    /// </summary>
    public required string ProjectEndpoint { get; set; }

    /// <summary>
    /// Model deployment name for orchestration, e.g. "chat4o".
    /// </summary>
    public required string ModelDeploymentName { get; set; }

    /// <summary>
    /// Full ARM resource ID of the Fabric connection in the Foundry project.
    /// Format: /subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.CognitiveServices/accounts/{account}/projects/{project}/connections/{connectionName}
    /// Docs: https://learn.microsoft.com/azure/ai-foundry/agents/how-to/tools/fabric
    /// </summary>
    public string? FabricConnectionId { get; set; }

    /// <summary>
    /// Optional: Named agent reference. If set, the Responses API will reference
    /// this pre-configured agent instead of passing inline tools.
    /// </summary>
    public string? AgentName { get; set; }

    /// <summary>
    /// Agent instructions sent with each Responses API call (when not using a named agent).
    /// Should include guidance for when to use the Fabric tool.
    /// </summary>
    public string Instructions { get; set; } =
        "You are a helpful data analysis assistant. " +
        "For any questions about data, accounts, sales, or reports, use the Fabric tool. " +
        "Always provide clear, concise answers based on the data returned.";

    /// <summary>
    /// API version for the Foundry Responses REST API.
    /// </summary>
    public string ApiVersion { get; set; } = "2025-05-15-preview";

    /// <summary>
    /// HTTP timeout for the Responses API call in seconds.
    /// The v2 Responses API is synchronous, so this replaces polling.
    /// </summary>
    public int ResponseTimeoutSeconds { get; set; } = 180;
}

/// <summary>
/// Implements the v2 Foundry Agents Responses API workflow:
///   POST /openai/conversations              (create conversation for multi-turn)
///   POST /openai/responses                  (send question with Fabric tool, get answer)
///
/// Key differences from the classic API:
///   - Synchronous responses — no polling loop
///   - Uses the Responses API instead of threads/messages/runs
///   - Fabric tool specified via "fabric_dataagent_preview" tool type
///   - Conversations replace threads for multi-turn context
///
/// Identity passthrough (OBO) is handled by the Fabric tool at the service layer.
///
/// Docs:
///   https://learn.microsoft.com/azure/ai-foundry/quickstarts/get-started-code
///   https://learn.microsoft.com/azure/ai-foundry/agents/how-to/tools/fabric
/// </summary>
public sealed class FoundryAgentService : IFoundryAgentService
{
    private readonly IHttpClientFactory _httpClientFactory;
    private readonly FoundryOptions _options;
    private readonly ILogger<FoundryAgentService> _logger;

    public FoundryAgentService(
        IHttpClientFactory httpClientFactory,
        IOptions<FoundryOptions> options,
        ILogger<FoundryAgentService> logger)
    {
        _httpClientFactory = httpClientFactory;
        _options = options.Value;
        _logger = logger;
    }

    public async Task<AgentResponse> RunAgentAsync(
        string question,
        string? conversationId,
        string oboAccessToken,
        string correlationId,
        CancellationToken cancellationToken = default)
    {
        var client = CreateClient(oboAccessToken);

        try
        {
            // ──────────────────────────────────────────────────────────
            // Step 1: Create conversation if not provided (for multi-turn)
            // Docs: Conversations provide context across multiple turns.
            // ──────────────────────────────────────────────────────────
            conversationId ??= await CreateConversationAsync(client, correlationId, cancellationToken);

            // ──────────────────────────────────────────────────────────
            // Step 2: Call the Responses API
            //
            // The v2 API is synchronous — a single POST returns the
            // complete response including any tool execution results.
            //
            // The Fabric tool uses identity passthrough (OBO) — the
            // bearer token carries the user's identity through to
            // Fabric, where data access is scoped to the user.
            //
            // Docs: https://learn.microsoft.com/azure/ai-foundry/agents/how-to/tools/fabric
            // ──────────────────────────────────────────────────────────
            var responseJson = await CallResponsesApiAsync(
                client, question, conversationId, correlationId, cancellationToken);

            // ──────────────────────────────────────────────────────────
            // Step 3: Parse the response
            // ──────────────────────────────────────────────────────────
            var responseId = responseJson["id"]?.GetValue<string>();
            var status = responseJson["status"]?.GetValue<string>() ?? "unknown";

            if (status != "completed")
            {
                var errorDetail = responseJson["error"]?.ToJsonString();
                _logger.LogWarning(
                    "[{CorrelationId}] Response {ResponseId} ended with status: {Status}, error: {Error}",
                    correlationId, responseId, status, errorDetail);

                return new AgentResponse
                {
                    Status = status,
                    CorrelationId = correlationId,
                    ConversationId = conversationId,
                    ResponseId = responseId,
                    Error = $"Agent response ended with status: {status}. {errorDetail}"
                };
            }

            // Extract answer and tool evidence from the output array
            var (answer, toolEvidence) = ParseOutput(responseJson, correlationId);

            _logger.LogInformation(
                "[{CorrelationId}] Response completed. ConversationId={ConversationId}, " +
                "ResponseId={ResponseId}, ToolSteps={StepCount}, AnswerLength={AnswerLen}",
                correlationId, conversationId, responseId,
                toolEvidence.Count, answer?.Length ?? 0);

            return new AgentResponse
            {
                Status = "completed",
                CorrelationId = correlationId,
                ConversationId = conversationId,
                ResponseId = responseId,
                AssistantAnswer = answer,
                ToolEvidence = toolEvidence
            };
        }
        catch (HttpRequestException ex)
        {
            _logger.LogError(ex,
                "[{CorrelationId}] Foundry API HTTP error. ConversationId={ConversationId}",
                correlationId, conversationId);

            return new AgentResponse
            {
                Status = "error",
                CorrelationId = correlationId,
                ConversationId = conversationId,
                Error = $"Foundry API error: {ex.StatusCode} — {ex.Message}"
            };
        }
        catch (TaskCanceledException ex) when (ex.InnerException is TimeoutException)
        {
            _logger.LogError(ex,
                "[{CorrelationId}] Foundry API timed out after {Timeout}s",
                correlationId, _options.ResponseTimeoutSeconds);

            return new AgentResponse
            {
                Status = "timeout",
                CorrelationId = correlationId,
                ConversationId = conversationId,
                Error = $"Foundry API timed out after {_options.ResponseTimeoutSeconds}s"
            };
        }
    }

    // ================================================================
    // Private helpers
    // ================================================================

    private HttpClient CreateClient(string oboToken)
    {
        var client = _httpClientFactory.CreateClient("FoundryAgent");
        client.BaseAddress = new Uri(_options.ProjectEndpoint.TrimEnd('/') + "/");
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", oboToken);
        client.Timeout = TimeSpan.FromSeconds(_options.ResponseTimeoutSeconds);
        return client;
    }

    /// <summary>POST /openai/conversations — create a conversation context for multi-turn.</summary>
    private async Task<string> CreateConversationAsync(
        HttpClient client, string correlationId, CancellationToken ct)
    {
        _logger.LogDebug("[{CorrelationId}] Creating new conversation", correlationId);

        var resp = await client.PostAsync(
            $"openai/conversations?api-version={_options.ApiVersion}",
            new StringContent("{}", Encoding.UTF8, "application/json"),
            ct);

        resp.EnsureSuccessStatusCode();
        var json = await JsonNode.ParseAsync(await resp.Content.ReadAsStreamAsync(ct), cancellationToken: ct);
        var convId = json!["id"]!.GetValue<string>();

        _logger.LogInformation("[{CorrelationId}] Conversation created: {ConversationId}",
            correlationId, convId);
        return convId;
    }

    /// <summary>
    /// POST /openai/responses — call the Responses API with the Fabric tool.
    ///
    /// Supports two modes:
    ///   1. Inline tools: Pass model + instructions + Fabric tool config directly
    ///   2. Named agent: Reference a pre-configured agent by name
    /// </summary>
    private async Task<JsonNode> CallResponsesApiAsync(
        HttpClient client, string question, string conversationId,
        string correlationId, CancellationToken ct)
    {
        _logger.LogDebug("[{CorrelationId}] Calling Responses API with question: {Question}",
            correlationId, Truncate(question, 100));

        JsonNode requestBody;

        if (!string.IsNullOrEmpty(_options.AgentName))
        {
            // Named agent reference — agent has model + tools pre-configured in Foundry portal
            _logger.LogDebug("[{CorrelationId}] Using named agent: {AgentName}",
                correlationId, _options.AgentName);

            requestBody = new JsonObject
            {
                ["input"] = question,
                ["conversation"] = conversationId,
                ["tool_choice"] = "auto",
                ["agent"] = new JsonObject
                {
                    ["name"] = _options.AgentName,
                    ["type"] = "agent_reference"
                }
            };
        }
        else
        {
            // Inline tools mode — pass model + tools directly in the request
            _logger.LogDebug("[{CorrelationId}] Using inline Fabric tool with model: {Model}",
                correlationId, _options.ModelDeploymentName);

            requestBody = new JsonObject
            {
                ["model"] = _options.ModelDeploymentName,
                ["input"] = question,
                ["instructions"] = _options.Instructions,
                ["conversation"] = conversationId,
                ["tool_choice"] = string.IsNullOrEmpty(_options.FabricConnectionId) ? "auto" : "required"
            };

            // Add Fabric tool if connection ID is configured
            if (!string.IsNullOrEmpty(_options.FabricConnectionId))
            {
                requestBody["tools"] = new JsonArray
                {
                    new JsonObject
                    {
                        ["type"] = "fabric_dataagent_preview",
                        ["fabric_dataagent_preview"] = new JsonObject
                        {
                            ["project_connections"] = new JsonArray
                            {
                                new JsonObject
                                {
                                    ["project_connection_id"] = _options.FabricConnectionId
                                }
                            }
                        }
                    }
                };

                _logger.LogDebug("[{CorrelationId}] Fabric tool attached with connection: {ConnId}",
                    correlationId, _options.FabricConnectionId);
            }
        }

        var content = new StringContent(
            requestBody.ToJsonString(),
            Encoding.UTF8,
            "application/json");

        var resp = await client.PostAsync(
            $"openai/responses?api-version={_options.ApiVersion}",
            content,
            ct);

        // Read the response body for error details before throwing
        var responseBody = await resp.Content.ReadAsStringAsync(ct);

        if (!resp.IsSuccessStatusCode)
        {
            _logger.LogError(
                "[{CorrelationId}] Responses API returned {StatusCode}: {Body}",
                correlationId, resp.StatusCode, Truncate(responseBody, 1000));

            throw new HttpRequestException(
                $"Responses API error: {resp.StatusCode} — {Truncate(responseBody, 500)}",
                null, resp.StatusCode);
        }

        var json = JsonNode.Parse(responseBody)!;

        _logger.LogInformation(
            "[{CorrelationId}] Responses API returned status={Status}, id={ResponseId}",
            correlationId,
            json["status"]?.GetValue<string>(),
            json["id"]?.GetValue<string>());

        return json;
    }

    /// <summary>
    /// Parse the v2 response output array to extract the assistant answer
    /// and any tool usage evidence.
    ///
    /// The output array may contain:
    ///   - type="message" with role="assistant" (the text answer)
    ///   - type="fabric_dataagent_preview_call" or other tool output items
    /// </summary>
    private (string? Answer, List<ToolUsageSummary> ToolEvidence) ParseOutput(
        JsonNode responseJson, string correlationId)
    {
        var output = responseJson["output"]?.AsArray();
        if (output == null || output.Count == 0)
            return (null, []);

        string? answer = null;
        var toolEvidence = new List<ToolUsageSummary>();

        foreach (var item in output)
        {
            var type = item!["type"]?.GetValue<string>();

            switch (type)
            {
                case "message":
                {
                    var role = item["role"]?.GetValue<string>();
                    if (role == "assistant")
                    {
                        var contentArray = item["content"]?.AsArray();
                        if (contentArray != null)
                        {
                            var texts = new List<string>();
                            foreach (var block in contentArray)
                            {
                                var blockType = block!["type"]?.GetValue<string>();
                                if (blockType == "output_text")
                                {
                                    var text = block["text"]?.GetValue<string>();
                                    if (!string.IsNullOrEmpty(text))
                                        texts.Add(text);
                                }
                            }
                            answer = texts.Count > 0 ? string.Join("\n", texts) : null;
                        }
                    }
                    break;
                }

                // Tool usage output items — various types depending on tool
                case "fabric_dataagent_preview_call":
                case "tool_call":
                case "function_call":
                {
                    var toolId = item["id"]?.GetValue<string>() ?? "unknown";
                    _logger.LogInformation(
                        "[{CorrelationId}] Tool call detected: type={Type}, id={ToolId}",
                        correlationId, type, toolId);

                    toolEvidence.Add(new ToolUsageSummary
                    {
                        ItemId = toolId,
                        Type = type,
                        Status = item["status"]?.GetValue<string>() ?? "detected",
                        Detail = Truncate(item.ToJsonString(), 500)
                    });
                    break;
                }

                // Catch-all for other output items that indicate tool usage
                default:
                {
                    if (type != null && type.Contains("fabric", StringComparison.OrdinalIgnoreCase))
                    {
                        toolEvidence.Add(new ToolUsageSummary
                        {
                            ItemId = item["id"]?.GetValue<string>() ?? "unknown",
                            Type = type,
                            Status = item["status"]?.GetValue<string>() ?? "detected",
                            Detail = Truncate(item.ToJsonString(), 500)
                        });
                    }
                    break;
                }
            }
        }

        _logger.LogInformation(
            "[{CorrelationId}] Parsed output: {OutputCount} items, {ToolCount} tool calls, answer={HasAnswer}",
            correlationId, output.Count, toolEvidence.Count, answer != null);

        return (answer, toolEvidence);
    }

    private static string? Truncate(string? value, int maxLength)
    {
        if (value == null) return null;
        return value.Length <= maxLength ? value : value[..maxLength] + "…";
    }
}
