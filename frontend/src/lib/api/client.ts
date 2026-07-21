import axios, { AxiosError, type AxiosInstance } from 'axios';
import { ACCESS_TOKEN_KEY, API_BASE } from '@/lib/config';

/**
 * Shared axios instance for the terminal. Injects the JWT bearer token from
 * localStorage and emits a global `cmi:unauthorized` event on 401 so the auth
 * layer can log the operator out and redirect to /login.
 */
export const api: AxiosInstance = axios.create({
  baseURL: API_BASE,
  timeout: 15_000,
  headers: { 'Content-Type': 'application/json' },
});

export function getStoredToken(): string | null {
  if (typeof window === 'undefined') return null;
  return window.localStorage.getItem(ACCESS_TOKEN_KEY);
}

api.interceptors.request.use((config) => {
  const token = getStoredToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (res) => res,
  (error: AxiosError) => {
    if (error.response?.status === 401 && typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('cmi:unauthorized'));
    }
    return Promise.reject(error);
  },
);

/** Extract a human-readable message from an axios error. */
export function apiErrorMessage(error: unknown, fallback = 'Erreur inattendue'): string {
  if (axios.isAxiosError(error)) {
    const data = error.response?.data as { detail?: string; message?: string } | undefined;
    return data?.detail || data?.message || error.message || fallback;
  }
  if (error instanceof Error) return error.message;
  return fallback;
}
