// MCP Chat  mirrors Chat.tsx but calls /api/agent/v2
// Simplified: no concurrent test panel (that lives in McpLoadTest now).
import { useState, useRef, useEffect } from "react";
import { useMsal } from "@azure/msal-react";
import { apiScope, API_BASE_URL } from "./authConfig";
import ReactMarkdown from "react-markdown";

interface AgentResponse {
  status: string;
  correlationId: string;
  conversationId?: string;
  responseId?: string;
  assistantAnswer?: string;
  toolEvidence?: { itemId: string; type: string; status: string; detail?: string }[];
  entitlement?: { upn: string; oid: string; repCode?: string; role?: string; isAuthorized: boolean };
  error?: string;
}

interface Message {
  role: "user" | "assistant" | "error";
  content: string;
  timestamp: Date;
  latencyMs?: number;
  response?: AgentResponse;
}

const QUICK_QUESTIONS = [
  "Give me a list of all accounts",
  "What is the total balance across all accounts?",
  "Which region has the most accounts?",
  "Show me account details for the East region",
];

export default function McpChat() {
  const { instance } = useMsal();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string | undefined>();
  const [showDetails, setShowDetails] = useState<number | null>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const activeAccount = instance.getActiveAccount();

  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);
  useEffect(() => { inputRef.current?.focus(); }, [loading]);

  const getAccessToken = async () => {
    const account = instance.getActiveAccount();
    if (!account) throw new Error("No active account");
    const result = await instance.acquireTokenSilent({ scopes: [apiScope], account });
    return result.accessToken;
  };

  const sendMessage = async () => {
    const question = input.trim();
    if (!question || loading) return;
    setInput("");
    setMessages(prev => [...prev, { role: "user", content: question, timestamp: new Date() }]);
    setLoading(true);
    const t0 = Date.now();
    try {
      const token = await getAccessToken();
      const res = await fetch(`${API_BASE_URL}/agent/v2`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ question, conversationId }),
      });
      const data: AgentResponse = await res.json();
      const latencyMs = Date.now() - t0;
      if (data.status !== "completed") {
        setMessages(prev => [...prev, { role: "error", content: data.error || `Status: ${data.status}`, timestamp: new Date(), latencyMs, response: data }]);
      } else {
        if (data.conversationId) setConversationId(data.conversationId);
        setMessages(prev => [...prev, { role: "assistant", content: data.assistantAnswer || "(no answer)", timestamp: new Date(), latencyMs, response: data }]);
      }
    } catch (err: any) {
      setMessages(prev => [...prev, { role: "error", content: `Error: ${err.message}`, timestamp: new Date() }]);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  };
  const clearChat = () => { setMessages([]); setConversationId(undefined); };

  return (
    <div className="chat-container">
      <aside className="chat-sidebar">
        <div className="sidebar-section">
          <h3>MCP Route</h3>
          <div className="architecture-hint">
            <strong>Endpoint:</strong> /api/agent/v2<br />
            <strong>Auth:</strong> OBO x2 (Foundry + Fabric)<br />
            <strong>Tool:</strong> Inline MCP  /mcp  Fabric<br />
            <strong>Theory:</strong> new thread per request
          </div>
          <div className="mcp-arch-theory" style={{ marginTop: "8px" }}>
            vs. Chat tab: built-in Fabric tool via Foundry
          </div>
        </div>

        <div className="sidebar-section">
          <h3>Quick Questions</h3>
          <div className="quick-questions">
            {QUICK_QUESTIONS.map((q, i) => (
              <button key={i} className="btn btn-quick" onClick={() => { setInput(q); inputRef.current?.focus(); }} disabled={loading}>
                {q}
              </button>
            ))}
          </div>
        </div>

        {conversationId && (
          <div className="sidebar-section">
            <h3>Session</h3>
            <div className="sidebar-meta">
              <small>Conversation: {conversationId.slice(0, 8)}</small>
            </div>
            <button className="btn btn-outline btn-small" onClick={clearChat}>New Conversation</button>
          </div>
        )}

        <div className="sidebar-section sidebar-footer">
          <div className="architecture-hint">
            Use <strong>MCP Load Test</strong> tab to test same-user concurrency.
          </div>
        </div>
      </aside>

      <div className="chat-main">
        <div className="mcp-path-banner">
          <span className="mcp-path-badge">MCP Path</span>
          <span className="mcp-path-label">Browser  API (OBO x2)  Foundry  /mcp  Fabric (new thread/request)</span>
          <span className="mcp-path-vs">vs. Chat: built-in Fabric tool</span>
        </div>

        <div className="chat-messages">
          {messages.length === 0 && (
            <div className="chat-empty">
              <div className="chat-empty-icon"></div>
              <h3>MCP Chat Path</h3>
              <p>Ask a question using the MCP route.<br />Use <strong>MCP Load Test</strong> to test same-user concurrency.</p>
            </div>
          )}

          {messages.map((msg, i) => (
            <div key={i} className={`chat-message ${msg.role}`}>
              <div className="message-avatar">
                {msg.role === "user" ? (
                  <span style={{ background: "var(--accent)" }}>{activeAccount?.name?.charAt(0) || "U"}</span>
                ) : msg.role === "assistant" ? (
                  <span className="mcp-avatar">MCP</span>
                ) : (
                  <span style={{ background: "var(--error)" }}>!</span>
                )}
              </div>
              <div className="message-body">
                <div className="message-header">
                  <span className="message-role">
                    {msg.role === "user" ? (activeAccount?.name || "You") : msg.role === "assistant" ? "Fabric MCP Agent" : "Error"}
                  </span>
                  <span className="message-time">{msg.timestamp.toLocaleTimeString()}</span>
                  {msg.latencyMs != null && <span className="mcp-latency-badge">{(msg.latencyMs / 1000).toFixed(1)}s</span>}
                </div>
                <div className="message-content">
                  {msg.role === "assistant" ? <ReactMarkdown>{msg.content}</ReactMarkdown> : <p>{msg.content}</p>}
                </div>
                {msg.response && msg.role === "assistant" && (
                  <div className="message-meta">
                    <button className="btn btn-meta" onClick={() => setShowDetails(showDetails === i ? null : i)}>
                      {showDetails === i ? "Hide" : "Show"} Details
                    </button>
                    {msg.response.toolEvidence && msg.response.toolEvidence.length > 0 && (
                      <span className="meta-badge mcp-tool-badge">MCP tool used</span>
                    )}
                    {msg.response.entitlement?.repCode && (
                      <span className="meta-badge rep-badge">{msg.response.entitlement.repCode}</span>
                    )}
                    {showDetails === i && (
                      <div className="message-details">
                        <table>
                          <tbody>
                            <tr><td>Status</td><td>{msg.response.status}</td></tr>
                            <tr><td>Correlation ID</td><td><code>{msg.response.correlationId}</code></td></tr>
                            <tr><td>Conversation ID</td><td><code>{msg.response.conversationId || ""}</code></td></tr>
                            <tr><td>Latency</td><td>{msg.latencyMs != null ? `${(msg.latencyMs / 1000).toFixed(2)}s` : ""}</td></tr>
                            {msg.response.entitlement && (
                              <>
                                <tr><td>UPN</td><td>{msg.response.entitlement.upn}</td></tr>
                                <tr><td>RepCode</td><td><strong>{msg.response.entitlement.repCode || ""}</strong></td></tr>
                              </>
                            )}
                            {msg.response.toolEvidence?.map((t, j) => (
                              <tr key={j}><td>Tool #{j + 1}</td><td>{t.type} ({t.status})</td></tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          ))}

          {loading && (
            <div className="chat-message assistant">
              <div className="message-avatar"><span className="mcp-avatar">MCP</span></div>
              <div className="message-body">
                <div className="message-content">
                  <div className="typing-indicator">
                    <span /><span /><span />
                    <span className="typing-text">Foundry  /mcp  Fabric</span>
                  </div>
                </div>
              </div>
            </div>
          )}
          <div ref={chatEndRef} />
        </div>

        <div className="chat-input-area">
          <div className="chat-input-wrapper">
            <input
              ref={inputRef}
              type="text"
              className="chat-input mcp-chat-input"
              placeholder="Ask a question via MCP path (/api/agent/v2)"
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={loading}
            />
            <button className="btn mcp-btn-send" onClick={sendMessage} disabled={loading || !input.trim()}>
              {loading ? "" : "Send"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
