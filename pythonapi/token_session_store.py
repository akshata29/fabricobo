# ════════════════════════════════════════════════════════════════
# Token Session Store — In-Process Fabric OBO Token Cache
#
# Used exclusively on the MCP concurrency-theory path:
#
#   Browser → API (stores Fabric token here)
#           → Foundry (MCP tool called with session_key)
#           → MCP Server (retrieves Fabric token by session_key)
#           → Fabric Data Agent (RLS with user identity)
#
# WHY: The Fabric OBO token must reach the MCP server without
# passing through the AI model context. An opaque, short-lived
# session_key travels through Foundry; the actual token stays
# in this in-process store, shared only because the MCP server
# and the API run in the same Python process.
#
# SECURITY:
#   - The session_key is a UUID4 with no intrinsic value.
#   - 5-minute TTL — outlives any single Foundry→Fabric round-trip.
#   - Tokens are automatically evicted on access if expired.
#   - Suitable for a single-process deployment (dev/staging).
#   - For multi-process deployments (uvicorn --workers N > 1),
#     replace with a shared cache such as Redis.
# ════════════════════════════════════════════════════════════════
import time
from typing import Optional

# Module-level store: {session_key: (fabric_obo_token, expiry_monotonic_time)}
# asyncio is single-threaded per worker, so no lock is needed.
_store: dict[str, tuple[str, float]] = {}

_DEFAULT_TTL_SECONDS = 300  # 5 minutes


def set_token(session_key: str, fabric_token: str) -> None:
    """Store a Fabric OBO token under session_key with a 5-minute TTL."""
    _store[session_key] = (fabric_token, time.monotonic() + _DEFAULT_TTL_SECONDS)


def get_token(session_key: str) -> Optional[str]:
    """
    Retrieve the Fabric OBO token for a given session_key.

    Returns None if the key is unknown or the TTL has elapsed.
    Expired entries are evicted on access.
    """
    entry = _store.get(session_key)
    if entry is None:
        return None

    token, expires_at = entry
    if time.monotonic() > expires_at:
        del _store[session_key]
        return None

    return token


def delete_token(session_key: str) -> None:
    """Eagerly remove a session entry. Called after the Foundry round-trip completes."""
    _store.pop(session_key, None)


def token_count() -> int:
    """Return the number of currently live session entries (useful for health checks)."""
    return len(_store)
