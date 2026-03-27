# ════════════════════════════════════════════════════════════════
# Fabric MCP Server — Direct Fabric Data Agent Access via MCP
#
# This is the core of the concurrency-theory alternative path:
#
#   Browser → API → Foundry → [THIS MCP SERVER] → Fabric Assistants API
#                                                   (new thread per request)
#                                                → Fabric Data Agent
#                                                → RLS-filtered results
#
# Key difference vs. the built-in Foundry Fabric tool:
#   - We bypass Foundry's conversation→Fabric thread lifecycle
#   - A fresh uuid-named thread is created for EVERY request
#   - If Fabric serialises at the thread level (not globally per-OID),
#     multiple simultaneous requests from the same user CAN run in parallel
#
# Theory source:
#   https://github.com/microsoft/fabric_data_agent_client
#   "New thread = new execution slot = concurrency works" (README, thread management section)
#
# Authentication:
#   The API performs a Fabric-scoped OBO exchange BEFORE calling Foundry and
#   stores the resulting token in token_session_store under a random session_key UUID.
#   The session_key (not the token) travels through Foundry's AI context.
#   This MCP tool retrieves the token from the store — the token itself never
#   appears in the model's prompt.
#
# Mounting:
#   This module exposes `build_mcp_asgi_app(settings)` which returns a
#   Starlette ASGI app that FastAPI mounts at /mcp:
#
#       app.mount("/mcp", build_mcp_asgi_app(fabric_direct_settings))
#
#   Foundry's server_url for the MCP tool: https://<public-api-url>/mcp
# ════════════════════════════════════════════════════════════════
import asyncio
import logging
import time
import uuid
from typing import Optional

import httpx
from openai import AsyncOpenAI

from config import FabricDirectSettings
from token_session_store import get_token

logger = logging.getLogger("fabricobo.mcp")


# ──────────────────────────────────────────────────────────────────────────────
# Fabric thread creation (adapted from fabric_data_agent_client._get_existing_or_create_new_thread)
# ──────────────────────────────────────────────────────────────────────────────

async def _create_fabric_thread(
    fabric_token: str,
    data_agent_url: str,
    thread_name: str,
) -> dict:
    """
    Create a new named thread for the Fabric Data Agent.

    We always generate a fresh uuid4 thread_name — this is what enables
    concurrent same-user requests: each gets its own execution slot.

    The URL manipulation mirrors the fabric_data_agent_client so it works with
    both the current "aiskills" URL format and any legacy formats.
    """
    # Derive the Fabric REST base URL from the OpenAI-compatible endpoint
    if "aiskills" in data_agent_url:
        base_url = (
            data_agent_url
            .replace("aiskills", "dataagents")
            .removesuffix("/openai")
            .replace("/aiassistant", "/__private/aiassistant")
        )
    else:
        base_url = (
            data_agent_url
            .removesuffix("/openai")
            .replace("/aiassistant", "/__private/aiassistant")
        )

    thread_url = f'{base_url}/threads/fabric?tag="{thread_name}"'

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            thread_url,
            headers={
                "Authorization": f"Bearer {fabric_token}",
                "Accept": "application/json",
                "ActivityId": str(uuid.uuid4()),
            },
        )
        resp.raise_for_status()
        thread = resp.json()
        thread["name"] = thread_name
        return thread


# ──────────────────────────────────────────────────────────────────────────────
# Core query handler
# ──────────────────────────────────────────────────────────────────────────────

async def _handle_query_fabric(
    question: str,
    session_key: str,
    settings: FabricDirectSettings,
) -> str:
    """
    Execute one question against the Fabric Data Agent using direct Assistants API access.

    Steps:
      1. Retrieve the Fabric OBO token from the in-process session store.
      2. Create an OpenAI client pointed at the Fabric endpoint.
      3. Generate a NEW unique thread name (the concurrency unlock).
      4. Create/retrieve the thread via the Fabric REST API.
      5. Post the question + start a run.
      6. Poll until the run completes (or times out).
      7. Return the assistant's text response.
      8. Delete the thread (cleanup — each call is stateless/independent).
    """
    # 1. Retrieve Fabric OBO token
    fabric_token = get_token(session_key)
    if not fabric_token:
        logger.warning("Session key not found or expired: %s...", session_key[:8])
        return (
            "Authentication session expired or invalid. "
            "Each request must supply a fresh session_key. Please retry."
        )

    logger.info("MCP query_fabric: question_len=%d session_key=%s...", len(question), session_key[:8])

    # 2. OpenAI client pointed at the Fabric Data Agent endpoint
    #    The Fabric Data Agent exposes an OpenAI Assistants v1 API.
    #    Auth is via Bearer token (not the api_key field).
    openai_client = AsyncOpenAI(
        api_key="unused",
        base_url=settings.data_agent_url,
        default_query={"api-version": "2024-05-01-preview"},
        default_headers={
            "Authorization": f"Bearer {fabric_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "ActivityId": str(uuid.uuid4()),
        },
    )

    # 3. Unique thread name per request — NEW THREAD = NEW EXECUTION SLOT
    #    This is the key mechanism being tested: by not reusing any conversation/thread
    #    from a previous call, we avoid the "run already active on this thread" constraint.
    thread_name = f"mcp-direct-{uuid.uuid4()}"
    thread_id: Optional[str] = None

    try:
        # 4. Create the Fabric thread via REST
        thread_info = await _create_fabric_thread(fabric_token, settings.data_agent_url, thread_name)
        thread_id = thread_info["id"]
        logger.debug("Fabric thread ready: name=%s id=%s", thread_name, thread_id)

        # 5a. Create a minimal assistant (Fabric ignores the model — required for API compatibility)
        assistant = await openai_client.beta.assistants.create(model="fabric-data-agent")

        # 5b. Post the user question to the thread
        await openai_client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=question,
        )

        # 5c. Start the run
        run = await openai_client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=assistant.id,
        )
        logger.debug("Run started: run_id=%s status=%s", run.id, run.status)

        # 6. Poll for completion with configurable timeout
        start_ts = time.monotonic()
        while run.status in ("queued", "in_progress"):
            elapsed = time.monotonic() - start_ts
            if elapsed > settings.query_timeout_seconds:
                logger.warning(
                    "Fabric query timed out after %.0fs. thread=%s",
                    elapsed,
                    thread_name,
                )
                return f"Fabric query timed out after {settings.query_timeout_seconds}s. Please retry."
            await asyncio.sleep(2)
            run = await openai_client.beta.threads.runs.retrieve(
                thread_id=thread_id,
                run_id=run.id,
            )

        if run.status != "completed":
            logger.warning("Run ended with status=%s thread=%s", run.status, thread_name)
            return f"Fabric run ended with status '{run.status}'. Please retry."

        # 7. Extract the assistant's text response
        messages = await openai_client.beta.threads.messages.list(
            thread_id=thread_id, order="asc"
        )
        responses = []
        for msg in messages.data:
            if msg.role == "assistant":
                for block in msg.content:
                    if hasattr(block, "text") and hasattr(block.text, "value"):
                        responses.append(block.text.value)

        result = "\n".join(responses) if responses else "No response received from Fabric."
        logger.info(
            "MCP query_fabric completed: run_id=%s response_len=%d",
            run.id,
            len(result),
        )
        return result

    except httpx.HTTPStatusError as exc:
        logger.error(
            "Fabric HTTP error: %d %s",
            exc.response.status_code,
            exc.response.text[:200],
        )
        return f"Fabric API error (HTTP {exc.response.status_code}). Please retry."
    except Exception as exc:
        logger.error("Unexpected error in query_fabric MCP tool: %s", exc, exc_info=True)
        return f"Error querying Fabric Data Agent: {exc}"
    finally:
        # 8. Best-effort thread cleanup (stateless: each call creates its own thread)
        if thread_id:
            try:
                await openai_client.beta.threads.delete(thread_id=thread_id)
                logger.debug("Deleted Fabric thread: %s", thread_id)
            except Exception:
                pass  # Non-fatal — threads age out on Fabric's side


# ──────────────────────────────────────────────────────────────────────────────
# MCP Streamable HTTP server (no fastmcp dependency)
#
# Implements the MCP Streamable HTTP transport (protocol version 2025-03-26)
# directly on top of Starlette — which is already in the FastAPI dependency tree.
#
# Handles exactly the four JSON-RPC 2.0 methods Foundry will send:
#   initialize      → server capabilities handshake
#   ping            → keepalive
#   tools/list      → returns the query_fabric tool schema
#   tools/call      → executes query_fabric
#
# Notifications (requests without an "id") are acknowledged with HTTP 202.
# ──────────────────────────────────────────────────────────────────────────────

_TOOL_SCHEMA = {
    "name": "query_fabric",
    "description": (
        "Query the Microsoft Fabric Data Agent directly. "
        "Returns business data filtered by the authenticated user's "
        "Row-Level Security policy. "
        "Each invocation creates a new Fabric execution thread, "
        "enabling concurrent same-user queries."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "Natural language question about business data.",
            },
            "session_key": {
                "type": "string",
                "description": (
                    "Short-lived opaque identifier (UUID) provided by the API "
                    "request context. Do not ask the user for this value."
                ),
            },
        },
        "required": ["question", "session_key"],
    },
}


def build_mcp_asgi_app(settings: FabricDirectSettings):
    """
    Build and return a Starlette ASGI app that speaks MCP Streamable HTTP.

    Mount in main.py:
        app.mount("/mcp", build_mcp_asgi_app(fabric_direct_settings))

    Foundry MCP tool server_url: https://<public-api-url>/mcp
    """
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Route

    async def mcp_handler(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32700, "message": "Parse error"}},
                status_code=400,
            )

        method = body.get("method", "")
        req_id = body.get("id")  # None means it's a notification

        # Notifications have no id — just acknowledge
        if req_id is None:
            return Response(status_code=202)

        if method == "initialize":
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fabric-direct-agent", "version": "1.0"},
                },
            })

        if method == "ping":
            return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {}})

        if method == "tools/list":
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": [_TOOL_SCHEMA]},
            })

        if method == "tools/call":
            params = body.get("params", {})
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            if tool_name != "query_fabric":
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
                })
            result_text = await _handle_query_fabric(
                question=arguments.get("question", ""),
                session_key=arguments.get("session_key", ""),
                settings=settings,
            )
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": result_text}]},
            })

        return JSONResponse({
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        })

    class _NormalizePath:
        """Strip the extra round-trip: sub-app sees path='' for /mcp (no slash).
        Map it to '/' so the Route matches without issuing a 307 redirect."""
        def __init__(self, app):
            self._app = app
        async def __call__(self, scope, receive, send):
            if scope.get("type") == "http" and scope.get("path", "/") == "":
                scope = {**scope, "path": "/", "raw_path": b"/"}
            await self._app(scope, receive, send)

    return _NormalizePath(Starlette(
        routes=[Route("/", mcp_handler, methods=["POST"])],
    ))
