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

/**
 * Base path for the control plane (auth + all `/trading/*` writes). These are
 * served by the **control-api** service, not the read-only api-gateway, so they
 * get their own same-origin path (`/api/control`) that Next.js rewrites to
 * control-api. In mock mode everything collapses onto the built-in BFF.
 */
export const CONTROL_BASE = USE_MOCK
  ? '/api/mock'
  : process.env.NEXT_PUBLIC_CONTROL_BASE || '/api/control';

export const WS_URL = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8080/ws';

/**
 * Cloudflare Turnstile site key (public half). Empty string = captcha disabled,
 * which is the local-dev / mock default: the login form then renders without a
 * widget and control-api skips verification (it only gates when its own
 * `TURNSTILE_SECRET_KEY` is set). The two halves are configured together —
 * setting only one of them locks login out or leaves it unguarded.
 */
export const TURNSTILE_SITE_KEY = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY || '';

export const ACCESS_TOKEN_KEY = 'cmi.access_token';
export const REFRESH_TOKEN_KEY = 'cmi.refresh_token';
