namespace FabricObo.Models;

/// <summary>
/// Result of the entitlement DB lookup.
/// Advisory only — enforcement is by Fabric RLS.
/// </summary>
public sealed class EntitlementResult
{
    /// <summary>User principal name from the JWT.</summary>
    public required string Upn { get; init; }

    /// <summary>Object ID (oid) from the JWT.</summary>
    public required string Oid { get; init; }

    /// <summary>Internal representative code mapped from the user.</summary>
    public string? RepCode { get; init; }

    /// <summary>Human-readable role or permission level (e.g. "Advisor", "Admin").</summary>
    public string? Role { get; init; }

    /// <summary>Whether the user is authorized (advisory check).</summary>
    public bool IsAuthorized { get; init; }
}
