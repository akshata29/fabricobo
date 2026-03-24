import { useState, useRef, useCallback } from "react";
import { useMsal } from "@azure/msal-react";
import { apiScope, API_BASE_URL } from "./authConfig";

// ════════════════════════════════════════════════════════════════
// Types
// ════════════════════════════════════════════════════════════════

interface LoadTestConfig {
  inputTokens: number;
  outputTokens: number;
  concurrentUsers: number;
  totalRequests: number;
  delayBetweenRequestsMs: number;
  delayBetweenWavesMs: number;
}

type RequestStatus = "completed" | "error" | "timeout" | "pending";

interface ToolEvidenceDetail {
  itemId: string;
  type: string;
  status: string;
  detail?: string;
}

interface RequestResult {
  requestIndex: number;
  wave: number;
  slotInWave: number;
  startTimeIso: string;
  endTimeIso: string;
  latencyMs: number;
  status: RequestStatus;
  httpStatus: number;
  correlationId: string;
  conversationId: string;
  responseId: string;
  // Prompt & completion — full text stored in JSON, truncated in UI
  question: string;
  prompt: string;
  promptChars: number;
  completion: string;
  // Token estimates
  answerLength: number;
  toolSteps: number;
  estimatedInputTokens: number;
  estimatedOutputTokens: number;
  // Tool evidence
  toolEvidenceDetail: ToolEvidenceDetail[];
  error: string;
}

interface LoadTestSession {
  testId: string;
  fabricSku: string;
  cuHoursPerRequest: number;
  maxRequestsPerDay: number;
  config: LoadTestConfig;
  startTimeIso: string;
  endTimeIso: string;
  totalDurationMs: number;
  results: RequestResult[];
  summary: {
    totalRequests: number;
    successCount: number;
    errorCount: number;
    successRate: number;
    minLatencyMs: number;
    maxLatencyMs: number;
    avgLatencyMs: number;
    p50LatencyMs: number;
    p95LatencyMs: number;
    p99LatencyMs: number;
    requestsPerSecond: number;
    estimatedCuHoursUsed: number;
    estimatedDailyBudgetConsumedPct: number;
  };
}

// ════════════════════════════════════════════════════════════════
// F64 Capacity constants
// ════════════════════════════════════════════════════════════════

const F64_CUS = 64;
const F64_CU_HOURS_PER_DAY = 1536; // 64 CUs × 24 hours
const CU_HOURS_PER_REQUEST = 0.11; // based on 2000 input + 500 output tokens
const F64_MAX_REQUESTS_PER_DAY = Math.floor(F64_CU_HOURS_PER_DAY / CU_HOURS_PER_REQUEST); // ~13,964

// ════════════════════════════════════════════════════════════════
// Questions — same set as the Chat sidebar quick questions
// ════════════════════════════════════════════════════════════════

const QUICK_QUESTIONS = [
  "Give me a list of all accounts",
  "What is the total balance across all accounts?",
  "Which region has the most accounts?",
  "Show me account details for the East region",
];

// ════════════════════════════════════════════════════════════════
// Stats helpers
// ════════════════════════════════════════════════════════════════

function percentile(sortedArr: number[], p: number): number {
  if (sortedArr.length === 0) return 0;
  const idx = Math.ceil((p / 100) * sortedArr.length) - 1;
  return sortedArr[Math.max(0, idx)];
}

function computeSummary(
  results: RequestResult[],
  totalDurationMs: number,
  totalRequests: number
): LoadTestSession["summary"] {
  const done = results.filter((r) => r.status !== "pending");
  const successes = done.filter((r) => r.status === "completed");
  const latencies = successes.map((r) => r.latencyMs).sort((a, b) => a - b);

  const successCount = successes.length;
  const errorCount = done.length - successCount;
  const successRate = done.length > 0 ? (successCount / done.length) * 100 : 0;
  const minLatencyMs = latencies.length > 0 ? latencies[0] : 0;
  const maxLatencyMs = latencies.length > 0 ? latencies[latencies.length - 1] : 0;
  const avgLatencyMs =
    latencies.length > 0 ? latencies.reduce((a, b) => a + b, 0) / latencies.length : 0;
  const p50 = percentile(latencies, 50);
  const p95 = percentile(latencies, 95);
  const p99 = percentile(latencies, 99);
  const rps = totalDurationMs > 0 ? (done.length / totalDurationMs) * 1000 : 0;
  const cuHoursUsed = done.length * CU_HOURS_PER_REQUEST;
  const dailyBudgetConsumedPct = (cuHoursUsed / F64_CU_HOURS_PER_DAY) * 100;

  return {
    totalRequests: done.length,
    successCount,
    errorCount,
    successRate: Math.round(successRate * 10) / 10,
    minLatencyMs: Math.round(minLatencyMs),
    maxLatencyMs: Math.round(maxLatencyMs),
    avgLatencyMs: Math.round(avgLatencyMs),
    p50LatencyMs: Math.round(p50),
    p95LatencyMs: Math.round(p95),
    p99LatencyMs: Math.round(p99),
    requestsPerSecond: Math.round(rps * 100) / 100,
    estimatedCuHoursUsed: Math.round(cuHoursUsed * 1000) / 1000,
    estimatedDailyBudgetConsumedPct: Math.round(dailyBudgetConsumedPct * 1000) / 1000,
  };
}

// ════════════════════════════════════════════════════════════════
// Download helper
// ════════════════════════════════════════════════════════════════

function downloadJson(data: LoadTestSession): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `load-test-${data.testId}-${data.startTimeIso.slice(0, 19).replace(/:/g, "-")}.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ════════════════════════════════════════════════════════════════
// LoadTest Component
// ════════════════════════════════════════════════════════════════

export default function LoadTest() {
  const { instance } = useMsal();

  const [config, setConfig] = useState<LoadTestConfig>({
    inputTokens: 2000,
    outputTokens: 500,
    concurrentUsers: 5,
    totalRequests: 20,
    delayBetweenRequestsMs: 0,
    delayBetweenWavesMs: 0,
  });

  const [isRunning, setIsRunning] = useState(false);
  const [results, setResults] = useState<RequestResult[]>([]);
  const [session, setSession] = useState<LoadTestSession | null>(null);
  const [progress, setProgress] = useState({ completed: 0, total: 0 });
  const [liveLog, setLiveLog] = useState<string[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const logEndRef = useRef<HTMLDivElement>(null);

  const addLog = useCallback((msg: string) => {
    const ts = new Date().toLocaleTimeString();
    setLiveLog((prev) => [...prev.slice(-199), `[${ts}] ${msg}`]);
    setTimeout(() => logEndRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
  }, []);

  const getAccessToken = async (): Promise<string> => {
    const account = instance.getActiveAccount();
    if (!account) throw new Error("No active MSAL account");
    const result = await instance.acquireTokenSilent({
      scopes: [apiScope],
      account,
    });
    return result.accessToken;
  };

  const runSingleRequest = async (
    requestIndex: number,
    wave: number,
    slotInWave: number,
    token: string,
    signal: AbortSignal
  ): Promise<RequestResult> => {
    const question = QUICK_QUESTIONS[requestIndex % QUICK_QUESTIONS.length];
    const estimatedInputTokens = Math.round(question.length / 4);

    const startTime = new Date();
    const startTimeIso = startTime.toISOString();

    const result: RequestResult = {
      requestIndex,
      wave,
      slotInWave,
      startTimeIso,
      endTimeIso: "",
      latencyMs: 0,
      status: "pending",
      httpStatus: 0,
      correlationId: "",
      conversationId: "",
      responseId: "",
      question,
      prompt: question,
      promptChars: question.length,
      completion: "",
      answerLength: 0,
      toolSteps: 0,
      estimatedInputTokens,
      estimatedOutputTokens: 0,
      toolEvidenceDetail: [],
      error: "",
    };

    try {
      const response = await fetch(`${API_BASE_URL}/agent`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ question }),
        signal,
      });

      const endTime = new Date();
      result.endTimeIso = endTime.toISOString();
      result.latencyMs = endTime.getTime() - startTime.getTime();
      result.httpStatus = response.status;

      if (!response.ok) {
        result.status = "error";
        result.error = `HTTP ${response.status}`;
        return result;
      }

      const data = await response.json();
      result.correlationId = data.correlationId ?? "";
      result.conversationId = data.conversationId ?? "";
      result.responseId = data.responseId ?? "";
      result.completion = data.assistantAnswer ?? "";
      result.answerLength = result.completion.length;
      result.toolSteps = data.toolEvidence?.length ?? 0;
      result.estimatedOutputTokens = Math.round(result.answerLength / 4);
      result.toolEvidenceDetail = (data.toolEvidence ?? []).map((t: any) => ({
        itemId: t.itemId ?? "",
        type: t.type ?? "",
        status: t.status ?? "",
        detail: t.detail ?? undefined,
      }));

      if (data.status === "completed") {
        result.status = "completed";
      } else {
        result.status = "error";
        result.error = data.error ?? `Agent status: ${data.status}`;
      }
    } catch (err: any) {
      const endTime = new Date();
      result.endTimeIso = endTime.toISOString();
      result.latencyMs = endTime.getTime() - startTime.getTime();
      if (err.name === "AbortError") {
        result.status = "timeout";
        result.error = "Aborted by user";
      } else {
        result.status = "error";
        result.error = err.message ?? String(err);
      }
    }

    return result;
  };

  const startTest = async () => {
    if (isRunning) return;

    setIsRunning(true);
    setResults([]);
    setSession(null);
    setLiveLog([]);
    setProgress({ completed: 0, total: config.totalRequests });

    const abort = new AbortController();
    abortRef.current = abort;

    const testId = Math.random().toString(36).slice(2, 10).toUpperCase();
    const testStart = new Date();

    addLog(`Test ${testId} started — ${config.totalRequests} requests, ${config.concurrentUsers} concurrent, ~${config.inputTokens} input tokens`);
    addLog(`F64 capacity: ${F64_MAX_REQUESTS_PER_DAY.toLocaleString()} req/day @ 0.11 CU-hours each`);

    let token: string;
    try {
      token = await getAccessToken();
      addLog("MSAL token acquired successfully");
    } catch (err: any) {
      addLog(`ERROR: Failed to acquire token — ${err.message}`);
      setIsRunning(false);
      return;
    }

    const allResults: RequestResult[] = [];
    let globalIndex = 0;
    let wave = 0;

    while (globalIndex < config.totalRequests && !abort.signal.aborted) {
      const batchSize = Math.min(
        config.concurrentUsers,
        config.totalRequests - globalIndex
      );
      const waveIndices = Array.from({ length: batchSize }, (_, i) => ({
        requestIndex: globalIndex + i,
        slotInWave: i,
      }));

      const delayNote = config.delayBetweenRequestsMs > 0
        ? `, ${config.delayBetweenRequestsMs}ms stagger between requests`
        : "";
      addLog(`Wave ${wave + 1}: firing ${batchSize} request(s) (req #${globalIndex + 1}–${globalIndex + batchSize}${delayNote})`);

      const waveStart = Date.now();
      const wavePromises = waveIndices.map(({ requestIndex, slotInWave }) => {
        const staggerMs = slotInWave * config.delayBetweenRequestsMs;
        if (staggerMs === 0) return runSingleRequest(requestIndex, wave, slotInWave, token, abort.signal);
        return new Promise<RequestResult>((resolve) => {
          const t = setTimeout(() => resolve(runSingleRequest(requestIndex, wave, slotInWave, token, abort.signal)), staggerMs);
          abort.signal.addEventListener("abort", () => clearTimeout(t), { once: true });
        }).then((r) => r as RequestResult);
      });

      const waveResults = await Promise.allSettled(wavePromises);
      const waveDurationMs = Date.now() - waveStart;

      waveResults.forEach((settled) => {
        const r =
          settled.status === "fulfilled"
            ? settled.value
            : ({
                ...waveIndices[0],
                wave,
                startTimeIso: new Date().toISOString(),
                endTimeIso: new Date().toISOString(),
                latencyMs: 0,
                status: "error" as const,
                httpStatus: 0,
                correlationId: "",
                conversationId: "",
                responseId: "",
                question: QUICK_QUESTIONS[waveIndices[0].requestIndex % QUICK_QUESTIONS.length],
                prompt: "",
                promptChars: 0,
                completion: "",
                answerLength: 0,
                toolSteps: 0,
                estimatedInputTokens: config.inputTokens,
                estimatedOutputTokens: 0,
                toolEvidenceDetail: [],
                error: settled.reason?.message ?? "Unknown error",
              } as unknown as RequestResult);
        allResults.push(r);
        setResults((prev) => [...prev, r]);
        setProgress((prev) => ({ ...prev, completed: prev.completed + 1 }));

        const icon = r.status === "completed" ? "✓" : "✗";
        const latStr = r.latencyMs > 0 ? `${r.latencyMs.toLocaleString()}ms` : "—";
        addLog(
          `  ${icon} Req #${r.requestIndex + 1}: ${r.status.toUpperCase()} | ${latStr}` +
            ` | Q: "${r.question.slice(0, 60)}"` +
            (r.error ? ` | ERR: ${r.error}` : "") +
            (r.completion
              ? ` | Reply: "${r.completion.slice(0, 120).replace(/\n/g, " ")}${r.completion.length > 120 ? "…" : ""}"`
              : "") +
            (r.answerLength > 0 ? ` | ~${r.estimatedOutputTokens} out-tokens` : "") +
            (r.toolSteps > 0 ? ` | ${r.toolSteps} tool step(s)` : "")
        );
      });

      addLog(`  Wave ${wave + 1} done in ${waveDurationMs.toLocaleString()}ms`);

      globalIndex += batchSize;
      wave++;

      if (config.delayBetweenWavesMs > 0 && globalIndex < config.totalRequests && !abort.signal.aborted) {
        addLog(`  Waiting ${config.delayBetweenWavesMs.toLocaleString()}ms before next wave...`);
        await new Promise<void>((resolve) => {
          const t = setTimeout(resolve, config.delayBetweenWavesMs);
          abort.signal.addEventListener("abort", () => { clearTimeout(t); resolve(); }, { once: true });
        });
      }
    }

    // Refresh token between waves if test is long
    const testEnd = new Date();
    const totalDurationMs = testEnd.getTime() - testStart.getTime();
    const summary = computeSummary(allResults, totalDurationMs, config.totalRequests);

    const sessionData: LoadTestSession = {
      testId,
      fabricSku: "F64",
      cuHoursPerRequest: CU_HOURS_PER_REQUEST,
      maxRequestsPerDay: F64_MAX_REQUESTS_PER_DAY,
      config,
      startTimeIso: testStart.toISOString(),
      endTimeIso: testEnd.toISOString(),
      totalDurationMs,
      results: allResults,
      summary,
    };

    setSession(sessionData);

    addLog("─".repeat(60));
    addLog(`Test complete. Duration: ${(totalDurationMs / 1000).toFixed(1)}s`);
    addLog(`Success: ${summary.successCount}/${summary.totalRequests} (${summary.successRate}%)`);
    addLog(`Latency — avg: ${summary.avgLatencyMs}ms | p50: ${summary.p50LatencyMs}ms | p95: ${summary.p95LatencyMs}ms | p99: ${summary.p99LatencyMs}ms`);
    addLog(`Est. CU-hours used: ${summary.estimatedCuHoursUsed} (${summary.estimatedDailyBudgetConsumedPct}% of F64 daily budget)`);

    setIsRunning(false);
  };

  const stopTest = () => {
    abortRef.current?.abort();
    addLog("Stop requested — aborting in-flight requests...");
  };

  const reset = () => {
    setResults([]);
    setSession(null);
    setLiveLog([]);
    setProgress({ completed: 0, total: 0 });
  };

  const completedCount = results.filter((r) => r.status !== "pending").length;
  const successCount = results.filter((r) => r.status === "completed").length;
  const errorCount = results.filter((r) => r.status === "error" || r.status === "timeout").length;
  const avgLatency = (() => {
    const lats = results.filter((r) => r.status === "completed").map((r) => r.latencyMs);
    return lats.length > 0 ? Math.round(lats.reduce((a, b) => a + b, 0) / lats.length) : 0;
  })();

  // Capacity estimation
  const estimatedCuHoursPerTest = config.totalRequests * CU_HOURS_PER_REQUEST;
  const estimatedDailyRunsAtConfig = Math.floor(F64_CU_HOURS_PER_DAY / estimatedCuHoursPerTest);

  return (
    <div className="loadtest-container">
      {/* ── Config Panel ── */}
      <div className="loadtest-config-panel">
        <div className="loadtest-panel-header">
          <h2>Load Test Configuration</h2>
          <span className="sku-badge">F64 SKU</span>
        </div>

        {/* SKU Capacity Info */}
        <div className="sku-capacity-grid">
          <div className="sku-stat">
            <span className="sku-stat-value">{F64_CUS}</span>
            <span className="sku-stat-label">CUs</span>
          </div>
          <div className="sku-stat">
            <span className="sku-stat-value">{F64_CU_HOURS_PER_DAY.toLocaleString()}</span>
            <span className="sku-stat-label">CU-hours/day</span>
          </div>
          <div className="sku-stat">
            <span className="sku-stat-value">{CU_HOURS_PER_REQUEST}</span>
            <span className="sku-stat-label">CU-hrs/request</span>
          </div>
          <div className="sku-stat highlight">
            <span className="sku-stat-value">{F64_MAX_REQUESTS_PER_DAY.toLocaleString()}</span>
            <span className="sku-stat-label">Max req/day</span>
          </div>
        </div>

        {/* Config inputs */}
        <div className="loadtest-form">
          <div className="form-row">
            <div className="form-group">
              <label>Input Tokens (capacity estimate basis)</label>
              <input
                type="number"
                className="form-input"
                value={config.inputTokens}
                min={100}
                max={8000}
                step={100}
                disabled={isRunning}
                onChange={(e) =>
                  setConfig((c) => ({ ...c, inputTokens: parseInt(e.target.value) || 100 }))
                }
              />
              <span className="form-hint">Used for CU-hour math only — actual prompt is the question</span>
            </div>
            <div className="form-group">
              <label>Output Tokens (reference)</label>
              <input
                type="number"
                className="form-input"
                value={config.outputTokens}
                min={50}
                max={4000}
                step={50}
                disabled={isRunning}
                onChange={(e) =>
                  setConfig((c) => ({ ...c, outputTokens: parseInt(e.target.value) || 50 }))
                }
              />
              <span className="form-hint">Capacity est. basis</span>
            </div>
          </div>

          <div className="form-row">
            <div className="form-group">
              <label>Concurrent Users (wave size)</label>
              <input
                type="number"
                className="form-input"
                value={config.concurrentUsers}
                min={1}
                max={50}
                step={1}
                disabled={isRunning}
                onChange={(e) =>
                  setConfig((c) => ({ ...c, concurrentUsers: parseInt(e.target.value) || 1 }))
                }
              />
              <span className="form-hint">Parallel requests per wave</span>
            </div>
            <div className="form-group">
              <label>Total Requests</label>
              <input
                type="number"
                className="form-input"
                value={config.totalRequests}
                min={1}
                max={500}
                step={1}
                disabled={isRunning}
                onChange={(e) =>
                  setConfig((c) => ({ ...c, totalRequests: parseInt(e.target.value) || 1 }))
                }
              />
              <span className="form-hint">
                Est. {estimatedCuHoursPerTest.toFixed(2)} CU-hrs ·{" "}
                {estimatedDailyRunsAtConfig.toLocaleString()} runs/day possible
              </span>
            </div>
          </div>

          <div className="form-row">
            <div className="form-group">
              <label>Delay Between Requests (seconds)</label>
              <input
                type="number"
                className="form-input"
                value={config.delayBetweenRequestsMs / 1000}
                min={0}
                max={60}
                step={0.5}
                disabled={isRunning}
                onChange={(e) =>
                  setConfig((c) => ({ ...c, delayBetweenRequestsMs: (parseFloat(e.target.value) || 0) * 1000 }))
                }
              />
              <span className="form-hint">Stagger between each request within a wave. 0 = all fire simultaneously.</span>
            </div>
            <div className="form-group">
              <label>Delay Between Waves (seconds)</label>
              <input
                type="number"
                className="form-input"
                value={config.delayBetweenWavesMs / 1000}
                min={0}
                max={300}
                step={1}
                disabled={isRunning}
                onChange={(e) =>
                  setConfig((c) => ({ ...c, delayBetweenWavesMs: (parseFloat(e.target.value) || 0) * 1000 }))
                }
              />
              <span className="form-hint">Wait after all requests in a wave complete before starting the next.</span>
            </div>
          </div>

          <div className="form-group">
            <label>Questions (rotating)</label>
            <div className="question-rotation-list">
              {QUICK_QUESTIONS.map((q, i) => (
                <span key={i} className="question-rotation-item">Q{i + 1}: {q}</span>
              ))}
            </div>
            <span className="form-hint">Cycles through these in order, matching the Chat sidebar quick questions</span>
          </div>
        </div>

        {/* Actions */}
        <div className="loadtest-actions">
          {!isRunning ? (
            <>
              <button
                className="btn btn-primary btn-run"
                onClick={startTest}
                disabled={isRunning}
              >
                ▶ Run Load Test
              </button>
              {results.length > 0 && (
                <button className="btn btn-outline" onClick={reset}>
                  Reset
                </button>
              )}
            </>
          ) : (
            <button className="btn btn-stop" onClick={stopTest}>
              ■ Stop Test
            </button>
          )}
          {session && (
            <button
              className="btn btn-download"
              onClick={() => downloadJson(session)}
            >
              ↓ Download JSON
            </button>
          )}
        </div>

        {/* Progress bar */}
        {(isRunning || completedCount > 0) && (
          <div className="loadtest-progress">
            <div className="progress-header">
              <span>
                {completedCount} / {config.totalRequests} requests
              </span>
              <span>
                <span className="badge badge-success">{successCount} ok</span>
                {errorCount > 0 && (
                  <span className="badge badge-error">{errorCount} err</span>
                )}
                {avgLatency > 0 && (
                  <span className="badge badge-neutral">
                    avg {avgLatency.toLocaleString()}ms
                  </span>
                )}
              </span>
            </div>
            <div className="progress-bar-track">
              <div
                className={`progress-bar-fill ${errorCount > 0 ? "has-errors" : ""}`}
                style={{
                  width: `${(completedCount / config.totalRequests) * 100}%`,
                }}
              />
            </div>
          </div>
        )}
      </div>

      {/* ── Results Area ── */}
      <div className="loadtest-results-area">
        {/* Summary stats (shown after completion) */}
        {session && (
          <div className="loadtest-summary">
            <h3>Results Summary</h3>
            <div className="summary-grid">
              <div className="summary-stat">
                <span className="summary-val">{session.summary.successRate}%</span>
                <span className="summary-lbl">Success Rate</span>
              </div>
              <div className="summary-stat">
                <span className="summary-val">{session.summary.avgLatencyMs.toLocaleString()}ms</span>
                <span className="summary-lbl">Avg Latency</span>
              </div>
              <div className="summary-stat">
                <span className="summary-val">{session.summary.p50LatencyMs.toLocaleString()}ms</span>
                <span className="summary-lbl">p50</span>
              </div>
              <div className="summary-stat">
                <span className="summary-val">{session.summary.p95LatencyMs.toLocaleString()}ms</span>
                <span className="summary-lbl">p95</span>
              </div>
              <div className="summary-stat">
                <span className="summary-val">{session.summary.p99LatencyMs.toLocaleString()}ms</span>
                <span className="summary-lbl">p99</span>
              </div>
              <div className="summary-stat">
                <span className="summary-val">{session.summary.minLatencyMs.toLocaleString()}ms</span>
                <span className="summary-lbl">Min</span>
              </div>
              <div className="summary-stat">
                <span className="summary-val">{session.summary.maxLatencyMs.toLocaleString()}ms</span>
                <span className="summary-lbl">Max</span>
              </div>
              <div className="summary-stat">
                <span className="summary-val">{session.summary.requestsPerSecond}</span>
                <span className="summary-lbl">req/sec</span>
              </div>
              <div className="summary-stat">
                <span className="summary-val">{session.summary.estimatedCuHoursUsed}</span>
                <span className="summary-lbl">CU-hrs used</span>
              </div>
              <div className="summary-stat">
                <span className="summary-val">{session.summary.estimatedDailyBudgetConsumedPct}%</span>
                <span className="summary-lbl">of daily budget</span>
              </div>
              <div className="summary-stat">
                <span className="summary-val">{(session.totalDurationMs / 1000).toFixed(1)}s</span>
                <span className="summary-lbl">Total Duration</span>
              </div>
              <div className="summary-stat">
                <span className="summary-val">{session.testId}</span>
                <span className="summary-lbl">Test ID</span>
              </div>
            </div>
          </div>
        )}

        <div className="loadtest-panels">
          {/* Request table */}
          {results.length > 0 && (
            <div className="loadtest-table-panel">
              <h3>Per-Request Results ({results.length})</h3>
              <div className="table-scroll">
                <table className="results-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Wave</th>
                      <th>Status</th>
                      <th>HTTP</th>
                      <th>Latency</th>
                      <th>In Tokens</th>
                      <th>Out Tokens</th>
                      <th>Tool Steps</th>
                      <th>Question</th>
                      <th>Completion Preview</th>
                      <th>Correlation ID</th>
                      <th>Error</th>
                    </tr>
                  </thead>
                  <tbody>
                    {results.map((r) => (
                      <tr
                        key={r.requestIndex}
                        className={
                          r.status === "completed"
                            ? "row-ok"
                            : r.status === "pending"
                            ? "row-pending"
                            : "row-err"
                        }
                      >
                        <td>{r.requestIndex + 1}</td>
                        <td>{r.wave + 1}</td>
                        <td>
                          <span className={`status-pill status-${r.status}`}>
                            {r.status}
                          </span>
                        </td>
                        <td>{r.httpStatus || "—"}</td>
                        <td>{r.latencyMs > 0 ? `${r.latencyMs.toLocaleString()}ms` : "—"}</td>
                        <td>~{r.estimatedInputTokens.toLocaleString()}</td>
                        <td>
                          {r.estimatedOutputTokens > 0
                            ? `~${r.estimatedOutputTokens.toLocaleString()}`
                            : "—"}
                        </td>
                        <td>{r.toolSteps > 0 ? r.toolSteps : "—"}</td>
                        <td className="cell-question" title={r.question}>
                          {r.question.slice(0, 50)}{r.question.length > 50 ? "…" : ""}
                        </td>
                        <td className="cell-completion" title={r.completion}>
                          {r.completion
                            ? r.completion.slice(0, 100).replace(/\n/g, " ") +
                              (r.completion.length > 100 ? "…" : "")
                            : "—"}
                        </td>
                        <td>
                          <code>{r.correlationId ? r.correlationId.slice(0, 8) : "—"}</code>
                        </td>
                        <td className="error-cell">{r.error || ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Live log */}
          <div className="loadtest-log-panel">
            <h3>Live Log {isRunning && <span className="blink">●</span>}</h3>
            <div className="log-output">
              {liveLog.length === 0 ? (
                <span className="log-placeholder">
                  Log output will appear here when the test runs...
                </span>
              ) : (
                liveLog.map((line, i) => (
                  <div key={i} className="log-line">
                    {line}
                  </div>
                ))
              )}
              <div ref={logEndRef} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
