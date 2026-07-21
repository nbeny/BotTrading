import { NextResponse } from 'next/server';
import { setAuto } from '@/lib/mock/store';

export async function POST(req: Request) {
  const body = await req.json().catch(() => ({})) as { enabled?: boolean };
  const { enabled = false } = body;
  return NextResponse.json(setAuto(Boolean(enabled)));
}
