import type { Configuration } from '@azure/msal-browser';

/** Shape returned by the API's GET /api/auth/config endpoint. */
export interface ApiAuthConfig {
  enabled: boolean;
  clientId: string | null;
  authority: string | null;
  /** Fully-qualified API scopes, e.g. ["api://<api-client-id>/access_as_user"]. */
  scopes: string[];
}

const DISABLED: ApiAuthConfig = { enabled: false, clientId: null, authority: null, scopes: [] };

/**
 * Fetches auth configuration from the API. The API is the single source of truth,
 * so switching Entra tenants only requires updating the API config (no rebuild).
 * Any failure falls back to "disabled" so the app still loads.
 */
export async function fetchAuthConfig(): Promise<ApiAuthConfig> {
  try {
    const res = await fetch('/api/auth/config');
    if (!res.ok) return DISABLED;
    const cfg = (await res.json()) as ApiAuthConfig;
    if (!cfg.enabled || !cfg.clientId || !cfg.authority) return DISABLED;
    return cfg;
  } catch {
    return DISABLED;
  }
}

/** Builds an MSAL configuration from the API-provided auth config. */
export function buildMsalConfig(cfg: ApiAuthConfig): Configuration {
  return {
    auth: {
      clientId: cfg.clientId!,
      authority: cfg.authority!,
      redirectUri: window.location.origin,
      postLogoutRedirectUri: window.location.origin,
    },
    cache: {
      cacheLocation: 'localStorage',
    },
  };
}
