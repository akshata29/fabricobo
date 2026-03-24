"""
FabricObo Load Test — Locust Script
====================================
Tests the POST /api/agent endpoint under load.

Usage (via VS Code task):
  locust -f locustfile.py -u 10 -r 2 --run-time 1m

Or headless with custom params:
  locust -f locustfile.py \
    --headless \
    -u 10 -r 2 \
    --run-time 2m \
    --host http://localhost:5180

Environment variables:
  LOCUST_HOST          — API base URL (default: http://localhost:5180)
  LOCUST_INPUT_TOKENS  — Target input token count per request (default: 2000)
  LOCUST_OUTPUT_TOKENS — Target output token count reference (default: 500)
  LOCUST_BEARER_TOKEN  — Static bearer token for auth (required for /api/agent)
                         Obtain via browser devtools: copy the Bearer token from
                         any /api/agent request in the Network tab while logged in.

F64 SKU Capacity Reference (2000 in + 500 out tokens @ 0.11 CU-hrs/request):
  CUs          : 64
  CU-hours/day : 1,536
  Max req/day  : ~13,964
"""

import json
import logging
import os
import random
import time
from datetime import datetime, timezone

from locust import HttpUser, between, events, task

logger = logging.getLogger("fabricobo.locust")

# ════════════════════════════════════════════════════════════════
# Configuration from environment
# ════════════════════════════════════════════════════════════════

INPUT_TOKENS = int(os.getenv("LOCUST_INPUT_TOKENS", "2000"))
OUTPUT_TOKENS = int(os.getenv("LOCUST_OUTPUT_TOKENS", "500"))
BEARER_TOKEN = os.getenv("LOCUST_BEARER_TOKEN", "")

# F64 capacity constants
F64_CU_HOURS_PER_DAY = 1536
CU_HOURS_PER_REQUEST = 0.11
F64_MAX_REQUESTS_PER_DAY = int(F64_CU_HOURS_PER_DAY / CU_HOURS_PER_REQUEST)

# ════════════════════════════════════════════════════════════════
# Prompt generation (mirrors LoadTest.tsx logic)
# ════════════════════════════════════════════════════════════════

_BASE_QUESTIONS = [
    "Show me a summary of all accounts with their current balances",
    "What is the total portfolio value grouped by region",
    "List all accounts sorted by balance in descending order",
    "Show me accounts in the East region with balance above average",
    "What are the top performing accounts this quarter",
    "Give me a breakdown of account distribution by type",
    "Which accounts have changed most significantly this period",
    "Show all accounts with their representative codes and roles",
    "What is the average balance per region",
    "List accounts that are flagged for review",
]

_FILLER_CONTEXT = (
    "\n\nContext for this request: This is a load test simulation designed to evaluate "
    "the throughput and latency characteristics of the Fabric Data Agent running on an "
    "F64 SKU capacity. The Fabric Data Agent leverages Row-Level Security (RLS) to ensure "
    "users only see data they are authorized to access. This test validates that the agent "
    "correctly handles concurrent requests without degradation. The Fabric platform does not "
    "impose concurrency limits at the agent level; the only constraint is the total CU-hours "
    "consumed per day (1,536 CU-hours for F64). Each request consumes approximately "
    "0.11 CU-hours based on a workload profile of 2,000 input tokens and 500 output tokens. "
    "Data used in this test includes synthetic account information across multiple regions. "
    "Additional padding follows to simulate the target input token count: "
)

_PAD_UNIT = (
    "The account balance represents the total assets under management for this client "
    "portfolio including equities, fixed income, and alternative investments. "
)


def build_prompt(request_index: int, target_tokens: int) -> str:
    """Generate a prompt padded to approximately target_tokens (4 chars/token)."""
    question = _BASE_QUESTIONS[request_index % len(_BASE_QUESTIONS)]
    header = f"[Request #{request_index + 1}] {question}\n\n"
    context = _FILLER_CONTEXT
    target_chars = target_tokens * 4
    current_len = len(header) + len(context)

    if current_len >= target_chars:
        return header + context[: target_chars - len(header)]

    pad_needed = target_chars - current_len
    pad = (_PAD_UNIT * (pad_needed // len(_PAD_UNIT) + 1))[:pad_needed]
    return header + context + pad


# ════════════════════════════════════════════════════════════════
# Custom results collector
# ════════════════════════════════════════════════════════════════

_test_results: list[dict] = []
_test_start_time: str = ""


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    global _test_start_time, _test_results
    _test_start_time = datetime.now(timezone.utc).isoformat()
    _test_results = []

    if not BEARER_TOKEN:
        logger.warning(
            "LOCUST_BEARER_TOKEN is not set. "
            "Requests to /api/agent will return 401. "
            "Set it via: set LOCUST_BEARER_TOKEN=<token>"
        )

    logger.info(
        "Load test starting — input_tokens=%d, output_tokens=%d (ref), "
        "F64 max req/day=%d",
        INPUT_TOKENS,
        OUTPUT_TOKENS,
        F64_MAX_REQUESTS_PER_DAY,
    )


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    end_time = datetime.now(timezone.utc).isoformat()

    if not _test_results:
        logger.info("No results to save.")
        return

    # Compute summary stats
    successes = [r for r in _test_results if r["status"] == "completed"]
    errors = [r for r in _test_results if r["status"] != "completed"]
    latencies = sorted(r["latency_ms"] for r in successes)

    def pct(pv: int) -> float:
        if not latencies:
            return 0.0
        idx = max(0, int(len(latencies) * pv / 100) - 1)
        return round(latencies[idx], 1)

    total = len(_test_results)
    avg = round(sum(latencies) / len(latencies), 1) if latencies else 0
    cu_used = round(total * CU_HOURS_PER_REQUEST, 3)

    summary = {
        "total_requests": total,
        "success_count": len(successes),
        "error_count": len(errors),
        "success_rate_pct": round(len(successes) / total * 100, 1) if total else 0,
        "min_latency_ms": round(latencies[0], 1) if latencies else 0,
        "max_latency_ms": round(latencies[-1], 1) if latencies else 0,
        "avg_latency_ms": avg,
        "p50_latency_ms": pct(50),
        "p95_latency_ms": pct(95),
        "p99_latency_ms": pct(99),
        "estimated_cu_hours_used": cu_used,
        "estimated_daily_budget_consumed_pct": round(
            cu_used / F64_CU_HOURS_PER_DAY * 100, 4
        ),
    }

    output = {
        "test_id": _test_start_time[:19].replace(":", "-"),
        "fabric_sku": "F64",
        "cu_hours_per_request": CU_HOURS_PER_REQUEST,
        "max_requests_per_day": F64_MAX_REQUESTS_PER_DAY,
        "config": {
            "input_tokens": INPUT_TOKENS,
            "output_tokens_reference": OUTPUT_TOKENS,
        },
        "start_time_iso": _test_start_time,
        "end_time_iso": end_time,
        "summary": summary,
        "results": _test_results,
    }

    # Save to timestamped JSON file
    fname = f"load-test-results-{_test_start_time[:19].replace(':', '-').replace('T', '_')}.json"
    fpath = os.path.join(os.path.dirname(__file__), fname)
    try:
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2)
        logger.info("Results saved to: %s", fpath)
        print(f"\n{'=' * 60}")
        print(f"Results saved → {fpath}")
        print(f"  Requests   : {total} total, {len(successes)} ok, {len(errors)} err")
        print(f"  Latency    : avg={avg}ms  p50={pct(50)}ms  p95={pct(95)}ms  p99={pct(99)}ms")
        print(f"  CU-hrs used: {cu_used} ({summary['estimated_daily_budget_consumed_pct']}% of F64 daily)")
        print(f"{'=' * 60}\n")
    except OSError as exc:
        logger.error("Failed to save results: %s", exc)


@events.request.add_listener
def on_request(
    request_type,
    name,
    response_time,
    response_length,
    response,
    context,
    exception,
    **kwargs,
):
    if name != "/api/agent":
        return

    result = {
        "timestamp_iso": datetime.now(timezone.utc).isoformat(),
        "latency_ms": round(response_time, 1),
        "status": "error",
        "http_status": 0,
        "estimated_input_tokens": INPUT_TOKENS,
        "estimated_output_tokens": 0,
        "answer_length": 0,
        "tool_steps": 0,
        "correlation_id": "",
        "conversation_id": "",
        "error": "",
    }

    if exception:
        result["error"] = str(exception)
    elif response is not None:
        result["http_status"] = response.status_code
        if response.status_code == 200:
            try:
                data = response.json()
                result["correlation_id"] = data.get("correlationId", "")
                result["conversation_id"] = data.get("conversationId", "")
                answer = data.get("assistantAnswer", "")
                result["answer_length"] = len(answer)
                result["estimated_output_tokens"] = len(answer) // 4
                result["tool_steps"] = len(data.get("toolEvidence") or [])
                result["status"] = data.get("status", "unknown")
            except Exception as parse_exc:
                result["error"] = f"JSON parse error: {parse_exc}"
        else:
            result["error"] = f"HTTP {response.status_code}"

    _test_results.append(result)


# ════════════════════════════════════════════════════════════════
# Locust User
# ════════════════════════════════════════════════════════════════


class FabricAgentUser(HttpUser):
    """
    Simulates a single concurrent user sending questions to the Fabric OBO agent.

    Wait time is set to mimic realistic usage (0–1 second between requests
    per user). Adjust as needed for burst vs. sustained load patterns.
    """

    wait_time = between(0, 1)
    _request_counter = 0

    def on_start(self):
        self._request_counter = random.randint(0, 999)

    @task
    def ask_agent(self):
        self._request_counter += 1
        prompt = build_prompt(self._request_counter, INPUT_TOKENS)

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if BEARER_TOKEN:
            headers["Authorization"] = f"Bearer {BEARER_TOKEN}"

        with self.client.post(
            "/api/agent",
            json={"question": prompt},
            headers=headers,
            name="/api/agent",
            catch_response=True,
        ) as response:
            if response.status_code == 401:
                response.failure(
                    "401 Unauthorized — set LOCUST_BEARER_TOKEN env var"
                )
                return
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
                return

            try:
                data = response.json()
                if data.get("status") != "completed":
                    response.failure(
                        f"Agent returned status: {data.get('status')} — {data.get('error', '')}"
                    )
                else:
                    response.success()
            except Exception as exc:
                response.failure(f"Response parse error: {exc}")
