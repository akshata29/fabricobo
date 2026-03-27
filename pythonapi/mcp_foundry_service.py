# ════════════════════════════════════════════════════════════════
# MCP Foundry Agent Service — Foundry Responses API with MCP Tool
#
# This is the Foundry-side of the concurrency-theory path.
# It is a drop-in alternative to FoundryAgentService used on
# the /api/agent/v2 endpoint ONLY — existing paths are unchanged.
#
# Differences from FoundryAgentService:
#   1. No named agent reference (FabricOboAgent) and no built-in
#      fabric_dataagent_preview tool.
#   2. Instead, an inline MCP tool is configured pointing at the
#      local /mcp endpoint served by fabric_mcp_server.py.
#   3. A session_key is prepended to the input so that the agent
#      can extract and pass it to the MCP tool — the Fabric OBO
#      token is stored under this key in token_session_store and
#      never appears in the model context.
#
# Request flow:
#   1. /api/agent/v2 handler:
#        a. OBO exchange for Foundry scope  (as before)
#        b. OBO exchange for Fabric scope   (NEW)
#        c. store Fabric token in TokenSessionStore under session_key
#        d. call McpFoundryAgentService.run_agent(question, session_key, ...)
#   2. McpFoundryAgentService.run_agent:
#        a. create Foundry conversation
#        b. POST /openai/responses with:
#             - model + inline instructions
#             - input  = "[session_key=<UUID>] <user question>"
#             - tools  = [{ type: "mcp", server_url: <api>/mcp, ... }]
#   3. Foundry agent (GPT-4o):
#        a. extracts session_key from [session_key=...] prefix
#        b. calls query_fabric(question=..., session_key=...)
#   4. MCP server (fabric_mcp_server.py):
#        a. retrieves Fabric token from TokenSessionStore
#        b. creates NEW Fabric thread (new execution slot!)
#        c. runs question against Fabric Data Agent (with OBO RLS)
#        d. returns text answer to Foundry
#   5. Foundry formats and returns final response → /api/agent/v2 handler
# ════════════════════════════════════════════════════════════════
import json
import logging
from typing import Optional

import httpx

from config import FabricDirectSettings, FoundrySettings
from models import AgentResponse, ToolUsageSummary

logger = logging.getLogger("fabricobo.mcp_foundry")

# System prompt that instructs the agent to extract the session_key and forward it
# to the query_fabric MCP tool.  The tag format [session_key=UUID] is chosen to be
# visually unambiguous and easy for the LLM to extract reliably.
_MCP_SYSTEM_INSTRUCTIONS = """\
You are a helpful data analysis assistant with access to Microsoft Fabric business data \
via the query_fabric tool.

CONTEXT EXTRACTION:
The user's message begins with a context tag in the following format:
    [session_key=<UUID>]

Extract the UUID from this tag — it is the session_key parameter for query_fabric.
The actual question is everything AFTER the closing ] bracket.

TOOL USAGE:
For any question about data, accounts, sales, portfolios, positions, reports, or \
business metrics, call query_fabric with:
  - question:    the actual user question  (without the [session_key=...] prefix)
  - session_key: the UUID you extracted above

RESPONSE:
- Present the data returned by the tool in a clear, readable format.
- Never include [session_key=...] or the raw UUID in your visible response.
- If the tool returns an error or timeout message, relay it clearly and suggest retrying.\
"""


def _truncate(text: str, max_len: int) -> str:
    return text if len(text) <= max_len else text[:max_len] + "…"


class McpFoundryAgentService:
    """
    Calls the Azure AI Foundry Responses API with an inline MCP tool for Fabric access.

    Use this on /api/agent/v2 (theory-test endpoint) alongside the existing
    FoundryAgentService on /api/agent (unchanged).
    """

    def __init__(
        self,
        foundry_settings: FoundrySettings,
        fabric_direct_settings: FabricDirectSettings,
    ):
        self._foundry = foundry_settings
        self._fabric_direct = fabric_direct_settings
        # Set to the agent name once ensure_agent() has created/verified it.
        # When set, run_agent uses an agent reference (traces appear in Foundry).
        self._agent_name: Optional[str] = None

    async def ensure_agent(self, service_token: str) -> str:
        """
        Create or update the FabricMcpAgent in Foundry with the current MCP server URL.

        Called at startup so the agent exists and appears in the Foundry portal
        with traces/logs before any requests arrive.  If the agent already exists
        (name match), its tool URL is updated to the current mcp_server_url so that
        devtunnel URL changes are picked up automatically on restart.

        Returns the agent name that can be passed as an agent_reference.
        """
        agent_name = self._fabric_direct.mcp_agent_name
        mcp_server_url = self._fabric_direct.mcp_server_url
        base_url = self._foundry.project_endpoint.rstrip("/") + "/"
        api_ver = self._foundry.api_version

        headers = {
            "Authorization": f"Bearer {service_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        agent_tools = [
            {
                "type": "mcp",
                "server_label": "fabric_direct",
                "server_url": mcp_server_url,
                "require_approval": "never",
            }
        ]

        # New Foundry Agents API body — model/instructions/tools go inside "definition"
        agent_definition = {
            "kind": "prompt",
            "model": self._foundry.model_deployment_name,
            "instructions": _MCP_SYSTEM_INSTRUCTIONS,
            "tools": agent_tools,
        }

        async with httpx.AsyncClient(
            base_url=base_url, headers=headers, timeout=httpx.Timeout(30.0)
        ) as client:
            # ── List existing agents ──────────────────────────────
            list_resp = await client.get(f"agents?api-version={api_ver}")
            list_resp.raise_for_status()
            agents = list_resp.json().get("data", [])
            existing = next((a for a in agents if a.get("name") == agent_name), None)

            if existing:
                agent_id = existing.get("id") or existing.get("name", agent_name)
                # Create a new version to pick up the updated MCP server URL
                update_resp = await client.post(
                    f"agents?api-version={api_ver}",
                    json={
                        "name": agent_name,
                        "definition": {
                            **agent_definition,
                            # keep tools in the definition but no separate top-level model
                        },
                    },
                )
                if not update_resp.is_success:
                    logger.warning(
                        "FabricMcpAgent update returned %s: %s",
                        update_resp.status_code, update_resp.text,
                    )
                else:
                    logger.info(
                        "FabricMcpAgent updated (new version): name=%s mcp_url=%s",
                        agent_name, mcp_server_url,
                    )
            else:
                create_resp = await client.post(
                    f"agents?api-version={api_ver}",
                    json={
                        "name": agent_name,
                        "definition": agent_definition,
                    },
                )
                if create_resp.status_code == 409:
                    # Agent already exists (list may have been paginated / filtered).
                    # Treat as success — just reference it by name.
                    logger.info(
                        "FabricMcpAgent already exists (409 on create) — using existing agent: %s",
                        agent_name,
                    )
                elif not create_resp.is_success:
                    error_body = create_resp.text
                    logger.error(
                        "FabricMcpAgent create returned %s: %s",
                        create_resp.status_code, error_body,
                    )
                    create_resp.raise_for_status()
                else:
                    created = create_resp.json()
                    agent_id = created.get("id") or created.get("name", agent_name)
                    logger.info(
                        "FabricMcpAgent created: id=%s name=%s mcp_url=%s",
                        agent_id, agent_name, mcp_server_url,
                    )

        self._agent_name = agent_name
        return agent_name

    async def run_agent(
        self,
        question: str,
        session_key: str,
        obo_access_token: str,
        correlation_id: str,
        conversation_id: Optional[str] = None,
    ) -> AgentResponse:
        """
        Run the agent using Foundry with MCP tool for Fabric data access.

        Args:
            question:          The user's natural-language question.
            session_key:       UUID stored in TokenSessionStore mapping to the Fabric OBO token.
            obo_access_token:  Foundry-scoped OBO token (for the Foundry Responses API auth).
            correlation_id:    Request tracing ID.
            conversation_id:   Optional existing conversation ID for multi-turn context.
        """
        base_url = self._foundry.project_endpoint.rstrip("/") + "/"
        timeout = httpx.Timeout(self._foundry.response_timeout_seconds, connect=30.0)
        mcp_server_url = self._fabric_direct.mcp_server_url

        logger.info(
            "[%s] McpFoundry run_agent: mcp_url=%s session_key=%s...",
            correlation_id,
            mcp_server_url,
            session_key[:8],
        )

        # Inject session_key as a prefixed tag — the agent extracts and forwards it to the MCP tool
        tagged_input = f"[session_key={session_key}] {question}"

        async with httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {obo_access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        ) as client:
            try:
                # ──────────────────────────────────────────────────
                # Step 1: Create conversation (multi-turn context)
                # ──────────────────────────────────────────────────
                if conversation_id is None:
                    conv_resp = await client.post(
                        f"openai/conversations?api-version={self._foundry.api_version}",
                        json={},
                    )
                    conv_resp.raise_for_status()
                    conversation_id = conv_resp.json()["id"]
                    logger.info("[%s] Conversation created: %s", correlation_id, conversation_id)

                # ──────────────────────────────────────────────────
                # Step 2: Responses API with inline MCP tool
                #
                # We pass the MCP tool inline — no named-agent reference.
                # The tool_names list restricts Foundry to invoking only
                # query_fabric on our MCP server.
                #
                # NOTE: If the Foundry API version does not yet support
                # inline "mcp" tool type, you may need to pre-register the
                # MCP server as a connection in the Foundry project and
                # reference it here.  Check Azure AI Foundry release notes.
                # ──────────────────────────────────────────────────
                if self._agent_name:
                    # Named agent reference — traces and logs appear in Foundry portal.
                    # The agent was created/updated at startup via ensure_agent().
                    payload = {
                        "input": tagged_input,
                        "conversation": conversation_id,
                        "agent": {
                            "name": self._agent_name,
                            "type": "agent_reference",
                        },
                    }
                    logger.debug(
                        "[%s] Using named agent reference: %s",
                        correlation_id, self._agent_name,
                    )
                else:
                    # Fallback: inline MCP tool (no Foundry portal traces).
                    # ensure_agent() may have failed at startup.
                    payload = {
                        "model": self._foundry.model_deployment_name,
                        "input": tagged_input,
                        "instructions": _MCP_SYSTEM_INSTRUCTIONS,
                        "conversation": conversation_id,
                        "tools": [
                            {
                                "type": "mcp",
                                "server_label": "fabric_direct",
                                "server_url": mcp_server_url,
                                "require_approval": "never",
                            }
                        ],
                    }
                    logger.debug("[%s] Falling back to inline MCP tools (no named agent)", correlation_id)

                logger.debug(
                    "[%s] Responses API payload: model=%s mcp=%s input_preview=%s",
                    correlation_id,
                    self._foundry.model_deployment_name,
                    mcp_server_url,
                    _truncate(tagged_input, 120),
                )

                resp = await client.post(
                    f"openai/responses?api-version={self._foundry.api_version}",
                    json=payload,
                )

                if not resp.is_success:
                    logger.error(
                        "[%s] Responses API returned %d: %s",
                        correlation_id,
                        resp.status_code,
                        _truncate(resp.text, 800),
                    )
                    resp.raise_for_status()

                data = resp.json()

                # ──────────────────────────────────────────────────
                # Step 3: Parse response
                # ──────────────────────────────────────────────────
                status = data.get("status", "unknown")
                response_id = data.get("id")

                logger.info(
                    "[%s] Responses API returned status=%s id=%s",
                    correlation_id,
                    status,
                    response_id,
                )

                if status != "completed":
                    error_detail = json.dumps(data.get("error"))
                    return AgentResponse(
                        status=status,
                        correlation_id=correlation_id,
                        conversation_id=conversation_id,
                        response_id=response_id,
                        error=f"Agent ended with status: {status}. {error_detail}",
                    )

                answer, tool_evidence = _parse_output(data, correlation_id)

                return AgentResponse(
                    status="completed",
                    correlation_id=correlation_id,
                    conversation_id=conversation_id,
                    response_id=response_id,
                    assistant_answer=answer,
                    tool_evidence=tool_evidence or None,
                )

            except httpx.HTTPStatusError as exc:
                logger.error(
                    "[%s] Foundry HTTP error: %d — %s",
                    correlation_id,
                    exc.response.status_code,
                    exc.response.text[:500],
                )
                return AgentResponse(
                    status="error",
                    correlation_id=correlation_id,
                    conversation_id=conversation_id,
                    error=f"Foundry API error: {exc.response.status_code} — {exc.response.text[:500]}",
                )
            except httpx.TimeoutException:
                logger.error(
                    "[%s] Foundry API timed out after %ds",
                    correlation_id,
                    self._foundry.response_timeout_seconds,
                )
                return AgentResponse(
                    status="timeout",
                    correlation_id=correlation_id,
                    conversation_id=conversation_id,
                    error=f"Foundry API timed out after {self._foundry.response_timeout_seconds}s",
                )


# ──────────────────────────────────────────────────────────────────────────────
# Response parser (mirrors FoundryAgentService._parse_output)
# ──────────────────────────────────────────────────────────────────────────────

def _parse_output(
    response_json: dict,
    correlation_id: str,
) -> tuple[Optional[str], list[ToolUsageSummary]]:
    """
    Parse the v2 Responses API output array.

    Handles both the standard tool_call type and the expected mcp_call type
    that Foundry emits when an MCP tool is invoked.
    """
    output = response_json.get("output", [])
    if not output:
        logger.warning("[%s] Empty output array in Responses API response", correlation_id)
        return None, []

    answer: Optional[str] = None
    tool_evidence: list[ToolUsageSummary] = []

    for item in output:
        item_type = item.get("type", "")

        if item_type == "message":
            role = item.get("role")
            if role == "assistant":
                texts = [
                    block.get("text", "")
                    for block in item.get("content", [])
                    if block.get("type") == "output_text" and block.get("text")
                ]
                if texts:
                    answer = "\n".join(texts)

        elif item_type in (
            "mcp_call",          # Foundry MCP tool invocation
            "tool_call",         # generic tool call
            "function_call",     # function-style tool call
            "fabric_dataagent_preview_call",  # kept for compatibility
        ):
            tool_id = item.get("id", "unknown")
            logger.info(
                "[%s] Tool call detected: type=%s id=%s",
                correlation_id,
                item_type,
                tool_id,
            )
            tool_evidence.append(
                ToolUsageSummary(
                    item_id=tool_id,
                    type=item_type,
                    status=item.get("status", "detected"),
                    detail=_truncate(json.dumps(item), 500),
                )
            )

    logger.info(
        "[%s] Parsed output: %d items, %d tool calls, answer=%s",
        correlation_id,
        len(output),
        len(tool_evidence),
        answer is not None,
    )

    return answer, tool_evidence
