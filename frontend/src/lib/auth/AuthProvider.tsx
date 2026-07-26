'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { jwtDecode } from 'jwt-decode';
import { control } from '@/lib/api/client';
import { ACCESS_TOKEN_KEY, REFRESH_TOKEN_KEY } from '@/lib/config';
import { hasPermission } from './rbac';
import type { AuthState, AuthTokens, JwtClaims, Permission, User } from './types';

interface AuthContextValue extends AuthState {
  login: (email: string, password: string, turnstileToken?: string | null) => Promise<void>;
  logout: () => void;
  can: (permission: Permission) => boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function userFromToken(token: string): User | null {
  try {
    const c = jwtDecode<JwtClaims>(token);
    if (!c.exp || c.exp * 1000 < Date.now()) return null;
    return {
      id: c.sub,
      email: c.email ?? c.sub,
      name: c.name ?? c.email ?? 'Opérateur',
      role: c.role ?? 'viewer',
    };
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [state, setState] = useState<AuthState>({
    user: null,
    accessToken: null,
    status: 'loading',
  });

  // Hydrate from a persisted token on mount.
  useEffect(() => {
    const token = window.localStorage.getItem(ACCESS_TOKEN_KEY);
    const user = token ? userFromToken(token) : null;
    if (token && user) {
      setState({ user, accessToken: token, status: 'authenticated' });
    } else {
      window.localStorage.removeItem(ACCESS_TOKEN_KEY);
      setState({ user: null, accessToken: null, status: 'unauthenticated' });
    }
  }, []);

  const logout = useCallback(() => {
    window.localStorage.removeItem(ACCESS_TOKEN_KEY);
    window.localStorage.removeItem(REFRESH_TOKEN_KEY);
    setState({ user: null, accessToken: null, status: 'unauthenticated' });
    router.replace('/login');
  }, [router]);

  // React to 401s emitted by the axios interceptor.
  useEffect(() => {
    const handler = () => logout();
    window.addEventListener('cmi:unauthorized', handler);
    return () => window.removeEventListener('cmi:unauthorized', handler);
  }, [logout]);

  const login = useCallback(
    async (email: string, password: string, turnstileToken?: string | null) => {
      // control-api expects `username`; the mock BFF reads `email`. Send both so
      // the same call works against either backend. `turnstile_token` is the
      // captcha proof — omitted when the widget is disabled, in which case
      // control-api (having no secret key either) doesn't ask for it.
      const { data } = await control.post<AuthTokens>('/auth/login', {
        username: email,
        email,
        password,
        turnstile_token: turnstileToken ?? undefined,
      });
      const user = userFromToken(data.access_token);
      if (!user) throw new Error('Token invalide reçu du serveur');
      window.localStorage.setItem(ACCESS_TOKEN_KEY, data.access_token);
      if (data.refresh_token) {
        window.localStorage.setItem(REFRESH_TOKEN_KEY, data.refresh_token);
      }
      setState({ user, accessToken: data.access_token, status: 'authenticated' });
    },
    [],
  );

  const can = useCallback(
    (permission: Permission) => hasPermission(state.user?.role, permission),
    [state.user?.role],
  );

  const value = useMemo<AuthContextValue>(
    () => ({ ...state, login, logout, can }),
    [state, login, logout, can],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
