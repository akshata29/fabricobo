import { useState } from "react";
import { useMsal } from "@azure/msal-react";
import { InteractionStatus } from "@azure/msal-browser";
import { apiScope, getAuthConfig } from "./authConfig";
import Chat from "./Chat";

export default function App() {
  const { instance, accounts, inProgress } = useMsal();
  const [loginError, setLoginError] = useState<string | null>(null);

  // Derive auth state directly from accounts array — more reliable than useIsAuthenticated()
  const isAuthenticated = accounts.length > 0;
  const activeAccount = instance.getActiveAccount() ?? accounts[0] ?? null;

  const handleLogin = async (loginHint?: string) => {
    setLoginError(null);
    try {
      const result = await instance.loginPopup({
        scopes: [apiScope, "openid", "profile"],
        loginHint,
        prompt: loginHint ? "login" : undefined,
      });
      // Explicitly set the active account from the popup result
      if (result?.account) {
        instance.setActiveAccount(result.account);
      }
    } catch (e: any) {
      const msg = e?.errorMessage || e?.message || String(e);
      console.error("Login failed:", e);
      setLoginError(msg);
    }
  };

  const handleLogout = async () => {
    try {
      await instance.logoutPopup();
    } catch (e) {
      console.error("Logout failed:", e);
    }
  };

  if (inProgress !== InteractionStatus.None) {
    return (
      <div className="app">
        <div className="loading">Authenticating...</div>
      </div>
    );
  }

  return (
    <div className="app">
      <header className="header">
        <div className="header-left">
          <h1>Fabric OBO POC</h1>
          <span className="subtitle">Identity Passthrough &amp; RLS Demo</span>
        </div>
        <div className="header-right">
          {isAuthenticated && activeAccount ? (
            <div className="user-info">
              <div className="user-badge">
                <span className="user-icon">
                  {activeAccount.name?.charAt(0) || "U"}
                </span>
                <div className="user-details">
                  <span className="user-name">{activeAccount.name}</span>
                  <span className="user-upn">{activeAccount.username}</span>
                </div>
              </div>
              <button className="btn btn-outline" onClick={handleLogout}>
                Sign Out
              </button>
            </div>
          ) : null}
        </div>
      </header>

      <main className="main">
        {!isAuthenticated ? (
          <div className="login-panel">
            <div className="login-card">
              <h2>Sign In to Test</h2>
              <p className="login-desc">
                Sign in as different users to see how Row-Level Security (RLS)
                filters data based on user identity flowing through OBO.
              </p>

              {loginError && (
                <div className="login-error" style={{ color: '#ff6b6b', background: '#2a1a1a', padding: '0.75rem 1rem', borderRadius: '0.5rem', marginBottom: '1rem', fontSize: '0.875rem' }}>
                  <strong>Sign-in failed:</strong> {loginError}
                </div>
              )}

              <div className="login-buttons">
                <button
                  className="btn btn-primary btn-large"
                  onClick={() => handleLogin()}
                >
                  Sign In (Any User)
                </button>

                <div className="login-divider">
                  <span>or sign in as a test user</span>
                </div>

                {getAuthConfig().testUsers.map((user, idx) => (
                  <button
                    key={idx}
                    className={`btn btn-user-${String.fromCharCode(97 + idx)}`}
                    onClick={() => handleLogin(user.upn)}
                  >
                    <span className="btn-icon">
                      {user.label.charAt(user.label.length - 1)}
                    </span>
                    <div className="btn-label">
                      <strong>{user.label}</strong>
                      <small>{user.description}</small>
                    </div>
                  </button>
                ))}
              </div>
            </div>

            <div className="info-card">
              <h3>How it works</h3>
              <ol>
                <li>SPA acquires token via MSAL.js popup</li>
                <li>Token sent to ASP.NET Core Web API</li>
                <li>API performs OBO exchange for Foundry token</li>
                <li>Foundry calls Fabric Data Agent with user identity</li>
                <li>Fabric enforces RLS — each user sees only their data</li>
              </ol>
            </div>
          </div>
        ) : (
          <Chat />
        )}
      </main>
    </div>
  );
}
