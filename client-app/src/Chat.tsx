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
  toolEvidence?: {
    itemId: string;
    type: string;
    status: string;
    detail?: string;
  }[];
  entitlement?: {
    upn: string;
    oid: string;
    repCode?: string;
    role?: string;
    isAuthorized: boolean;
  };
  error?: string;
}

interface Message {
  role: "user" | "assistant" | "error" | "info";
  content: string;
  timestamp: Date;
  response?: AgentResponse;
}

export default function Chat() {
  const { instance } = useMsal();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string | undefined>();
  const [showDetails, setShowDetails] = useState<number | null>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const activeAccount = instance.getActiveAccount();

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    inputRef.current?.focus();
  }, [loading]);

  const getAccessToken = async (): Promise<string> => {
    const account = instance.getActiveAccount();
    if (!account) throw new Error("No active account");

    const result = await instance.acquireTokenSilent({
      scopes: [apiScope],
      account,
    });
    return result.accessToken;
  };

  const sendMessage = async () => {
    const question = input.trim();
    if (!question || loading) return;

    setInput("");
    setMessages((prev) => [
      ...prev,
      { role: "user", content: question, timestamp: new Date() },
    ]);
    setLoading(true);

    try {
      const token = await getAccessToken();

      const response = await fetch(`${API_BASE_URL}/agent`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          question,
          conversationId,
        }),
      });

      const data: AgentResponse = await response.json();

      if (!response.ok || data.status !== "completed") {
        setMessages((prev) => [
          ...prev,
          {
            role: "error",
            content: data.error || `Request failed: ${response.status}`,
            timestamp: new Date(),
            response: data,
          },
        ]);
      } else {
        if (data.conversationId) {
          setConversationId(data.conversationId);
        }
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            content: data.assistantAnswer || "(No answer returned)",
            timestamp: new Date(),
            response: data,
          },
        ]);
      }
    } catch (err: any) {
      setMessages((prev) => [
        ...prev,
        {
          role: "error",
          content: `Error: ${err.message}`,
          timestamp: new Date(),
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const clearChat = () => {
    setMessages([]);
    setConversationId(undefined);
  };

  const quickQuestions = [
    "Give me a list of all accounts",
    "What is the total balance across all accounts?",
    "Which region has the most accounts?",
    "Show me account details for the East region",
  ];

  return (
    <div className="chat-container">
      {/* Sidebar with user info */}
      <aside className="chat-sidebar">
        <div className="sidebar-section">
          <h3>Signed In As</h3>
          <div className="sidebar-user">
            <span className="user-icon large">
              {activeAccount?.name?.charAt(0) || "U"}
            </span>
            <div>
              <div className="sidebar-user-name">{activeAccount?.name}</div>
              <div className="sidebar-user-upn">{activeAccount?.username}</div>
            </div>
          </div>
        </div>

        <div className="sidebar-section">
          <h3>Quick Questions</h3>
          <div className="quick-questions">
            {quickQuestions.map((q, i) => (
              <button
                key={i}
                className="btn btn-quick"
                onClick={() => {
                  setInput(q);
                  inputRef.current?.focus();
                }}
                disabled={loading}
              >
                {q}
              </button>
            ))}
          </div>
        </div>

        {conversationId && (
          <div className="sidebar-section">
            <h3>Session</h3>
            <div className="sidebar-meta">
              <small>
                Conversation: {conversationId.slice(0, 8)}...
              </small>
            </div>
            <button className="btn btn-outline btn-small" onClick={clearChat}>
              New Conversation
            </button>
          </div>
        )}

        <div className="sidebar-section sidebar-footer">
          <div className="architecture-hint">
            <strong>Flow:</strong> SPA → API (OBO) → Foundry → Fabric (RLS)
          </div>
        </div>
      </aside>

      {/* Chat area */}
      <div className="chat-main">
        <div className="chat-messages">
          {messages.length === 0 && (
            <div className="chat-empty">
              <div className="chat-empty-icon">💬</div>
              <h3>Ask a question about your accounts</h3>
              <p>
                Your data is filtered by Fabric Row-Level Security based on your
                identity. Try asking for a list of accounts!
              </p>
            </div>
          )}

          {messages.map((msg, i) => (
            <div key={i} className={`chat-message ${msg.role}`}>
              <div className="message-avatar">
                {msg.role === "user" ? (
                  <span>{activeAccount?.name?.charAt(0) || "U"}</span>
                ) : msg.role === "assistant" ? (
                  <span>AI</span>
                ) : (
                  <span>!</span>
                )}
              </div>
              <div className="message-body">
                <div className="message-header">
                  <span className="message-role">
                    {msg.role === "user"
                      ? activeAccount?.name || "You"
                      : msg.role === "assistant"
                      ? "Fabric OBO Agent"
                      : "Error"}
                  </span>
                  <span className="message-time">
                    {msg.timestamp.toLocaleTimeString()}
                  </span>
                </div>
                <div className="message-content">
                  {msg.role === "assistant" ? (
                    <ReactMarkdown>{msg.content}</ReactMarkdown>
                  ) : (
                    <p>{msg.content}</p>
                  )}
                </div>

                {/* Response metadata */}
                {msg.response && msg.role === "assistant" && (
                  <div className="message-meta">
                    <button
                      className="btn btn-meta"
                      onClick={() =>
                        setShowDetails(showDetails === i ? null : i)
                      }
                    >
                      {showDetails === i ? "Hide" : "Show"} Details
                    </button>

                    {msg.response.entitlement?.repCode && (
                      <span className="meta-badge rep-badge">
                        {msg.response.entitlement.repCode}
                      </span>
                    )}

                    {msg.response.toolEvidence &&
                      msg.response.toolEvidence.length > 0 && (
                        <span className="meta-badge tool-badge">
                          Fabric tool used
                        </span>
                      )}

                    {showDetails === i && (
                      <div className="message-details">
                        <table>
                          <tbody>
                            <tr>
                              <td>Status</td>
                              <td>{msg.response.status}</td>
                            </tr>
                            <tr>
                              <td>Correlation ID</td>
                              <td>
                                <code>{msg.response.correlationId}</code>
                              </td>
                            </tr>
                            <tr>
                              <td>Conversation ID</td>
                              <td>
                                <code>
                                  {msg.response.conversationId || "—"}
                                </code>
                              </td>
                            </tr>
                            <tr>
                              <td>Response ID</td>
                              <td>
                                <code>{msg.response.responseId || "—"}</code>
                              </td>
                            </tr>
                            {msg.response.entitlement && (
                              <>
                                <tr>
                                  <td>UPN</td>
                                  <td>{msg.response.entitlement.upn}</td>
                                </tr>
                                <tr>
                                  <td>RepCode</td>
                                  <td>
                                    <strong>
                                      {msg.response.entitlement.repCode ||
                                        "—"}
                                    </strong>
                                  </td>
                                </tr>
                                <tr>
                                  <td>Role</td>
                                  <td>
                                    {msg.response.entitlement.role || "—"}
                                  </td>
                                </tr>
                              </>
                            )}
                            {msg.response.toolEvidence?.map((t, j) => (
                              <tr key={j}>
                                <td>Tool #{j + 1}</td>
                                <td>
                                  {t.type} ({t.status})
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
          ))}

          {loading && (
            <div className="chat-message assistant">
              <div className="message-avatar">
                <span>AI</span>
              </div>
              <div className="message-body">
                <div className="message-content">
                  <div className="typing-indicator">
                    <span></span>
                    <span></span>
                    <span></span>
                    <span className="typing-text">
                      Querying Fabric via OBO...
                    </span>
                  </div>
                </div>
              </div>
            </div>
          )}

          <div ref={chatEndRef} />
        </div>

        {/* Input area */}
        <div className="chat-input-area">
          <div className="chat-input-wrapper">
            <input
              ref={inputRef}
              type="text"
              className="chat-input"
              placeholder="Ask about your accounts..."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={loading}
            />
            <button
              className="btn btn-send"
              onClick={sendMessage}
              disabled={loading || !input.trim()}
            >
              {loading ? "..." : "Send"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
