import { NextResponse } from 'next/server';
import { recentEvents } from '@/lib/mock/sim';

// Incremental event feed. Client passes `?since=<cursor>` (0 on first load to
// receive backfilled history) and gets back new events plus the next cursor.
export async function GET(req: Request) {
  const p = new URL(req.url).searchParams;
  const since = p.get('since') ? Number(p.get('since')) : 0;
  const limit = p.get('limit') ? Number(p.get('limit')) : 150;
  return NextResponse.json(recentEvents(Number.isFinite(since) ? since : 0, limit));
}
