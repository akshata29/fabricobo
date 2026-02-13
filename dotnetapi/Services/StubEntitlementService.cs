using FabricObo.Models;

namespace FabricObo.Services;

/// <summary>
/// Stub entitlement service that simulates a DB lookup.
/// Replace with a real database call in production.
///
/// The mapping here mirrors the Fabric RLS RepUserMapping table
/// so that the advisory check and the enforcement boundary agree.
///
/// User mappings are loaded from appsettings.json "Entitlements" section.
/// If not configured, falls back to allowing all users (Fabric RLS is the real gate).
/// </summary>
public sealed class StubEntitlementService : IEntitlementService
{
    private readonly Dictionary<string, (string RepCode, string Role)> _userMap;

    public StubEntitlementService(IConfiguration configuration)
    {
        _userMap = new Dictionary<string, (string RepCode, string Role)>(StringComparer.OrdinalIgnoreCase);

        var entitlements = configuration.GetSection("Entitlements:Users").GetChildren();
        foreach (var entry in entitlements)
        {
            var upn = entry["Upn"];
            var repCode = entry["RepCode"];
            var role = entry["Role"] ?? "Advisor";
            if (!string.IsNullOrEmpty(upn) && !string.IsNullOrEmpty(repCode))
            {
                _userMap[upn] = (repCode, role);
            }
        }
    }

    public Task<EntitlementResult> GetEntitlementAsync(string upn, string oid)
    {
        if (_userMap.TryGetValue(upn, out var entry))
        {
            return Task.FromResult(new EntitlementResult
            {
                Upn = upn,
                Oid = oid,
                RepCode = entry.RepCode,
                Role = entry.Role,
                IsAuthorized = true
            });
        }

        // Unknown user — still authorized (Fabric RLS controls actual data access),
        // but we flag that no RepCode was found for logging purposes.
        return Task.FromResult(new EntitlementResult
        {
            Upn = upn,
            Oid = oid,
            RepCode = null,
            Role = null,
            IsAuthorized = true // Advisory; Fabric RLS is the real gate
        });
    }
}
