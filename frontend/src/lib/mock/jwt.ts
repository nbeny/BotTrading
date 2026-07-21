import { createHmac } from 'node:crypto';

const DEFAULT_SECRET = 'mock-bff-secret-do-not-use-in-production';

function base64url(input: string | Buffer): string {
  const buf = typeof input === 'string' ? Buffer.from(input, 'utf8') : input;
  return buf.toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
}

/**
 * Sign a minimal HS256 JWT. The frontend only decodes it via jwt-decode
 * (no signature verification), so the signature just needs to be well-formed.
 * Adds `iat` (now) and `exp` (now + 12 h) automatically.
 */
export function signJwt(claims: Record<string, unknown>, secret = DEFAULT_SECRET): string {
  const now = Math.floor(Date.now() / 1000);
  const payload: Record<string, unknown> = {
    iat: now,
    exp: now + 12 * 3600,
    ...claims,
  };

  const header = base64url(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const body = base64url(JSON.stringify(payload));
  const signingInput = `${header}.${body}`;

  const sig = base64url(
    createHmac('sha256', secret).update(signingInput).digest(),
  );

  return `${signingInput}.${sig}`;
}
