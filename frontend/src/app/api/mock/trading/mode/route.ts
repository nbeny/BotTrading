import { NextResponse } from 'next/server';
import { setMode } from '@/lib/mock/store';
import type { TradingMode } from '@/lib/types/domain';

export async function POST(req: Request) {
  const body = await req.json().catch(() => ({})) as { mode?: TradingMode };
  const { mode = 'dry_run' } = body;
  return NextResponse.json(setMode(mode));
}
