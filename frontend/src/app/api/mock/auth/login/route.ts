import { NextResponse } from 'next/server';
import { signJwt } from '@/lib/mock/jwt';
import { uid } from '@/lib/mock/universe';
import type { Role } from '@/lib/auth/types';

export async function POST(req: Request) {
  // `turnstile_token` may be present (the form sends it whenever a site key is
  // configured) but the mock BFF never verifies it: verification needs the
  // secret key, which lives only in control-api. Demo mode is not a security
  // boundary — it hands out fake tokens to fake accounts.
  const body = await req.json().catch(() => ({})) as { email?: string; password?: string };
  const { email = '', password = '' } = body;

  if (!email || !password) {
    return NextResponse.json({ detail: 'Missing credentials' }, { status: 401 });
  }

  let role: Role = 'viewer';
  let name = 'Demo Viewer';
  if (email.includes('admin')) {
    role = 'admin';
    name = 'Admin User';
  } else if (email.includes('operator')) {
    role = 'operator';
    name = 'Operator User';
  }

  const sub = uid('usr');
  const access_token = signJwt({ sub, email, name, role });

  return NextResponse.json({ access_token, token_type: 'bearer' });
}
