/** Runtime configuration resolved from public env vars. */

export const USE_MOCK = process.env.NEXT_PUBLIC_USE_MOCK === '1';

/**
 * Base path for REST calls. In mock mode we hit the built-in Next.js BFF
 * (`/api/mock`); otherwise the Traefik/Next rewrite proxies to the FastAPI
 * api-gateway via `/api/gateway`.
 */
export const API_BASE = USE_MOCK
  ? '/api/mock'
  : process.env.NEXT_PUBLIC_API_BASE || '/api/gateway';

export const WS_URL = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8080/ws';

export const ACCESS_TOKEN_KEY = 'cmi.access_token';
export const REFRESH_TOKEN_KEY = 'cmi.refresh_token';
