# ════════════════════════════════════════════════════════════════
# Foundry Agent Service — v2 Responses API
#
# Implements the Azure AI Foundry Responses API workflow:
#   POST /openai/conversations   (create conversation for multi-turn)
#   POST /openai/responses       (send question with Fabric tool, get answer)
#
# Identity passthrough (OBO) is handled by the Fabric tool at the service layer.
#
# Equivalent of:
#   - IFoundryAgentService + FoundryAgentService in .NET
#
# Docs:
#   https://learn.microsoft.com/azure/ai-foundry/quickstarts/get-started-code
#   https://learn.microsoft.com/azure/ai-foundry/agents/how-to/tools/fabric
# ════════════════════════════════════════════════════════════════
import json
import logging
from abc import ABC, abstractmethod
from typing import Optional

import httpx

from config import FoundrySettings
from models import AgentResponse, ToolUsageSummary

logger = logging.getLogger("fabricobo.foundry")


class IFoundryAgentService(ABC):
    """
    Abstraction over Azure AI Foundry v2 Responses API.
    Equivalent of IFoundryAgentService in .NET.
    """

    @abstractmethod
    async def run_agent(
        self,
        question: str,
        conversation_id: Optional[str],
        obo_access_token: str,
        correlation_id: str,
    ) -> AgentResponse:
        ...


class FoundryAgentService(IFoundryAgentService):
    """
    Implements the v2 Foundry Agents Responses API workflow.

    Key differences from the classic API:
      - Synchronous responses — no polling loop
      - Uses the Responses API instead of threads/messages/runs
      - Fabric tool specified via "fabric_dataagent_preview" tool type
      - Conversations replace threads for multi-turn context
    """

    def __init__(self, settings: FoundrySettings):
        self._settings = settings

    async def run_agent(
        self,
        question: str,
        conversation_id: Optional[str],
        obo_access_token: str,
        correlation_id: str,
    ) -> AgentResponse:
        base_url = self._settings.project_endpoint.rstrip("/") + "/"
        timeout = httpx.Timeout(self._settings.response_timeout_seconds, connect=30.0)

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
                # Step 1: Create conversation if not provided
                # ──────────────────────────────────────────────────
                if conversation_id is None:
                    conversation_id = await self._create_conversation(
                        client, correlation_id
                    )

                # ──────────────────────────────────────────────────
                # Step 2: Call the Responses API
                # ──────────────────────────────────────────────────
                response_json = await self._call_responses_api(
                    client, question, conversation_id, correlation_id
                )

                # ──────────────────────────────────────────────────
                # Step 3: Parse the response
                # ──────────────────────────────────────────────────
                response_id = response_json.get("id")
                status = response_json.get("status", "unknown")

                if status != "completed":
                    error_detail = json.dumps(response_json.get("error"))
                    logger.warning(
                        "[%s] Response %s ended with status: %s, error: %s",
                        correlation_id,
                        response_id,
                        status,
                        error_detail,
                    )
                    return AgentResponse(
                        status=status,
                        correlation_id=correlation_id,
                        conversation_id=conversation_id,
                        response_id=response_id,
                        error=f"Agent response ended with status: {status}. {error_detail}",
                    )

                # Extract answer and tool evidence from the output array
                answer, tool_evidence = self._parse_output(response_json, correlation_id)

                logger.info(
                    "[%s] Response completed. ConversationId=%s, ResponseId=%s, "
                    "ToolSteps=%d, AnswerLength=%d",
                    correlation_id,
                    conversation_id,
                    response_id,
                    len(tool_evidence),
                    len(answer) if answer else 0,
                )

                return AgentResponse(
                    status="completed",
                    correlation_id=correlation_id,
                    conversation_id=conversation_id,
                    response_id=response_id,
                    assistant_answer=answer,
                    tool_evidence=tool_evidence if tool_evidence else None,
                )

            except httpx.HTTPStatusError as exc:
                logger.error(
                    "[%s] Foundry API HTTP error: %s — %s",
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
                    self._settings.response_timeout_seconds,
                )
                return AgentResponse(
                    status="timeout",
                    correlation_id=correlation_id,
                    conversation_id=conversation_id,
                    error=f"Foundry API timed out after {self._settings.response_timeout_seconds}s",
                )

    # ================================================================
    # Private helpers
    # ================================================================

    async def _create_conversation(
        self, client: httpx.AsyncClient, correlation_id: str
    ) -> str:
        """POST /openai/conversations — create a conversation context for multi-turn."""
        logger.debug("[%s] Creating new conversation", correlation_id)

        resp = await client.post(
            f"openai/conversations?api-version={self._settings.api_version}",
            json={},
        )
        resp.raise_for_status()
        data = resp.json()
        conv_id = data["id"]

        logger.info(
            "[%s] Conversation created: %s", correlation_id, conv_id
        )
        return conv_id

    async def _call_responses_api(
        self,
        client: httpx.AsyncClient,
        question: str,
        conversation_id: str,
        correlation_id: str,
    ) -> dict:
        """
        POST /openai/responses — call the Responses API with the Fabric tool.

        Supports two modes:
          1. Inline tools: Pass model + instructions + Fabric tool config directly
          2. Named agent: Reference a pre-configured agent by name
        """
        logger.debug(
            "[%s] Calling Responses API with question: %s",
            correlation_id,
            _truncate(question, 100),
        )

        if self._settings.agent_name:
            # Named agent reference
            logger.debug(
                "[%s] Using named agent: %s",
                correlation_id,
                self._settings.agent_name,
            )
            request_body = {
                "input": question,
                "conversation": conversation_id,
                "tool_choice": "auto",
                "agent": {
                    "name": self._settings.agent_name,
                    "type": "agent_reference",
                },
            }
        else:
            # Inline tools mode
            logger.debug(
                "[%s] Using inline Fabric tool with model: %s",
                correlation_id,
                self._settings.model_deployment_name,
            )
            request_body: dict = {
                "model": self._settings.model_deployment_name,
                "input": question,
                "instructions": self._settings.instructions,
                "conversation": conversation_id,
                "tool_choice": (
                    "required"
                    if self._settings.fabric_connection_id
                    else "auto"
                ),
            }

            # Add Fabric tool if connection ID is configured
            if self._settings.fabric_connection_id:
                request_body["tools"] = [
                    {
                        "type": "fabric_dataagent_preview",
                        "fabric_dataagent_preview": {
                            "project_connections": [
                                {
                                    "project_connection_id": self._settings.fabric_connection_id
                                }
                            ]
                        },
                    }
                ]
                logger.debug(
                    "[%s] Fabric tool attached with connection: %s",
                    correlation_id,
                    self._settings.fabric_connection_id,
                )

        resp = await client.post(
            f"openai/responses?api-version={self._settings.api_version}",
            json=request_body,
        )

        response_body = resp.text
        if not resp.is_success:
            logger.error(
                "[%s] Responses API returned %d: %s",
                correlation_id,
                resp.status_code,
                _truncate(response_body, 1000),
            )
            resp.raise_for_status()

        data = resp.json()
        logger.info(
            "[%s] Responses API returned status=%s, id=%s",
            correlation_id,
            data.get("status"),
            data.get("id"),
        )
        return data

    def _parse_output(
        self, response_json: dict, correlation_id: str
    ) -> tuple[Optional[str], list[ToolUsageSummary]]:
        """
        Parse the v2 response output array to extract the assistant answer
        and any tool usage evidence.
        """
        output = response_json.get("output", [])
        if not output:
            return None, []

        answer: Optional[str] = None
        tool_evidence: list[ToolUsageSummary] = []

        for item in output:
            item_type = item.get("type")

            if item_type == "message":
                role = item.get("role")
                if role == "assistant":
                    content_array = item.get("content", [])
                    texts = []
                    for block in content_array:
                        if block.get("type") == "output_text":
                            text = block.get("text")
                            if text:
                                texts.append(text)
                    if texts:
                        answer = "\n".join(texts)

            elif item_type in (
                "fabric_dataagent_preview_call",
                "tool_call",
                "function_call",
            ):
                tool_id = item.get("id", "unknown")
                logger.info(
                    "[%s] Tool call detected: type=%s, id=%s",
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

            elif item_type and "fabric" in item_type.lower():
                tool_evidence.append(
                    ToolUsageSummary(
                        item_id=item.get("id", "unknown"),
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


def _truncate(value: Optional[str], max_length: int) -> Optional[str]:
    if value is None:
        return None
    return value[:max_length] + "…" if len(value) > max_length else value
