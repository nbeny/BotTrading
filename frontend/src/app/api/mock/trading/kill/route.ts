import { NextResponse } from 'next/server';
import { setKill } from '@/lib/mock/store';

export async function POST(req: Request) {
  const body = await req.json().catch(() => ({})) as { enabled?: boolean };
  const { enabled = false } = body;
  setKill(Boolean(enabled));
  return NextResponse.json({ ok: true });
}
