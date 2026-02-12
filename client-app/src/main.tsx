import React from "react";
import ReactDOM from "react-dom/client";
import { PublicClientApplication, EventType } from "@azure/msal-browser";
import { MsalProvider } from "@azure/msal-react";
import { loadAuthConfig, initLegacyExports, getMsalConfig } from "./authConfig";
import App from "./App";
import "./index.css";

// Load auth config from backend, then initialize MSAL and render.
loadAuthConfig()
  .then(() => {
    // Populate legacy exports (apiScope, msalConfig) used by other components
    initLegacyExports();

    const msalInstance = new PublicClientApplication(getMsalConfig());

    // MSAL Browser v3 requires initialize() before any API calls.
    return msalInstance.initialize().then(() => {
      // Handle any redirect / popup response that landed on this page
      msalInstance.handleRedirectPromise().catch(console.error);

      // Set the first cached account as active (survives page refresh)
      const accounts = msalInstance.getAllAccounts();
      if (accounts.length > 0) {
        msalInstance.setActiveAccount(accounts[0]);
      }

      // Keep active account in sync after every successful login
      msalInstance.addEventCallback((event) => {
        if (event.eventType === EventType.LOGIN_SUCCESS && event.payload) {
          const payload = event.payload as { account?: { username: string } };
          if (payload.account) {
            msalInstance.setActiveAccount(payload.account as any);
          }
        }
      });

      ReactDOM.createRoot(document.getElementById("root")!).render(
        <React.StrictMode>
          <MsalProvider instance={msalInstance}>
            <App />
          </MsalProvider>
        </React.StrictMode>
      );
    });
  })
  .catch((err) => {
    // Show a user-friendly error if config fails to load
    console.error("Failed to load auth configuration:", err);
    const root = document.getElementById("root");
    if (root) {
      root.innerHTML = `
        <div style="display:flex;align-items:center;justify-content:center;height:100vh;font-family:system-ui;color:#ff6b6b;padding:2rem;text-align:center;">
          <div>
            <h2>Configuration Error</h2>
            <p>Failed to load authentication configuration from the backend.</p>
            <p style="color:#888;font-size:0.875rem;">Ensure the API is running and <code>SpaAuth</code> is configured in appsettings.json.</p>
            <p style="color:#666;font-size:0.75rem;margin-top:1rem;">${err.message}</p>
          </div>
        </div>`;
    }
  });
