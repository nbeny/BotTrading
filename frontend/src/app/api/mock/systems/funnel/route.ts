import { NextResponse } from 'next/server';
import { getFunnelStats } from '@/lib/mock/funnel';

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const window = searchParams.get('window') ?? '24h';
  return NextResponse.json(getFunnelStats(window));
}
