import { useState, useRef, useCallback } from "react";
import { useMsal } from "@azure/msal-react";
import { apiScope, API_BASE_URL, getAuthConfig } from "./authConfig";

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
  // Which Entra identity was used (UPN) — determines Fabric RLS view
  userUpn: string;
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
// Log types
// ════════════════════════════════════════════════════════════════

type LogLineType = "info" | "success" | "error" | "warning" | "wave" | "summary" | "fired";

interface LogEntry {
  ts: string;
  msg: string;
  type: LogLineType;
}

interface HistorySummary {
  testId: string;
  startTimeIso: string;
  endTimeIso: string;
  totalDurationMs: number;
  fabricSku: string;
  summary: LoadTestSession["summary"];
  config: LoadTestConfig;
  filename: string;
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
  const [liveLog, setLiveLog] = useState<LogEntry[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const logEndRef = useRef<HTMLDivElement>(null);

  // ── Multi-user identity pool ──
  // Each entry holds a pre-acquired token for a distinct test user.
  // During a wave, slot i uses tokenPool[i % tokenPool.length] so every
  // concurrent slot presents a different Fabric identity, allowing full
  // parallelism (no same-user thread contention in Fabric).
  const [multiUserMode, setMultiUserMode] = useState(false);
  const [tokenPool, setTokenPool] = useState<{ upn: string; label: string; token: string }[]>([]);
  const [tokenAcqStatus, setTokenAcqStatus] = useState<Record<string, "idle" | "acquiring" | "ready" | "error">>({});
  const [isAcquiringTokens, setIsAcquiringTokens] = useState(false);

  // Request detail drawer
  const [selectedRequest, setSelectedRequest] = useState<RequestResult | null>(null);

  // Test history panel
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyList, setHistoryList] = useState<HistorySummary[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");

  const addLog = useCallback((msg: string, type: LogLineType = "info") => {
    const ts = new Date().toLocaleTimeString();
    setLiveLog((prev) => [...prev.slice(-499), { ts, msg, type }]);
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

  // ── Persist a completed test to the server (data/ folder) ──
  const saveTestToServer = async (sessionData: LoadTestSession): Promise<void> => {
    setSaveStatus("saving");
    try {
      const token = await getAccessToken();
      const resp = await fetch(`${API_BASE_URL}/tests`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(sessionData),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setSaveStatus("saved");
      addLog(`Test ${sessionData.testId} saved to server (pythonapi/data/).`, "success");
      setTimeout(() => setSaveStatus("idle"), 4000);
    } catch (err: any) {
      setSaveStatus("error");
      addLog(`Failed to auto-save test: ${err.message ?? String(err)}`, "warning");
      setTimeout(() => setSaveStatus("idle"), 6000);
    }
  };

  // ── Load list of saved tests from server ──
  const loadHistory = async (): Promise<void> => {
    setHistoryLoading(true);
    try {
      const token = await getAccessToken();
      const resp = await fetch(`${API_BASE_URL}/tests?type=chat`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data: HistorySummary[] = await resp.json();
      setHistoryList(data);
    } catch (err: any) {
      addLog(`Could not load test history: ${err.message ?? String(err)}`, "warning");
    } finally {
      setHistoryLoading(false);
    }
  };

  // ── Load a full historical session into the results view ──
  const loadHistorySession = async (testId: string): Promise<void> => {
    try {
      const token = await getAccessToken();
      const resp = await fetch(`${API_BASE_URL}/tests/${encodeURIComponent(testId)}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data: LoadTestSession = await resp.json();
      setResults(data.results);
      setSession(data);
      setProgress({ completed: data.results.length, total: data.results.length });
      setHistoryOpen(false);
      addLog(`Loaded historical test ${testId} (${data.results.length} requests).`, "success");
    } catch (err: any) {
      addLog(`Failed to load test ${testId}: ${err.message ?? String(err)}`, "error");
    }
  };

  // ── Download a historical test JSON from server ──
  const downloadHistoryJson = async (testId: string): Promise<void> => {
    try {
      const token = await getAccessToken();
      const resp = await fetch(`${API_BASE_URL}/tests/${encodeURIComponent(testId)}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data: LoadTestSession = await resp.json();
      downloadJson(data);
    } catch (err: any) {
      addLog(`Failed to download test ${testId}: ${err.message ?? String(err)}`, "error");
    }
  };

  // Acquire tokens for all configured test users.
  // Tries acquireTokenSilent first (instant if the account is already cached
  // from a previous login), falls back to acquireTokenPopup per user.
  // Must be triggered by a user-gesture click to allow the first popup.
  const acquireAllTestTokens = async () => {
    const testUsers = getAuthConfig().testUsers;
    if (!testUsers.length) {
      addLog("No test users configured in SPA_TEST_USERS_JSON — cannot build token pool.", "warning");
      return;
    }

    setIsAcquiringTokens(true);
    const initial: Record<string, "idle" | "acquiring" | "ready" | "error"> = {};
    testUsers.forEach((u) => { initial[u.upn] = "idle"; });
    setTokenAcqStatus(initial);

    const pool: { upn: string; label: string; token: string }[] = [];

    for (const user of testUsers) {
      setTokenAcqStatus((prev) => ({ ...prev, [user.upn]: "acquiring" }));
      addLog(`Acquiring token for ${user.label} (${user.upn})…`);

      try {
        const accounts = instance.getAllAccounts();
        const cached = accounts.find(
          (a) => a.username.toLowerCase() === user.upn.toLowerCase()
        );

        let accessToken: string;

        if (cached) {
          try {
            const res = await instance.acquireTokenSilent({ scopes: [apiScope], account: cached });
            accessToken = res.accessToken;
            addLog(`${user.label}: token acquired silently (cached)`, "success");
          } catch {
            addLog(`${user.label}: silent failed — opening popup…`);
            const res = await instance.acquireTokenPopup({ scopes: [apiScope], account: cached });
            accessToken = res.accessToken;
            addLog(`${user.label}: token acquired via popup`, "success");
          }
        } else {
          addLog(`${user.label}: no cached account — opening login popup…`);
          const res = await instance.loginPopup({
            scopes: [apiScope, "openid", "profile"],
            loginHint: user.upn,
            prompt: "login",
          });
          accessToken = res.accessToken;
          addLog(`${user.label}: token acquired via login popup`, "success");
        }

        pool.push({ upn: user.upn, label: user.label, token: accessToken });
        setTokenAcqStatus((prev) => ({ ...prev, [user.upn]: "ready" }));
      } catch (err: any) {
        addLog(`${user.label}: token acquisition failed — ${err.message ?? String(err)}`, "error");
        setTokenAcqStatus((prev) => ({ ...prev, [user.upn]: "error" }));
      }
    }

    setTokenPool(pool);
    setIsAcquiringTokens(false);
    addLog(`Token pool ready: ${pool.length}/${testUsers.length} identities acquired.`, pool.length === testUsers.length ? "success" : "warning");
  };

  const runSingleRequest = async (
    requestIndex: number,
    wave: number,
    slotInWave: number,
    token: string,
    userUpn: string,
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
      userUpn,
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

    addLog(`Test ${testId} — ${config.totalRequests} req | ${config.concurrentUsers} concurrent | ~${config.inputTokens} input tokens`, "wave");
    addLog(`F64: ${F64_CU_HOURS_PER_DAY.toLocaleString()} CU-hrs/day → ${F64_MAX_REQUESTS_PER_DAY.toLocaleString()} max req/day @ ${CU_HOURS_PER_REQUEST} CU-hrs/req`);

    let token: string;
    const currentUserUpn = instance.getActiveAccount()?.username ?? "";
    try {
      token = await getAccessToken();
      addLog(`MSAL token acquired for ${currentUserUpn || "current user"}`, "success");
    } catch (err: any) {
      addLog(`Failed to acquire MSAL token: ${err.message}`, "error");
      setIsRunning(false);
      return;
    }

    // Snapshot the token pool at test start so mid-test re-acquisition doesn't affect in-flight waves
    const activeTokenPool = [...tokenPool];
    if (multiUserMode && activeTokenPool.length === 0) {
      addLog("Multi-user mode active but no tokens acquired — falling back to single-user.", "warning");
    } else if (multiUserMode) {
      addLog(`Multi-user: ${activeTokenPool.length} identit${activeTokenPool.length !== 1 ? "ies" : "y"} rotating across ${config.concurrentUsers} slot(s)`);
      activeTokenPool.forEach((t, i) => addLog(`  slot ${i} → ${t.upn}`));
    }

    // Returns the token and UPN for a given wave slot
    const resolveSlotIdentity = (slotInWave: number) => {
      if (multiUserMode && activeTokenPool.length > 0) {
        const entry = activeTokenPool[slotInWave % activeTokenPool.length];
        return { slotToken: entry.token, slotUpn: entry.upn };
      }
      return { slotToken: token, slotUpn: currentUserUpn };
    };

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
        ? `  +${config.delayBetweenRequestsMs}ms stagger`
        : "";
      const identityNote = multiUserMode && activeTokenPool.length > 0
        ? `  ${activeTokenPool.length} identities rotating`
        : "";
      const totalWaves = Math.ceil(config.totalRequests / config.concurrentUsers);
      addLog(`─── Wave ${wave + 1}/${totalWaves}  ${batchSize} req  #${globalIndex + 1}–${globalIndex + batchSize}${delayNote}${identityNote}`, "wave");

      const waveStart = Date.now();
      const wavePromises = waveIndices.map(({ requestIndex, slotInWave }) => {
        const { slotToken, slotUpn } = resolveSlotIdentity(slotInWave);
        const staggerMs = slotInWave * config.delayBetweenRequestsMs;
        const q = QUICK_QUESTIONS[requestIndex % QUICK_QUESTIONS.length];
        const uid = slotUpn ? slotUpn.split("@")[0] : "user";
        addLog(`→ #${requestIndex + 1} dispatched  ${uid}  "${q}"${staggerMs > 0 ? `  (after ${staggerMs}ms)` : ""}`, "fired");
        if (staggerMs === 0) return runSingleRequest(requestIndex, wave, slotInWave, slotToken, slotUpn, abort.signal);
        return new Promise<RequestResult>((resolve) => {
          const t = setTimeout(() => resolve(runSingleRequest(requestIndex, wave, slotInWave, slotToken, slotUpn, abort.signal)), staggerMs);
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
                userUpn: "",
              } as unknown as RequestResult);
        allResults.push(r);
        setResults((prev) => [...prev, r]);
        setProgress((prev) => ({ ...prev, completed: prev.completed + 1 }));

        const logType: LogLineType = r.status === "completed" ? "success" : "error";
        const icon = r.status === "completed" ? "✓" : r.status === "timeout" ? "⏱" : "✗";
        const latStr = r.latencyMs > 0 ? `${r.latencyMs.toLocaleString()}ms` : "—";
        const uid = r.userUpn ? r.userUpn.split("@")[0] : "";
        const convStr = r.conversationId ? `  conv:${r.conversationId.slice(-14)}` : "";
        const corrStr = r.correlationId ? `  corr:${r.correlationId}` : "";
        const respStr = r.responseId ? `  resp:${r.responseId.slice(-16)}` : "";
        const stepsStr = r.toolSteps > 0 ? `  ${r.toolSteps} tool step${r.toolSteps !== 1 ? "s" : ""}` : "";
        const tokStr = r.estimatedOutputTokens > 0 ? `  ~${r.estimatedOutputTokens} tok` : "";
        addLog(
          `${icon} #${r.requestIndex + 1}  ${latStr}  ${uid}  http:${r.httpStatus || "—"}${corrStr}${convStr}${respStr}${stepsStr}${tokStr}`,
          logType
        );
        // Second line: answer preview or full error
        if (r.error) {
          addLog(`   └─ ${r.error}`, "error");
        } else if (r.completion) {
          const preview = r.completion.slice(0, 200).replace(/\n/g, " ");
          addLog(`   └─ "${preview}${r.completion.length > 200 ? "…" : ""}"`, "info");
        }
      });

      const waveOk  = waveResults.filter((s) => s.status === "fulfilled" && (s as PromiseFulfilledResult<RequestResult>).value.status === "completed").length;
      const waveErr = batchSize - waveOk;
      addLog(
        `─── Wave ${wave + 1} done  ${waveDurationMs.toLocaleString()}ms  ${waveOk} ok${waveErr > 0 ? `  ${waveErr} err` : ""}`,
        waveErr > 0 ? "warning" : "wave"
      );

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

    addLog("━".repeat(56), "summary");
    addLog(`TEST ${testId} COMPLETE  ${(totalDurationMs / 1000).toFixed(1)}s`, "summary");
    addLog(`Requests  ${summary.successCount}/${summary.totalRequests} succeeded  (${summary.successRate}% success rate)`, "summary");
    addLog(`Latency   avg ${summary.avgLatencyMs}ms  p50 ${summary.p50LatencyMs}ms  p95 ${summary.p95LatencyMs}ms  p99 ${summary.p99LatencyMs}ms  min ${summary.minLatencyMs}ms  max ${summary.maxLatencyMs}ms`, "summary");
    addLog(`Throughput  ${summary.requestsPerSecond} req/s  |  CU-hrs: ${summary.estimatedCuHoursUsed} used  (${summary.estimatedDailyBudgetConsumedPct}% of F64 daily budget)`, "summary");

    setIsRunning(false);
    saveTestToServer(sessionData);
  };

  const stopTest = () => {
    abortRef.current?.abort();
    addLog("Stop requested — aborting in-flight requests…", "warning");
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
      {/* ── LEFT COLUMN: config + history ── */}
      <div className="loadtest-left-col">
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

        {/* Identity Mode */}
        <div className="identity-mode-section">
          <div className="identity-mode-header">
            <label>Identity Mode</label>
            <div className="identity-mode-toggle">
              <button
                className={`mode-btn ${!multiUserMode ? "mode-btn-active" : ""}`}
                disabled={isRunning}
                onClick={() => setMultiUserMode(false)}
              >
                Single User
              </button>
              <button
                className={`mode-btn ${multiUserMode ? "mode-btn-active" : ""}`}
                disabled={isRunning}
                onClick={() => setMultiUserMode(true)}
              >
                Multi-User
              </button>
            </div>
          </div>
          <span className="form-hint">
            {multiUserMode
              ? "Each concurrent slot uses a distinct test user token — prevents Fabric same-user thread contention for true parallel load testing."
              : "All requests use your current session token (single Fabric identity — concurrent requests for the same user may serialize in Fabric)."}
          </span>

          {multiUserMode && (
            <div className="token-pool-panel">
              {getAuthConfig().testUsers.length === 0 ? (
                <div className="pool-warning">
                  No test users configured. Add <code>SPA_TEST_USERS_JSON</code> to the backend .env to enable multi-user testing.
                </div>
              ) : (
                <>
                  <div className="pool-user-list">
                    {getAuthConfig().testUsers.map((u) => {
                      const st = tokenAcqStatus[u.upn] ?? "idle";
                      return (
                        <div key={u.upn} className={`pool-user pool-user-${st}`}>
                          <span className={`pool-dot pool-dot-${st}`} />
                          <span className="pool-user-label">{u.label}</span>
                          <span className="pool-user-upn">{u.upn}</span>
                          <span className="pool-user-st">
                            {st === "ready" ? "✓ ready" : st === "error" ? "✗ failed" : st === "acquiring" ? "⏳…" : "idle"}
                          </span>
                        </div>
                      );
                    })}
                  </div>

                  {tokenPool.length > 0 && tokenPool.length < config.concurrentUsers && (
                    <div className="pool-warning">
                      ⚠ {tokenPool.length} token{tokenPool.length !== 1 ? "s" : ""} for {config.concurrentUsers} concurrent slot{config.concurrentUsers !== 1 ? "s" : ""} — tokens will wrap (slot {tokenPool.length} reuses {tokenPool[0].upn.split("@")[0]}).
                    </div>
                  )}

                  <button
                    className="btn btn-acquire"
                    disabled={isRunning || isAcquiringTokens}
                    onClick={acquireAllTestTokens}
                  >
                    {isAcquiringTokens
                      ? "⏳ Acquiring…"
                      : tokenPool.length > 0
                      ? `↻ Refresh Tokens (${tokenPool.length}/${getAuthConfig().testUsers.length} ready)`
                      : "🔑 Acquire Test User Tokens"}
                  </button>

                  {tokenPool.length > 0 && (
                    <span className="form-hint pool-ready-note">
                      ✓ {tokenPool.length} identit{tokenPool.length !== 1 ? "ies" : "y"} ready —
                      slot 0 → {tokenPool[0]?.upn.split("@")[0]}{tokenPool.length > 1 ? `, slot 1 → ${tokenPool[1]?.upn.split("@")[0]}` : ""}{tokenPool.length > 2 ? ", …" : ""}
                    </span>
                  )}
                </>
              )}
            </div>
          )}
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
          <button
            className="btn btn-history"
            onClick={() => { setHistoryOpen((o) => !o); if (!historyOpen) loadHistory(); }}
          >
            {historyOpen ? "✕ Close History" : "📋 Test History"}
          </button>
          {saveStatus === "saving" && <span className="save-status save-saving">⏳ Saving…</span>}
          {saveStatus === "saved"  && <span className="save-status save-saved">✓ Saved</span>}
          {saveStatus === "error"  && <span className="save-status save-error">⚠ Save failed</span>}
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

      </div>

      {/* ── RIGHT COLUMN: log + results ── */}
      <div className="loadtest-right-col">
        {/* Live log — always at top */}
        <div className="loadtest-log-panel">
          <div className="log-header-row">
            <h3>Live Log {isRunning && <span className="blink">●</span>}</h3>
            <div className="log-header-actions">
              {liveLog.length > 0 && <span className="log-count">{liveLog.length} lines</span>}
              {!isRunning && liveLog.length > 0 && (
                <button className="btn-hist-icon" onClick={() => setLiveLog([])}>Clear</button>
              )}
            </div>
          </div>
          <div className="log-output">
            {liveLog.length === 0 ? (
              <span className="log-placeholder">
                Log output will appear here when the test runs...
              </span>
            ) : (
              liveLog.map((entry, i) => (
                <div key={i} className={`log-line log-line-${entry.type}`}>
                  <span className="log-ts">{entry.ts}</span>
                  <span className="log-msg">{entry.msg}</span>
                </div>
              ))
            )}
            <div ref={logEndRef} />
          </div>
        </div>

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
                      <th>User</th>
                      <th>Status</th>
                      <th>Latency</th>
                      <th>Conversation ID</th>
                      <th>Correlation ID</th>
                      <th>Response ID</th>
                      <th>Question</th>
                      <th>Completion Preview</th>
                      <th>Tool Steps</th>
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
                        style={{ cursor: "pointer" }}
                        onClick={() => setSelectedRequest(r)}
                        title="Click for full details"
                      >
                        <td>{r.requestIndex + 1}</td>
                        <td>{r.wave + 1}</td>
                        <td title={r.userUpn}>{r.userUpn ? r.userUpn.split("@")[0] : "—"}</td>
                        <td>
                          <span className={`status-pill status-${r.status}`}>
                            {r.status}
                          </span>
                        </td>
                        <td>{r.latencyMs > 0 ? `${r.latencyMs.toLocaleString()}ms` : "—"}</td>
                        <td>
                          {r.conversationId
                            ? <code title={r.conversationId}>{r.conversationId.slice(0, 18)}…</code>
                            : <span className="cell-dim">—</span>}
                        </td>
                        <td>
                          <code title={r.correlationId}>{r.correlationId ? r.correlationId.slice(0, 8) : "—"}</code>
                        </td>
                        <td>
                          {r.responseId
                            ? <code title={r.responseId}>{r.responseId.slice(0, 14)}…</code>
                            : <span className="cell-dim">—</span>}
                        </td>
                        <td className="cell-question" title={r.question}>
                          {r.question.slice(0, 45)}{r.question.length > 45 ? "…" : ""}
                        </td>
                        <td className="cell-completion" title={r.completion}>
                          {r.completion
                            ? r.completion.slice(0, 80).replace(/\n/g, " ") +
                              (r.completion.length > 80 ? "…" : "")
                            : "—"}
                        </td>
                        <td>{r.toolSteps > 0 ? r.toolSteps : "—"}</td>
                        <td className="error-cell" title={r.error}>{r.error ? r.error.slice(0, 60) + (r.error.length > 60 ? "…" : "") : ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* ── Test History Panel ── */}
        {historyOpen && (
          <div className="history-panel">
            <div className="history-panel-header">
              <h3>Test History</h3>
              <div className="history-header-actions">
                <button
                  className="btn-hist-icon"
                  onClick={loadHistory}
                  disabled={historyLoading}
                  title="Refresh history"
                >
                  {historyLoading ? "⏳" : "↻"} Refresh
                </button>
                <button className="btn-hist-icon" onClick={() => setHistoryOpen(false)}>&#x2715;</button>
              </div>
            </div>
            {historyLoading ? (
              <div className="history-empty">Loading…</div>
            ) : historyList.length === 0 ? (
              <div className="history-empty">No saved tests yet. Complete a test run to auto-save.</div>
            ) : (
              <div className="table-scroll">
                <table className="results-table history-table">
                  <thead>
                    <tr>
                      <th>Test ID</th>
                      <th>Date</th>
                      <th>Duration</th>
                      <th>Requests</th>
                      <th>Success</th>
                      <th>Avg Latency</th>
                      <th>p95</th>
                      <th>Concurrent</th>
                      <th>Identity Mode</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {historyList.map((h) => (
                      <tr key={h.testId} className="row-ok">
                        <td><code>{h.testId}</code></td>
                        <td>{new Date(h.startTimeIso).toLocaleString()}</td>
                        <td>{h.totalDurationMs > 0 ? `${(h.totalDurationMs / 1000).toFixed(1)}s` : "—"}</td>
                        <td>{h.summary?.totalRequests ?? "—"}</td>
                        <td>
                          <span className={`status-pill ${
                            (h.summary?.successRate ?? 0) >= 80 ? "status-completed" : "status-error"
                          }`}>
                            {h.summary?.successRate ?? 0}%
                          </span>
                        </td>
                        <td>{h.summary?.avgLatencyMs != null ? `${h.summary.avgLatencyMs.toLocaleString()}ms` : "—"}</td>
                        <td>{h.summary?.p95LatencyMs != null ? `${h.summary.p95LatencyMs.toLocaleString()}ms` : "—"}</td>
                        <td>{h.config?.concurrentUsers ?? "—"}</td>
                        <td>{h.config?.concurrentUsers === 1 ? "Single" : "Multi"}</td>
                        <td>
                          <div className="history-row-actions">
                            <button
                              className="btn-hist-action"
                              onClick={() => loadHistorySession(h.testId)}
                              title="Load into results view"
                            >View</button>
                            <button
                              className="btn-hist-action"
                              onClick={() => downloadHistoryJson(h.testId)}
                              title="Download full JSON"
                            >↓ JSON</button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
        </div>
      </div>

      {/* ── Request Detail Drawer ── */}
      {selectedRequest && (
        <div className="drawer-overlay" onClick={() => setSelectedRequest(null)}>
          <div className="request-drawer" onClick={(e) => e.stopPropagation()}>
            <div className="drawer-header">
              <div className="drawer-title">
                <span className={`status-pill status-${selectedRequest.status}`}>
                  {selectedRequest.status}
                </span>
                <h3>Request #{selectedRequest.requestIndex + 1} — Wave {selectedRequest.wave + 1}</h3>
              </div>
              <button className="drawer-close" onClick={() => setSelectedRequest(null)}>&#x2715;</button>
            </div>
            <div className="drawer-body">

              <div className="drawer-section">
                <div className="drawer-section-title">Identity</div>
                <div className="detail-row"><span className="detail-label">User UPN</span><span className="detail-value">{selectedRequest.userUpn || "—"}</span></div>
                <div className="detail-row"><span className="detail-label">Wave</span><span className="detail-value">{selectedRequest.wave + 1}</span></div>
                <div className="detail-row"><span className="detail-label">Slot in Wave</span><span className="detail-value">{selectedRequest.slotInWave}</span></div>
              </div>

              <div className="drawer-section">
                <div className="drawer-section-title">Timing</div>
                <div className="detail-row"><span className="detail-label">Start Time</span><span className="detail-value">{new Date(selectedRequest.startTimeIso).toLocaleString()}</span></div>
                <div className="detail-row"><span className="detail-label">End Time</span><span className="detail-value">{selectedRequest.endTimeIso ? new Date(selectedRequest.endTimeIso).toLocaleString() : "—"}</span></div>
                <div className="detail-row"><span className="detail-label">Latency</span><span className="detail-value">{selectedRequest.latencyMs > 0 ? `${selectedRequest.latencyMs.toLocaleString()}ms` : "—"}</span></div>
                <div className="detail-row"><span className="detail-label">HTTP Status</span><span className="detail-value">{selectedRequest.httpStatus || "—"}</span></div>
              </div>

              <div className="drawer-section">
                <div className="drawer-section-title">IDs</div>
                <div className="detail-row"><span className="detail-label">Correlation ID</span><code className="detail-code">{selectedRequest.correlationId || "—"}</code></div>
                <div className="detail-row"><span className="detail-label">Conversation ID</span><code className="detail-code">{selectedRequest.conversationId || "—"}</code></div>
                <div className="detail-row"><span className="detail-label">Response ID</span><code className="detail-code">{selectedRequest.responseId || "—"}</code></div>
              </div>

              <div className="drawer-section">
                <div className="drawer-section-title">Request Sent</div>
                <div className="detail-text-block">{selectedRequest.question}</div>
                <div className="detail-row" style={{ marginTop: "8px" }}>
                  <span className="detail-label">Prompt Chars</span>
                  <span className="detail-value">{selectedRequest.promptChars.toLocaleString()}</span>
                </div>
                <div className="detail-row">
                  <span className="detail-label">Est. Input Tokens</span>
                  <span className="detail-value">~{selectedRequest.estimatedInputTokens.toLocaleString()}</span>
                </div>
              </div>

              {selectedRequest.completion ? (
                <div className="drawer-section">
                  <div className="drawer-section-title">Completion Received</div>
                  <div className="detail-text-block detail-completion">{selectedRequest.completion}</div>
                  <div className="detail-row" style={{ marginTop: "8px" }}>
                    <span className="detail-label">Answer Length</span>
                    <span className="detail-value">{selectedRequest.answerLength.toLocaleString()} chars</span>
                  </div>
                  <div className="detail-row">
                    <span className="detail-label">Est. Output Tokens</span>
                    <span className="detail-value">~{selectedRequest.estimatedOutputTokens.toLocaleString()}</span>
                  </div>
                  <div className="detail-row">
                    <span className="detail-label">Tool Steps</span>
                    <span className="detail-value">{selectedRequest.toolSteps}</span>
                  </div>
                </div>
              ) : null}

              {selectedRequest.toolEvidenceDetail.length > 0 && (
                <div className="drawer-section">
                  <div className="drawer-section-title">
                    Tool Evidence ({selectedRequest.toolEvidenceDetail.length} step{selectedRequest.toolEvidenceDetail.length !== 1 ? "s" : ""})
                  </div>
                  {selectedRequest.toolEvidenceDetail.map((t, i) => (
                    <div key={i} className="tool-evidence-item">
                      <div className="detail-row">
                        <span className="detail-label">Step {i + 1}</span>
                        <span className={`status-pill ${t.status === "completed" ? "status-completed" : "status-error"}`}>{t.status}</span>
                      </div>
                      <div className="detail-row"><span className="detail-label">Type</span><code className="detail-code">{t.type}</code></div>
                      <div className="detail-row"><span className="detail-label">Item ID</span><code className="detail-code">{t.itemId}</code></div>
                      {t.detail && (
                        <div className="detail-text-block detail-code-block">{t.detail}</div>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {selectedRequest.error && (
                <div className="drawer-section">
                  <div className="drawer-section-title">Error</div>
                  <div className="detail-text-block detail-error">{selectedRequest.error}</div>
                </div>
              )}

            </div>
          </div>
        </div>
      )}
    </div>
  );
}
