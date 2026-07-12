import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import {
  PublicClientApplication,
  InteractionType,
  InteractionRequiredAuthError,
  EventType,
  type AccountInfo,
} from '@azure/msal-browser';
import {
  MsalProvider,
  MsalAuthenticationTemplate,
  useMsal,
  type MsalAuthenticationResult,
} from '@azure/msal-react';
import { fetchAuthConfig, buildMsalConfig } from './authConfig';
import { setTokenProvider } from '../api';

export interface AuthState {
  /** True when Azure Entra auth is active; false when disabled via config. */
  enabled: boolean;
  name: string | null;
  username: string | null;
  signOut: () => void;
}

const disabledState: AuthState = { enabled: false, name: null, username: null, signOut: () => {} };

const AuthContext = createContext<AuthState>(disabledState);

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthState {
  return useContext(AuthContext);
}

type BootState =
  | { status: 'loading' }
  | { status: 'disabled' }
  | { status: 'ready'; pca: PublicClientApplication; scopes: string[] };

/**
 * Bootstraps authentication. Fetches config from the API; when auth is disabled
 * it renders children directly. When enabled it initialises MSAL, gates the app
 * behind an interactive login, and registers a token provider so api.ts attaches
 * a bearer token to every request.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<BootState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;

    (async () => {
      const cfg = await fetchAuthConfig();

      if (!cfg.enabled) {
        setTokenProvider(null);
        if (!cancelled) setState({ status: 'disabled' });
        return;
      }

      const pca = new PublicClientApplication(buildMsalConfig(cfg));
      await pca.initialize();
      await pca.handleRedirectPromise();

      const accounts = pca.getAllAccounts();
      if (accounts.length > 0 && !pca.getActiveAccount()) {
        pca.setActiveAccount(accounts[0]);
      }

      pca.addEventCallback((event) => {
        if (event.eventType === EventType.LOGIN_SUCCESS && event.payload && 'account' in event.payload) {
          const account = (event.payload as { account?: AccountInfo }).account;
          if (account) pca.setActiveAccount(account);
        }
      });

      // Every API request runs through this to get a fresh access token.
      setTokenProvider(async () => {
        const account = pca.getActiveAccount() ?? pca.getAllAccounts()[0];
        if (!account) return null;
        try {
          const result = await pca.acquireTokenSilent({ scopes: cfg.scopes, account });
          return result.accessToken;
        } catch (err) {
          if (err instanceof InteractionRequiredAuthError) {
            await pca.acquireTokenRedirect({ scopes: cfg.scopes, account });
          }
          return null;
        }
      });

      if (!cancelled) setState({ status: 'ready', pca, scopes: cfg.scopes });
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  if (state.status === 'loading') {
    return <div className="auth-loading">Loading…</div>;
  }

  if (state.status === 'disabled') {
    return <AuthContext.Provider value={disabledState}>{children}</AuthContext.Provider>;
  }

  return (
    <MsalProvider instance={state.pca}>
      <MsalAuthenticationTemplate
        interactionType={InteractionType.Redirect}
        authenticationRequest={{ scopes: state.scopes }}
        loadingComponent={AuthLoading}
        errorComponent={AuthErrorView}
      >
        <AuthedShell>{children}</AuthedShell>
      </MsalAuthenticationTemplate>
    </MsalProvider>
  );
}

function AuthedShell({ children }: { children: ReactNode }) {
  const { instance, accounts } = useMsal();
  const account = instance.getActiveAccount() ?? accounts[0] ?? null;

  const value = useMemo<AuthState>(
    () => ({
      enabled: true,
      name: account?.name ?? null,
      username: account?.username ?? null,
      signOut: () => {
        void instance.logoutRedirect();
      },
    }),
    [instance, account?.name, account?.username],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

function AuthLoading() {
  return <div className="auth-loading">Signing in…</div>;
}

function AuthErrorView(result: MsalAuthenticationResult) {
  return (
    <div className="auth-loading">
      Authentication error: {result.error?.errorMessage ?? 'Sign-in failed.'}
    </div>
  );
}
