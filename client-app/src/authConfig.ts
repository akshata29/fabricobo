import { Configuration, LogLevel } from "@azure/msal-browser";

// ═══════════════════════════════════════════════════════════════
// MSAL Configuration for Fabric OBO POC
//
// All values are loaded at runtime from the backend /api/config
// endpoint, which reads from appsettings.json. Nothing is
// hardcoded here — safe for public repositories.
// ═══════════════════════════════════════════════════════════════

// API base URL — proxied through Vite in dev
export const API_BASE_URL = "/api";

// Runtime config loaded from backend
export interface TestUser {
  label: string;
  upn: string;
  description: string;
}

export interface SpaAuthConfig {
  tenantId: string;
  spaClientId: string;
  apiClientId: string;
  testUsers: TestUser[];
}

let _config: SpaAuthConfig | null = null;

/**
 * Fetches SPA authentication configuration from the backend.
 * Must be called once before creating the MSAL instance.
 */
export async function loadAuthConfig(): Promise<SpaAuthConfig> {
  if (_config) return _config;

  const res = await fetch(`${API_BASE_URL}/config`);
  if (!res.ok) {
    throw new Error(`Failed to load auth config: ${res.status} ${res.statusText}`);
  }
  _config = await res.json();

  if (!_config!.tenantId || !_config!.spaClientId || !_config!.apiClientId) {
    throw new Error(
      "Auth config is incomplete. Ensure SpaAuth section in appsettings.json " +
      "has TenantId, SpaClientId, and ApiClientId values."
    );
  }

  return _config!;
}

/**
 * Returns the loaded auth config. Must call loadAuthConfig() first.
 */
export function getAuthConfig(): SpaAuthConfig {
  if (!_config) {
    throw new Error("Auth config not loaded. Call loadAuthConfig() first.");
  }
  return _config;
}

/**
 * Returns the MSAL configuration. Must call loadAuthConfig() first.
 */
export function getMsalConfig(): Configuration {
  if (!_config) {
    throw new Error("Auth config not loaded. Call loadAuthConfig() first.");
  }

  return {
    auth: {
      clientId: _config.spaClientId,
      authority: `https://login.microsoftonline.com/${_config.tenantId}`,
      redirectUri: window.location.origin,
      postLogoutRedirectUri: window.location.origin,
    },
    cache: {
      cacheLocation: "sessionStorage",
      storeAuthStateInCookie: false,
    },
    system: {
      loggerOptions: {
        logLevel: LogLevel.Warning,
        loggerCallback: (level, message) => {
          if (level === LogLevel.Error) console.error(message);
        },
      },
    },
  };
}

/**
 * Returns the API scope for the backend. Must call loadAuthConfig() first.
 */
export function getApiScope(): string {
  if (!_config) {
    throw new Error("Auth config not loaded. Call loadAuthConfig() first.");
  }
  return `api://${_config.apiClientId}/access_as_user`;
}

// ═══════════════════════════════════════════════════════════════
// Legacy exports for backward compatibility during migration
// These will be populated after loadAuthConfig() is called.
// ═══════════════════════════════════════════════════════════════
export let apiScope: string = "";
export let msalConfig: Configuration = {} as Configuration;

/**
 * Initializes the legacy exports after config is loaded.
 * Called by main.tsx after loadAuthConfig() completes.
 */
export function initLegacyExports(): void {
  apiScope = getApiScope();
  msalConfig = getMsalConfig();
}
