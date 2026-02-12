using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace FabricObo.Controllers;

/// <summary>
/// Serves non-secret SPA configuration (tenant ID, client IDs) to the frontend.
/// This endpoint is intentionally anonymous — the values are public client IDs,
/// not secrets. Loading them from appsettings.json at runtime avoids hardcoding
/// them in checked-in frontend source code.
/// </summary>
[ApiController]
[Route("api/[controller]")]
public class ConfigController : ControllerBase
{
    private readonly IConfiguration _configuration;

    public ConfigController(IConfiguration configuration)
    {
        _configuration = configuration;
    }

    /// <summary>
    /// GET /api/config
    /// Returns the SPA authentication configuration needed by MSAL.js.
    /// No authentication required — these are public values.
    /// </summary>
    [HttpGet]
    [AllowAnonymous]
    public IActionResult Get()
    {
        var spaAuth = _configuration.GetSection("SpaAuth");

        var testUsers = spaAuth.GetSection("TestUsers")
            .GetChildren()
            .Select(u => new
            {
                label = u["Label"],
                upn = u["Upn"],
                description = u["Description"]
            })
            .ToArray();

        return Ok(new
        {
            tenantId = spaAuth["TenantId"],
            spaClientId = spaAuth["SpaClientId"],
            apiClientId = spaAuth["ApiClientId"],
            testUsers
        });
    }
}
