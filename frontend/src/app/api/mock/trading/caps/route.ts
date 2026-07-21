import { NextResponse } from 'next/server';

export async function POST(req: Request) {
  await req.json().catch(() => ({}));
  return NextResponse.json({ ok: true });
}
