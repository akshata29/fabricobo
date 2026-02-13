# ════════════════════════════════════════════════════════════════
# Entitlement Service — Stub Implementation
#
# Maps user identity (UPN) to internal authorization metadata.
# Advisory only — enforcement is by Fabric RLS.
#
# Equivalent of:
#   - IEntitlementService + StubEntitlementService in .NET
#
# For production, replace with a real database lookup.
# ════════════════════════════════════════════════════════════════
import logging
from abc import ABC, abstractmethod

from models import EntitlementResult

logger = logging.getLogger("fabricobo.entitlement")


class IEntitlementService(ABC):
    """
    Entitlement service interface — maps a user identity to internal
    authorization metadata (RepCode, role, etc.).

    IMPORTANT: This is advisory data used for logging and optional UX hints.
    Real enforcement MUST happen at the Fabric RLS layer.
    """

    @abstractmethod
    async def get_entitlement(self, upn: str, oid: str) -> EntitlementResult:
        ...


class StubEntitlementService(IEntitlementService):
    """
    Stub entitlement service that simulates a DB lookup.
    Replace with a real database call in production.

    User mappings are loaded from settings. If not configured,
    falls back to allowing all users (Fabric RLS is the real gate).
    """

    def __init__(self, users: list[dict] | None = None):
        self._user_map: dict[str, tuple[str, str]] = {}
        if users:
            for u in users:
                upn = u.get("upn", "")
                rep_code = u.get("rep_code", "")
                role = u.get("role", "Advisor")
                if upn and rep_code:
                    self._user_map[upn.lower()] = (rep_code, role)

    async def get_entitlement(self, upn: str, oid: str) -> EntitlementResult:
        key = upn.lower()
        if key in self._user_map:
            rep_code, role = self._user_map[key]
            return EntitlementResult(
                upn=upn,
                oid=oid,
                rep_code=rep_code,
                role=role,
                is_authorized=True,
            )

        # Unknown user — still authorized (Fabric RLS controls actual data access)
        return EntitlementResult(
            upn=upn,
            oid=oid,
            rep_code=None,
            role=None,
            is_authorized=True,
        )
