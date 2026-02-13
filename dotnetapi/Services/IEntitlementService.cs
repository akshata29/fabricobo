using FabricObo.Models;

namespace FabricObo.Services;

/// <summary>
/// Entitlement service interface — maps a user identity to internal
/// authorization metadata (RepCode, role, etc.).
///
/// IMPORTANT: This is advisory data used for logging and optional UX hints.
/// Real enforcement MUST happen at the Fabric RLS layer.
/// See docs/SECURITY.md for rationale.
/// </summary>
public interface IEntitlementService
{
    /// <summary>
    /// Look up entitlement info for a given UPN and object ID.
    /// </summary>
    Task<EntitlementResult> GetEntitlementAsync(string upn, string oid);
}
