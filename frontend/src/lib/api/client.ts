import axios, { AxiosError, type AxiosInstance } from 'axios';
import { ACCESS_TOKEN_KEY, API_BASE, CONTROL_BASE } from '@/lib/config';

export function getStoredToken(): string | null {
  if (typeof window === 'undefined') return null;
  return window.localStorage.getItem(ACCESS_TOKEN_KEY);
}

/**
 * Build an axios instance that injects the JWT bearer token from localStorage
 * and emits a global `cmi:unauthorized` event on 401 so the auth layer can log
 * the operator out and redirect to /login.
 */
function createClient(baseURL: string): AxiosInstance {
  const instance = axios.create({
    baseURL,
    timeout: 15_000,
    headers: { 'Content-Type': 'application/json' },
  });

  instance.interceptors.request.use((config) => {
    const token = getStoredToken();
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  });

  instance.interceptors.response.use(
    (res) => res,
    (error: AxiosError) => {
      if (error.response?.status === 401 && typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('cmi:unauthorized'));
      }
      return Promise.reject(error);
    },
  );

  return instance;
}

/** Read-only intelligence API (api-gateway): portfolio, market, risk, signals. */
export const api: AxiosInstance = createClient(API_BASE);

/** Control plane (control-api): auth + all `/trading/*` mutating commands. */
export const control: AxiosInstance = createClient(CONTROL_BASE);

/** Extract a human-readable message from an axios error. */
export function apiErrorMessage(error: unknown, fallback = 'Erreur inattendue'): string {
  if (axios.isAxiosError(error)) {
    const data = error.response?.data as { detail?: string; message?: string } | undefined;
    return data?.detail || data?.message || error.message || fallback;
  }
  if (error instanceof Error) return error.message;
  return fallback;
}
